"""Tests for src.database module under Ponytail doctrine (real functional execution, no paper tests)."""

import datetime
import sqlite3
import time
import pytest

from src.database import (
    ITEM_LOCALIZATION,
    clean_demo_data,
    get_connection,
    get_item,
    get_item_name,
    init_db,
    init_schema,
    load_catalog,
    load_recipes,
    prune_snapshots,
)


@pytest.fixture
def memory_db():
    """Provide a fresh in-memory SQLite database connection."""
    conn = init_db(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def populated_db(memory_db):
    """Provide an in-memory database with catalog and recipes loaded."""
    load_catalog(memory_db)
    load_recipes(memory_db)
    return memory_db


def test_init_db_creates_tables_and_indexes(memory_db, tmp_path):
    """Verify DDL execution: creation of tables, columns, foreign keys, and composite indexes."""
    # Check foreign keys enabled
    fk_status = memory_db.execute("PRAGMA foreign_keys;").fetchone()[0]
    assert fk_status == 1

    # Check required tables exist
    cursor = memory_db.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = {row[0] for row in cursor.fetchall()}
    expected_tables = {"items", "price_snapshots", "recipes", "recipe_reagents"}
    assert expected_tables.issubset(tables)

    # Check items columns
    cursor.execute("PRAGMA table_info(items);")
    items_cols = {row["name"] for row in cursor.fetchall()}
    assert {
        "item_id", "name", "quality", "item_class", "item_subclass",
        "vendor_sell_price", "vendor_buy_price", "target_item_id",
        "target_item_count", "reward_copper", "category", "is_override"
    }.issubset(items_cols)

    # Check price_snapshots columns
    cursor.execute("PRAGMA table_info(price_snapshots);")
    snapshots_cols = {row["name"] for row in cursor.fetchall()}
    assert {"id", "item_id", "realm", "price", "timestamp"}.issubset(snapshots_cols)

    # Check composite indexes on price_snapshots
    cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='price_snapshots';")
    indexes = {row[0] for row in cursor.fetchall()}
    assert "idx_snapshots_item_time" in indexes
    assert "idx_snapshots_realm_time" in indexes
    assert "idx_snapshots_timestamp" in indexes

    cursor.execute("PRAGMA index_info(idx_snapshots_item_time);")
    item_time_cols = [row["name"] for row in cursor.fetchall()]
    assert item_time_cols == ["item_id", "timestamp"]

    cursor.execute("PRAGMA index_info(idx_snapshots_realm_time);")
    realm_time_cols = [row["name"] for row in cursor.fetchall()]
    assert realm_time_cols == ["realm", "timestamp"]

    cursor.execute("PRAGMA index_info(idx_snapshots_timestamp);")
    ts_cols = [row["name"] for row in cursor.fetchall()]
    assert ts_cols == ["timestamp"]

    # Check file-based creation with parent directory and WAL mode
    file_db_path = tmp_path / "nested" / "market.db"
    file_conn = init_db(str(file_db_path))
    assert file_db_path.exists()
    journal_mode = file_conn.execute("PRAGMA journal_mode;").fetchone()[0]
    assert journal_mode.lower() == "wal"
    file_conn.close()


def test_init_db_foreign_key_enforcement(memory_db):
    """Verify that SQLite foreign key constraints are actively enforced."""
    # Attempting to insert a snapshot for a non-existent item must fail
    with pytest.raises(sqlite3.IntegrityError):
        memory_db.execute(
            "INSERT INTO price_snapshots (item_id, realm, price, timestamp) VALUES (?, ?, ?, ?)",
            (999999, "Everlook", 500, int(time.time()))
        )


def test_get_connection(tmp_path):
    """Verify get_connection factory sets Row factory, foreign keys, WAL, synchronous, and busy_timeout."""
    # In-memory connection
    mem_conn = get_connection(":memory:")
    try:
        assert mem_conn.row_factory == sqlite3.Row
        fk = mem_conn.execute("PRAGMA foreign_keys;").fetchone()[0]
        assert fk == 1
    finally:
        mem_conn.close()

    # File-based connection with parent directory auto-creation
    db_file = tmp_path / "factory_dir" / "market.db"
    file_conn = get_connection(str(db_file))
    try:
        assert db_file.exists()
        assert file_conn.row_factory == sqlite3.Row
        fk = file_conn.execute("PRAGMA foreign_keys;").fetchone()[0]
        assert fk == 1
        journal_mode = file_conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert journal_mode.lower() == "wal"
        synchronous = file_conn.execute("PRAGMA synchronous;").fetchone()[0]
        assert synchronous == 1
        busy_timeout = file_conn.execute("PRAGMA busy_timeout;").fetchone()[0]
        assert busy_timeout == 30000
    finally:
        file_conn.close()


def test_load_catalog_era_and_overrides(memory_db):
    """Verify ingestion of Classic Era catalog and application of Forever overrides."""
    total_loaded = load_catalog(memory_db)
    assert total_loaded > 0

    # Verify Classic Era item
    copper_bar = get_item(memory_db, 2840)
    assert copper_bar is not None
    assert copper_bar["name"] == "Copper Bar"
    assert copper_bar["vendor_sell_price"] == 10
    assert copper_bar["vendor_buy_price"] == 0
    assert copper_bar["is_override"] == 0

    # Verify Waylaid Crate override item
    crate = get_item(memory_db, 211501)
    assert crate is not None
    assert "Waylaid Supplies: Copper Bars" in crate["name"]
    assert crate["target_item_id"] == 2840
    assert crate["target_item_count"] == 20
    assert crate["reward_copper"] == 500
    assert crate["category"] == "waylaid_crate"
    assert crate["is_override"] == 1

    # Verify categories present in overrides (Waylaid crates, bivouac/camps, warrants)
    cursor = memory_db.cursor()
    cursor.execute("SELECT DISTINCT category FROM items WHERE is_override = 1;")
    categories = {row[0] for row in cursor.fetchall()}
    assert {"waylaid_crate", "bivouac", "craftsman_writ"}.issubset(categories)

    # Verify bivouac item exists with override flag
    cursor.execute("SELECT item_id, name, is_override FROM items WHERE category = 'bivouac' LIMIT 1;")
    bivouac = cursor.fetchone()
    assert bivouac is not None
    assert bivouac["is_override"] == 1

    # Verify craftsman writ exists with override flag and target linkage
    cursor.execute("SELECT item_id, name, target_item_id, target_item_count, reward_copper FROM items WHERE category = 'craftsman_writ' LIMIT 1;")
    writ = cursor.fetchone()
    assert writ is not None
    assert writ["target_item_id"] is not None
    assert writ["target_item_count"] > 0
    assert writ["reward_copper"] > 0

    # Verify idempotence: repeating load_catalog shouldn't duplicate rows
    count_second = load_catalog(memory_db)
    assert count_second == total_loaded


def test_load_catalog_missing_files(memory_db, tmp_path):
    """Verify that load_catalog handles missing files without unhandled exceptions."""
    result = load_catalog(
        memory_db,
        era_catalog_path=str(tmp_path / "nonexistent_era.json"),
        overrides_path=str(tmp_path / "nonexistent_overrides.json")
    )
    assert result == 0


def test_load_catalog_upsert_preserves_foreign_keys(memory_db):
    """Verify load_catalog uses UPSERT so re-inserting items does not violate price_snapshots foreign keys."""
    # Initial load of catalog
    load_catalog(memory_db)

    # Insert a child snapshot referencing an existing item (Copper Bar: 2840)
    now = int(time.time())
    memory_db.execute(
        "INSERT INTO price_snapshots (item_id, realm, price, timestamp) VALUES (?, ?, ?, ?)",
        (2840, "Everlook", 150, now)
    )

    # Re-run load_catalog: must NOT raise sqlite3.IntegrityError from FK violation
    count = load_catalog(memory_db)
    assert count > 0

    # Ensure price snapshot is preserved
    snapshot = memory_db.execute(
        "SELECT item_id, price FROM price_snapshots WHERE item_id = 2840"
    ).fetchone()
    assert snapshot is not None
    assert snapshot["price"] == 150


def test_load_recipes_five_professions(memory_db):
    """Verify loading recipe graph for 5 professions (Engineering, Tailoring, Leatherworking, Alchemy, Cooking)."""
    # Load catalog first so crafted_item_id and reagent_item_id satisfy foreign keys
    load_catalog(memory_db)
    count = load_recipes(memory_db)
    assert count > 0

    # Verify all 5 professions are present and well-populated
    cursor = memory_db.cursor()
    cursor.execute("SELECT profession, count(*) FROM recipes GROUP BY profession;")
    prof_counts = dict(cursor.fetchall())
    expected_professions = {"Engineering", "Tailoring", "Leatherworking", "Alchemy", "Cooking"}
    assert expected_professions.issubset(prof_counts.keys())
    for prof in expected_professions:
        assert prof_counts[prof] >= 5, f"{prof} has fewer recipes than expected: {prof_counts[prof]}"

    # Verify yield metrics on all recipes
    cursor.execute("SELECT recipe_id, yield, yield_min, yield_max FROM recipes;")
    recipes = cursor.fetchall()
    assert len(recipes) == count
    for r in recipes:
        assert r["yield"] > 0
        assert r["yield_min"] >= 1
        assert r["yield_max"] >= r["yield_min"]

    # Verify recipe_reagents linkages and foreign keys
    cursor.execute("SELECT recipe_id, reagent_item_id, count, quantity FROM recipe_reagents;")
    reagents = cursor.fetchall()
    assert len(reagents) > 0

    # Ensure every recipe has at least one reagent linked
    recipe_ids_in_recipes = {r["recipe_id"] for r in recipes}
    recipe_ids_in_reagents = {row["recipe_id"] for row in reagents}
    assert recipe_ids_in_reagents == recipe_ids_in_recipes

    for reg in reagents:
        assert reg["count"] > 0
        assert reg["quantity"] > 0


def test_prune_snapshots(memory_db):
    """Verify removal of price snapshots older than max_days while keeping recent snapshots."""
    # Insert dummy items to satisfy foreign key constraints
    memory_db.execute("INSERT INTO items (item_id, name) VALUES (1, 'Test Item 1')")
    memory_db.execute("INSERT INTO items (item_id, name) VALUES (2, 'Test Item 2')")

    now = int(time.time())
    ts_now = now
    ts_10d = now - (10 * 86400)
    ts_29d = now - (29 * 86400)
    ts_31d = now - (31 * 86400)
    ts_60d = now - (60 * 86400)

    # Insert snapshots with integer epoch timestamps
    snapshots = [
        (1, "Everlook", 100, ts_now),
        (1, "Everlook", 110, ts_10d),
        (1, "Everlook", 120, ts_29d),
        (1, "Everlook", 130, ts_31d),
        (1, "Everlook", 140, ts_60d),
    ]
    memory_db.executemany(
        "INSERT INTO price_snapshots (item_id, realm, price, timestamp) VALUES (?, ?, ?, ?)",
        snapshots
    )

    # Insert snapshots with ISO text timestamps (one old, one recent)
    old_iso = "2020-01-01 12:00:00"
    recent_iso = datetime.datetime.fromtimestamp(now - (5 * 86400), tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    memory_db.execute(
        "INSERT INTO price_snapshots (item_id, realm, price, timestamp) VALUES (?, ?, ?, ?)",
        (2, "Everlook", 200, old_iso)
    )
    memory_db.execute(
        "INSERT INTO price_snapshots (item_id, realm, price, timestamp) VALUES (?, ?, ?, ?)",
        (2, "Everlook", 210, recent_iso)
    )

    # Total snapshots before pruning: 7
    total_before = memory_db.execute("SELECT count(*) FROM price_snapshots;").fetchone()[0]
    assert total_before == 7

    # Prune snapshots older than 30 days
    pruned = prune_snapshots(memory_db, max_days=30)
    # Expected deleted: ts_31d, ts_60d, and old_iso -> 3 snapshots
    assert pruned == 3

    # Total remaining: 4 (ts_now, ts_10d, ts_29d, recent_iso)
    total_after = memory_db.execute("SELECT count(*) FROM price_snapshots;").fetchone()[0]
    assert total_after == 4

    # Running prune again when nothing is older should delete 0
    pruned_again = prune_snapshots(memory_db, max_days=30)
    assert pruned_again == 0


def test_prune_snapshots_batching(memory_db):
    """Verify prune_snapshots deletes records in batches using batch_size parameter."""
    memory_db.execute("INSERT INTO items (item_id, name) VALUES (1, 'Batch Test Item')")

    now = int(time.time())
    old_ts = now - (40 * 86400)
    fresh_ts = now - (5 * 86400)

    # Insert 5 old snapshots and 3 fresh snapshots
    for _ in range(5):
        memory_db.execute(
            "INSERT INTO price_snapshots (item_id, realm, price, timestamp) VALUES (?, ?, ?, ?)",
            (1, "Everlook", 100, old_ts)
        )
    for _ in range(3):
        memory_db.execute(
            "INSERT INTO price_snapshots (item_id, realm, price, timestamp) VALUES (?, ?, ?, ?)",
            (1, "Everlook", 200, fresh_ts)
        )

    # Prune with batch_size=2 to test multiple batch loop iterations
    deleted = prune_snapshots(memory_db, max_days=30, batch_size=2)
    assert deleted == 5

    remaining = memory_db.execute("SELECT price FROM price_snapshots").fetchall()
    assert len(remaining) == 3
    for row in remaining:
        assert row["price"] == 200


def test_get_item(populated_db):
    """Verify retrieving existing and non-existing items."""
    # Existing item from era catalog
    item = get_item(populated_db, 2840)
    assert item is not None
    assert isinstance(item, dict)
    assert item["item_id"] == 2840
    assert item["name"] == "Copper Bar"
    assert item["vendor_sell_price"] == 10

    # Non-existing item returns None
    missing = get_item(populated_db, 99999999)
    assert missing is None


def test_clean_demo_data(memory_db):
    """Verify clean_demo_data removes item 9999 and all its price snapshots."""
    # Insert regular item and demo item 9999
    memory_db.execute("INSERT INTO items (item_id, name) VALUES (1, 'Real Item')")
    memory_db.execute("INSERT INTO items (item_id, name) VALUES (9999, 'Demo Item 9999')")

    now = int(time.time())
    memory_db.execute(
        "INSERT INTO price_snapshots (item_id, realm, price, timestamp) VALUES (?, ?, ?, ?)",
        (1, "Everlook", 50, now)
    )
    memory_db.execute(
        "INSERT INTO price_snapshots (item_id, realm, price, timestamp) VALUES (?, ?, ?, ?)",
        (9999, "Everlook", 100, now)
    )
    memory_db.execute(
        "INSERT INTO price_snapshots (item_id, realm, price, timestamp) VALUES (?, ?, ?, ?)",
        (9999, "Everlook", 120, now + 60)
    )

    # Pre-condition check
    item_9999 = memory_db.execute("SELECT count(*) FROM items WHERE item_id = 9999").fetchone()[0]
    snaps_9999 = memory_db.execute("SELECT count(*) FROM price_snapshots WHERE item_id = 9999").fetchone()[0]
    assert item_9999 == 1
    assert snaps_9999 == 2

    # Execute clean_demo_data
    clean_demo_data(memory_db)

    # Verify item 9999 and its snapshots are completely removed
    assert memory_db.execute("SELECT count(*) FROM items WHERE item_id = 9999").fetchone()[0] == 0
    assert memory_db.execute("SELECT count(*) FROM price_snapshots WHERE item_id = 9999").fetchone()[0] == 0

    # Verify regular item 1 and its snapshots remain untouched
    assert memory_db.execute("SELECT count(*) FROM items WHERE item_id = 1").fetchone()[0] == 1
    assert memory_db.execute("SELECT count(*) FROM price_snapshots WHERE item_id = 1").fetchone()[0] == 1

    # Verify idempotence: repeating clean_demo_data causes no errors
    clean_demo_data(memory_db)


def test_item_localization_and_get_item_name(memory_db):
    """Verify ITEM_LOCALIZATION mapping and get_item_name resolution for en/ru and placeholder handling."""
    # Check dictionary localization entries
    assert 3857 in ITEM_LOCALIZATION
    assert ITEM_LOCALIZATION[3857]["en"] == "Coal"
    assert ITEM_LOCALIZATION[3857]["ru"] == "Уголь"

    assert 2840 in ITEM_LOCALIZATION
    assert ITEM_LOCALIZATION[2840]["en"] == "Copper Bar"
    assert ITEM_LOCALIZATION[2840]["ru"] == "Медный слиток"

    # 1. Direct dictionary fallback (conn=None)
    assert get_item_name(None, 3857, lang="en") == "Coal"
    assert get_item_name(None, 3857, lang="ru") == "Уголь"
    assert get_item_name(None, 2840, lang="en") == "Copper Bar"
    assert get_item_name(None, 2840, lang="ru") == "Медный слиток"

    # 2. Database with placeholder / empty names should resolve from ITEM_LOCALIZATION
    memory_db.execute("INSERT OR REPLACE INTO items (item_id, name, name_ru) VALUES (3857, 'Item 3857', NULL)")
    assert get_item_name(memory_db, 3857, lang="en") == "Coal"
    assert get_item_name(memory_db, 3857, lang="ru") == "Уголь"

    memory_db.execute("UPDATE items SET name = '', name_ru = '' WHERE item_id = 3857")
    assert get_item_name(memory_db, 3857, lang="en") == "Coal"
    assert get_item_name(memory_db, 3857, lang="ru") == "Уголь"

    memory_db.execute("UPDATE items SET name = 'Item #3857', name_ru = 'Item 3857' WHERE item_id = 3857")
    assert get_item_name(memory_db, 3857, lang="en") == "Coal"
    assert get_item_name(memory_db, 3857, lang="ru") == "Уголь"

    # 3. Database with valid populated names
    memory_db.execute("UPDATE items SET name = 'Coal', name_ru = 'Уголь' WHERE item_id = 3857")
    assert get_item_name(memory_db, 3857, lang="en") == "Coal"
    assert get_item_name(memory_db, 3857, lang="ru") == "Уголь"

    # 4. Unknown item fallback
    assert get_item_name(memory_db, 9999999, lang="en") == "Item 9999999"
    assert get_item_name(None, 9999999, lang="ru") == "Item 9999999"


def test_items_schema_and_name_ru_migration(memory_db):
    """Verify name_ru column creation, migration on existing table, catalog loading, and get_item."""
    # 1. Column exists in newly initialized schema
    cursor = memory_db.cursor()
    cursor.execute("PRAGMA table_info(items);")
    cols = {row["name"] for row in cursor.fetchall()}
    assert "name_ru" in cols

    # 2. Migration: table created without name_ru is altered cleanly by init_schema
    legacy_conn = sqlite3.connect(":memory:")
    legacy_conn.execute("CREATE TABLE items (item_id INTEGER PRIMARY KEY, name TEXT NOT NULL);")
    init_schema(legacy_conn)
    legacy_cur = legacy_conn.cursor()
    legacy_cur.execute("PRAGMA table_info(items);")
    migrated_cols = {row[1] for row in legacy_cur.fetchall()}
    assert "name_ru" in migrated_cols
    legacy_conn.close()

    # 3. load_catalog correctly populates name_ru
    load_catalog(memory_db)

    item_coal = get_item(memory_db, 3857)
    assert item_coal is not None
    assert item_coal["name"] == "Coal"
    assert item_coal["name_ru"] == "Уголь"

    item_copper = get_item(memory_db, 2840)
    assert item_copper is not None
    assert item_copper["name"] == "Copper Bar"
    assert item_copper["name_ru"] == "Медный слиток"
