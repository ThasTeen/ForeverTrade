"""Tests for src.database module under Ponytail doctrine (real functional execution, no paper tests)."""

import datetime
import json
import os
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
    upsert_item_market_metrics,
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


def test_load_recipes_era_full_graph_and_gear(memory_db):
    """Verify loading full Classic Era 1.15 recipe graph across all 7 professions and sub-50 gear."""
    load_catalog(memory_db)
    total_loaded = load_recipes(memory_db, recipes_path="data/recipes_era_1_15.json")
    assert total_loaded >= 980

    cursor = memory_db.cursor()

    # 1. Verify all 7 professions are present
    cursor.execute("SELECT profession, count(*) FROM recipes GROUP BY profession;")
    prof_counts = dict(cursor.fetchall())
    expected_professions = {
        "Blacksmithing",
        "Leatherworking",
        "Tailoring",
        "Engineering",
        "Alchemy",
        "Cooking",
        "Smelting",
    }
    assert set(prof_counts.keys()) == expected_professions

    # 2. Recipe counts per profession: >= 10 in every profession, smelting has 12, major gear professions > 150
    for prof in expected_professions:
        assert prof_counts[prof] >= 10, f"{prof} count too low: {prof_counts[prof]}"

    assert prof_counts["Smelting"] == 12
    assert prof_counts["Blacksmithing"] > 150
    assert prof_counts["Leatherworking"] > 150
    assert prof_counts["Tailoring"] > 150
    assert prof_counts["Engineering"] >= 100
    assert prof_counts["Alchemy"] >= 100
    assert prof_counts["Cooking"] >= 50

    # 3. Verify key sub-50 gear and smelting recipes exist
    # Blacksmithing: Bronze Cuirass (crafted 2866), Green Iron Hauberk (recipe 3508 / crafted 3844)
    cursor.execute("SELECT recipe_id, name, crafted_item_id, profession FROM recipes WHERE crafted_item_id = 2866;")
    bronze_cuirass = cursor.fetchone()
    assert bronze_cuirass is not None
    assert bronze_cuirass["profession"] == "Blacksmithing"
    assert "Bronze Cuirass" in bronze_cuirass["name"]

    cursor.execute("SELECT recipe_id, name, crafted_item_id, profession FROM recipes WHERE name = 'Green Iron Hauberk';")
    green_iron = cursor.fetchone()
    assert green_iron is not None
    assert green_iron["profession"] == "Blacksmithing"
    assert green_iron["crafted_item_id"] == 3844

    # Leatherworking: Nightscape Shoulders (crafted 8192), Turtle Scale Breastplate (recipe 10511, crafted 8189), Turtle Scale Leggings (crafted 8185)
    cursor.execute("SELECT recipe_id, name, crafted_item_id, profession FROM recipes WHERE crafted_item_id = 8192;")
    nightscape = cursor.fetchone()
    assert nightscape is not None
    assert nightscape["profession"] == "Leatherworking"
    assert nightscape["name"] == "Nightscape Shoulders"

    cursor.execute("SELECT recipe_id, name, crafted_item_id, profession FROM recipes WHERE name = 'Turtle Scale Breastplate';")
    turtle_chest = cursor.fetchone()
    assert turtle_chest is not None
    assert turtle_chest["profession"] == "Leatherworking"

    cursor.execute("SELECT recipe_id, name, crafted_item_id, profession FROM recipes WHERE crafted_item_id = 8185;")
    turtle_legs = cursor.fetchone()
    assert turtle_legs is not None
    assert turtle_legs["profession"] == "Leatherworking"

    # Tailoring: Shadoweave Robe (crafted 10004), Red Mageweave Pants (recipe 12060, crafted 10009), Mooncloth (crafted 14342)
    cursor.execute("SELECT recipe_id, name, crafted_item_id, profession FROM recipes WHERE crafted_item_id = 10004;")
    shadoweave = cursor.fetchone()
    assert shadoweave is not None
    assert shadoweave["profession"] == "Tailoring"
    assert shadoweave["name"] == "Shadoweave Robe"

    cursor.execute("SELECT recipe_id, name, crafted_item_id, profession FROM recipes WHERE name = 'Red Mageweave Pants';")
    red_mageweave = cursor.fetchone()
    assert red_mageweave is not None
    assert red_mageweave["profession"] == "Tailoring"

    cursor.execute("SELECT recipe_id, name, crafted_item_id, profession FROM recipes WHERE crafted_item_id = 14342;")
    mooncloth = cursor.fetchone()
    assert mooncloth is not None
    assert mooncloth["profession"] == "Tailoring"
    assert mooncloth["name"] == "Mooncloth"

    # Engineering: Green Tinted Goggles (recipe 3956, crafted 4385) and Craftsman's Monocle (crafted 4393)
    cursor.execute("SELECT recipe_id, name, crafted_item_id, profession FROM recipes WHERE name = 'Green Tinted Goggles';")
    goggles = cursor.fetchone()
    assert goggles is not None
    assert goggles["profession"] == "Engineering"

    cursor.execute("SELECT recipe_id, name, crafted_item_id, profession FROM recipes WHERE crafted_item_id = 4393;")
    monocle = cursor.fetchone()
    assert monocle is not None
    assert monocle["profession"] == "Engineering"

    # Smelting: Smelt Bronze (crafted 2841, yield 2.0), Smelt Iron (crafted 3575), Smelt Mithril (crafted 3860)
    cursor.execute("SELECT recipe_id, name, crafted_item_id, yield, profession FROM recipes WHERE recipe_id = 2659;")
    smelt_bronze = cursor.fetchone()
    assert smelt_bronze is not None
    assert smelt_bronze["name"] == "Smelt Bronze"
    assert smelt_bronze["profession"] == "Smelting"
    assert smelt_bronze["crafted_item_id"] == 2841
    assert smelt_bronze["yield"] == 2.0

    cursor.execute("SELECT recipe_id, name, crafted_item_id, profession FROM recipes WHERE crafted_item_id = 3575 AND profession = 'Smelting';")
    smelt_iron = cursor.fetchone()
    assert smelt_iron is not None
    assert smelt_iron["name"] == "Smelt Iron"

    cursor.execute("SELECT recipe_id, name, crafted_item_id, profession FROM recipes WHERE crafted_item_id = 3860 AND profession = 'Smelting';")
    smelt_mithril = cursor.fetchone()
    assert smelt_mithril is not None
    assert smelt_mithril["name"] == "Smelt Mithril"

    # 4. Reagents linkage and Foreign Key integrity
    cursor.execute("""
        SELECT r.recipe_id, count(rr.reagent_item_id) as rc
        FROM recipes r
        LEFT JOIN recipe_reagents rr ON r.recipe_id = rr.recipe_id
        GROUP BY r.recipe_id
        HAVING rc == 0;
    """)
    recipes_without_reagents = cursor.fetchall()
    assert len(recipes_without_reagents) == 0

    # Key gear recipes specific reagent verification
    # Rough Bronze Cuirass (2670) requires Bronze Bar (2841) count=7
    cursor.execute("SELECT reagent_item_id, count FROM recipe_reagents WHERE recipe_id = 2670;")
    cuirass_reagents = dict(cursor.fetchall())
    assert cuirass_reagents == {2841: 7}

    # Nightscape Shoulders (10516) requires Thick Leather (4304) x8, Mageweave Cloth (4338) x6, Silken Thread (4291) x3
    cursor.execute("SELECT reagent_item_id, count FROM recipe_reagents WHERE recipe_id = 10516;")
    shoulders_reagents = dict(cursor.fetchall())
    assert shoulders_reagents == {4304: 8, 4338: 6, 4291: 3}

    # Smelt Bronze (2659) requires Copper Bar (2840) x1, Tin Bar (3576) x1
    cursor.execute("SELECT reagent_item_id, count FROM recipe_reagents WHERE recipe_id = 2659;")
    bronze_reagents = dict(cursor.fetchall())
    assert bronze_reagents == {2840: 1, 3576: 1}

    # SQLite PRAGMA foreign_key_check must return 0 violations across the entire database
    fk_violations = memory_db.execute("PRAGMA foreign_key_check;").fetchall()
    assert len(fk_violations) == 0


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


def test_r45_catalog_items_and_vendor_prices(populated_db):
    """Verify R45: items 2450, 2452, 2313, 2316 have expected catalog data and vendor prices."""
    # 1. Briarthorn (2450)
    item_2450 = get_item(populated_db, 2450)
    assert item_2450 is not None
    assert item_2450["name"] == "Briarthorn"
    assert item_2450["vendor_sell_price"] == 50

    # 2. Swiftthistle (2452)
    item_2452 = get_item(populated_db, 2452)
    assert item_2452 is not None
    assert item_2452["name"] == "Swiftthistle"
    assert item_2452["vendor_sell_price"] == 40

    # 3. Medium Armor Kit (2313)
    item_2313 = get_item(populated_db, 2313)
    assert item_2313 is not None
    assert item_2313["name"] == "Medium Armor Kit"
    assert item_2313["vendor_sell_price"] == 60
    assert item_2313["vendor_buy_price"] == 250

    # 4. Toughened Leather Gloves (2316)
    item_2316 = get_item(populated_db, 2316)
    assert item_2316 is not None
    assert item_2316["name"] == "Toughened Leather Gloves"
    assert item_2316["vendor_sell_price"] == 137


def test_r45_localization_mappings(populated_db):
    """Verify R45: en and ru localization for items 2450, 2452, 2313, 2316."""
    expected_l10n = {
        2450: ("Briarthorn", "Остротерн"),
        2452: ("Swiftthistle", "Свистоцвет"),
        2313: ("Medium Armor Kit", "Накладки из средней кожи"),
        2316: ("Toughened Leather Gloves", "Плотные кожаные перчатки"),
    }

    for item_id, (en_name, ru_name) in expected_l10n.items():
        # Check ITEM_LOCALIZATION constant dictionary
        assert item_id in ITEM_LOCALIZATION
        assert ITEM_LOCALIZATION[item_id]["en"] == en_name
        assert ITEM_LOCALIZATION[item_id]["ru"] == ru_name

        # Check get_item_name with DB connection
        assert get_item_name(populated_db, item_id, lang="en") == en_name
        assert get_item_name(populated_db, item_id, lang="ru") == ru_name

        # Check get_item_name with None connection (pure dictionary fallback)
        assert get_item_name(None, item_id, lang="en") == en_name
        assert get_item_name(None, item_id, lang="ru") == ru_name

        # Check loaded items table in SQLite
        row = get_item(populated_db, item_id)
        assert row is not None
        assert row["name"] == en_name
        assert row["name_ru"] == ru_name


def test_r45_recipes_and_reagents(populated_db):
    """Verify R45: recipe 3004 crafts item 2313; recipes 4004 & 4005 use reagent 2450 (Briarthorn), not 2452."""
    cursor = populated_db.cursor()

    # Recipe 3004: Medium Armor Kit -> crafts 2313
    cursor.execute("SELECT recipe_id, crafted_item_id FROM recipes WHERE recipe_id = 3004;")
    r_3004 = cursor.fetchone()
    assert r_3004 is not None
    assert r_3004["crafted_item_id"] == 2313

    # Recipe 4004: Lesser Healing Potion -> uses reagent 2450 (Briarthorn), NOT 2452 (Swiftthistle)
    cursor.execute("SELECT reagent_item_id FROM recipe_reagents WHERE recipe_id = 4004;")
    reagents_4004 = {row["reagent_item_id"] for row in cursor.fetchall()}
    assert 2450 in reagents_4004, "Recipe 4004 must use Briarthorn (2450)"
    assert 2452 not in reagents_4004, "Recipe 4004 must NOT use Swiftthistle (2452)"

    # Recipe 4005: Healing Potion -> uses reagent 2450 (Briarthorn), NOT 2452 (Swiftthistle)
    cursor.execute("SELECT reagent_item_id FROM recipe_reagents WHERE recipe_id = 4005;")
    reagents_4005 = {row["reagent_item_id"] for row in cursor.fetchall()}
    assert 2450 in reagents_4005, "Recipe 4005 must use Briarthorn (2450)"
    assert 2452 not in reagents_4005, "Recipe 4005 must NOT use Swiftthistle (2452)"


def test_vendor_copper_columns_and_bidirectional_sync(memory_db, tmp_path):
    """Verify vendor_buy_copper and vendor_sell_copper fields in load_catalog and items table.

    Asserts column aliases work and bidirectional synchronization holds via triggers.
    """
    # 1. Verify schema: vendor_sell_copper and vendor_buy_copper exist in items table
    cursor = memory_db.cursor()
    cursor.execute("PRAGMA table_info(items);")
    cols = {row["name"] for row in cursor.fetchall()}
    assert "vendor_sell_copper" in cols
    assert "vendor_buy_copper" in cols
    assert "vendor_sell_price" in cols
    assert "vendor_buy_price" in cols

    # 2. Verify load_catalog populates vendor_*_copper and matches vendor_*_price
    total_loaded = load_catalog(memory_db)
    assert total_loaded > 0

    # Copper Bar (2840): vendor_sell = 10, vendor_buy = 0
    row_copper = memory_db.execute(
        "SELECT vendor_sell_price, vendor_sell_copper, vendor_buy_price, vendor_buy_copper FROM items WHERE item_id = 2840"
    ).fetchone()
    assert row_copper is not None
    assert row_copper["vendor_sell_price"] == 10
    assert row_copper["vendor_sell_copper"] == 10
    assert row_copper["vendor_buy_price"] == 0
    assert row_copper["vendor_buy_copper"] == 0

    # get_item returns both aliases consistently
    item_copper = get_item(memory_db, 2840)
    assert item_copper["vendor_sell_copper"] == item_copper["vendor_sell_price"] == 10
    assert item_copper["vendor_buy_copper"] == item_copper["vendor_buy_price"] == 0

    # 3. Custom catalog files: one using *_copper, another using *_price
    custom_era = tmp_path / "custom_era.json"
    custom_era.write_text(json.dumps([
        {
            "item_id": 9101,
            "name": "Copper Only Item",
            "vendor_sell_copper": 150,
            "vendor_buy_copper": 500,
        },
        {
            "item_id": 9102,
            "name": "Price Only Item",
            "vendor_sell_price": 250,
            "vendor_buy_price": 600,
        }
    ]), encoding="utf-8")

    db2 = init_db(":memory:")
    try:
        load_catalog(db2, era_catalog_path=str(custom_era), overrides_path=str(tmp_path / "empty_overrides.json"))

        row_9101 = db2.execute(
            "SELECT vendor_sell_price, vendor_sell_copper, vendor_buy_price, vendor_buy_copper FROM items WHERE item_id = 9101"
        ).fetchone()
        assert row_9101["vendor_sell_copper"] == 150
        assert row_9101["vendor_sell_price"] == 150
        assert row_9101["vendor_buy_copper"] == 500
        assert row_9101["vendor_buy_price"] == 500

        row_9102 = db2.execute(
            "SELECT vendor_sell_price, vendor_sell_copper, vendor_buy_price, vendor_buy_copper FROM items WHERE item_id = 9102"
        ).fetchone()
        assert row_9102["vendor_sell_price"] == 250
        assert row_9102["vendor_sell_copper"] == 250
        assert row_9102["vendor_buy_price"] == 600
        assert row_9102["vendor_buy_copper"] == 600
    finally:
        db2.close()

    # 4. Direct INSERT triggers bidirectional synchronization
    with memory_db:
        # Insert specifying only vendor_*_price
        memory_db.execute(
            "INSERT INTO items (item_id, name, vendor_sell_price, vendor_buy_price) VALUES (?, ?, ?, ?)",
            (9201, "Sync Test Price", 120, 340)
        )
        # Insert specifying only vendor_*_copper
        memory_db.execute(
            "INSERT INTO items (item_id, name, vendor_sell_copper, vendor_buy_copper) VALUES (?, ?, ?, ?)",
            (9202, "Sync Test Copper", 220, 440)
        )

    r_9201 = memory_db.execute(
        "SELECT vendor_sell_price, vendor_sell_copper, vendor_buy_price, vendor_buy_copper FROM items WHERE item_id = 9201"
    ).fetchone()
    assert r_9201["vendor_sell_copper"] == 120
    assert r_9201["vendor_buy_copper"] == 340

    r_9202 = memory_db.execute(
        "SELECT vendor_sell_price, vendor_sell_copper, vendor_buy_price, vendor_buy_copper FROM items WHERE item_id = 9202"
    ).fetchone()
    assert r_9202["vendor_sell_price"] == 220
    assert r_9202["vendor_buy_price"] == 440

    # 5. Direct UPDATE triggers bidirectional synchronization
    with memory_db:
        # Update vendor_sell_price -> syncs vendor_sell_copper
        memory_db.execute("UPDATE items SET vendor_sell_price = 555 WHERE item_id = 9201")
    r = memory_db.execute("SELECT vendor_sell_price, vendor_sell_copper FROM items WHERE item_id = 9201").fetchone()
    assert r["vendor_sell_price"] == 555
    assert r["vendor_sell_copper"] == 555

    with memory_db:
        # Update vendor_sell_copper -> syncs vendor_sell_price
        memory_db.execute("UPDATE items SET vendor_sell_copper = 666 WHERE item_id = 9201")
    r = memory_db.execute("SELECT vendor_sell_price, vendor_sell_copper FROM items WHERE item_id = 9201").fetchone()
    assert r["vendor_sell_price"] == 666
    assert r["vendor_sell_copper"] == 666

    with memory_db:
        # Update vendor_buy_price -> syncs vendor_buy_copper
        memory_db.execute("UPDATE items SET vendor_buy_price = 777 WHERE item_id = 9201")
    r = memory_db.execute("SELECT vendor_buy_price, vendor_buy_copper FROM items WHERE item_id = 9201").fetchone()
    assert r["vendor_buy_price"] == 777
    assert r["vendor_buy_copper"] == 777

    with memory_db:
        # Update vendor_buy_copper -> syncs vendor_buy_price
        memory_db.execute("UPDATE items SET vendor_buy_copper = 888 WHERE item_id = 9201")
    r = memory_db.execute("SELECT vendor_buy_price, vendor_buy_copper FROM items WHERE item_id = 9201").fetchone()
    assert r["vendor_buy_price"] == 888
    assert r["vendor_buy_copper"] == 888

    # Verify get_item helper returns updated synchronized values
    item_9201 = get_item(memory_db, 9201)
    assert item_9201["vendor_sell_price"] == 666
    assert item_9201["vendor_sell_copper"] == 666
    assert item_9201["vendor_buy_price"] == 888
    assert item_9201["vendor_buy_copper"] == 888


def test_load_catalog_forever_catalog_priority_and_overrides(memory_db, tmp_path):
    """Verify load_catalog loads forever_catalog_path with priority over era base while preserving overrides."""
    # 1. Era base catalog
    era_data = [
        {
            "item_id": 10001,
            "name": "Era Iron Ore",
            "name_ru": "Эра железная руда",
            "vendor_sell_copper": 20,
            "vendor_buy_copper": 80,
            "quality": 1,
        },
        {
            "item_id": 10002,
            "name": "Era Mithril Bar",
            "name_ru": "Эра мифриловый слиток",
            "vendor_sell_copper": 100,
            "vendor_buy_copper": 400,
            "quality": 1,
        },
        {
            "item_id": 10003,
            "name": "Era Truesilver Bar",
            "name_ru": "Эра слиток истинного серебра",
            "vendor_sell_copper": 250,
            "vendor_buy_copper": 1000,
            "quality": 2,
        },
    ]
    era_path = tmp_path / "items_era.json"
    era_path.write_text(json.dumps(era_data), encoding="utf-8")

    # 2. Forever catalog: updates 10002, modifies 10003, adds 10004
    forever_data = [
        {
            "item_id": 10002,
            "name": "Forever Mithril Bar",
            "name_ru": "Мифриловый слиток Forever",
            "vendor_sell_copper": 250,
            "vendor_buy_copper": 600,
            "quality": 2,
        },
        {
            "item_id": 10003,
            "name": "Forever Truesilver Bar",
            "name_ru": "Слиток истинного серебра Forever",
            "vendor_sell_copper": 300,
            "vendor_buy_copper": 1200,
            "quality": 2,
        },
        {
            "item_id": 10004,
            "name": "Forever Special Potion",
            "name_ru": "Особое зелье Forever",
            "vendor_sell_copper": 500,
            "vendor_buy_copper": 2000,
            "quality": 3,
        },
    ]
    forever_path = tmp_path / "items_forever.json"
    forever_path.write_text(json.dumps(forever_data), encoding="utf-8")

    # 3. Server overrides: overrides 10003 with highest priority
    overrides_data = [
        {
            "item_id": 10003,
            "name": "Override Truesilver Bar",
            "name_ru": "Переопределенный слиток",
            "vendor_sell_copper": 450,
            "vendor_buy_copper": 1800,
            "quality": 4,
            "reward_copper": 7500,
            "category": "waylaid_crate",
            "is_override": 1,
        }
    ]
    overrides_path = tmp_path / "forever_overrides.json"
    overrides_path.write_text(json.dumps(overrides_data), encoding="utf-8")

    count = load_catalog(
        memory_db,
        era_catalog_path=str(era_path),
        overrides_path=str(overrides_path),
        forever_catalog_path=str(forever_path),
    )
    assert count == 4

    # Item 10001: Loaded from Era catalog
    item_10001 = get_item(memory_db, 10001)
    assert item_10001 is not None
    assert item_10001["name"] == "Era Iron Ore"
    assert item_10001["name_ru"] == "Эра железная руда"
    assert item_10001["vendor_sell_copper"] == 20
    assert item_10001["vendor_buy_copper"] == 80
    assert item_10001["is_override"] == 0

    # Item 10002: Forever catalog takes priority over Era base
    item_10002 = get_item(memory_db, 10002)
    assert item_10002 is not None
    assert item_10002["name"] == "Forever Mithril Bar"
    assert item_10002["name_ru"] == "Мифриловый слиток Forever"
    assert item_10002["vendor_sell_copper"] == 250
    assert item_10002["vendor_buy_copper"] == 600
    assert item_10002["quality"] == 2
    assert item_10002["is_override"] == 0

    # Item 10003: Overrides take precedence over both Forever and Era base
    item_10003 = get_item(memory_db, 10003)
    assert item_10003 is not None
    assert item_10003["name"] == "Override Truesilver Bar"
    assert item_10003["name_ru"] == "Переопределенный слиток"
    assert item_10003["vendor_sell_copper"] == 450
    assert item_10003["vendor_buy_copper"] == 1800
    assert item_10003["quality"] == 4
    assert item_10003["is_override"] == 1
    assert item_10003["reward_copper"] == 7500
    assert item_10003["category"] == "waylaid_crate"

    # Item 10004: Exclusive new item in Forever catalog
    item_10004 = get_item(memory_db, 10004)
    assert item_10004 is not None
    assert item_10004["name"] == "Forever Special Potion"
    assert item_10004["name_ru"] == "Особое зелье Forever"
    assert item_10004["vendor_sell_copper"] == 500
    assert item_10004["vendor_buy_copper"] == 2000
    assert item_10004["quality"] == 3
    assert item_10004["is_override"] == 0


def test_load_catalog_default_items_forever_path(memory_db, tmp_path):
    """Verify load_catalog forever_catalog_path loads when present without modifying data/ (Audit §10)."""
    fake_forever_path = tmp_path / "items_forever.json"
    test_item_id = 99997

    sample_forever = [
        {
            "item_id": test_item_id,
            "name": "Default Path Forever Item",
            "name_ru": "Предмет из data/items_forever.json",
            "vendor_sell_copper": 345,
            "vendor_buy_copper": 678,
            "quality": 2,
        }
    ]
    with open(fake_forever_path, "w", encoding="utf-8") as f:
        json.dump(sample_forever, f)

    load_catalog(memory_db, forever_catalog_path=str(fake_forever_path))
    item = get_item(memory_db, test_item_id)
    assert item is not None
    assert item["name"] == "Default Path Forever Item"
    assert item["name_ru"] == "Предмет из data/items_forever.json"
    assert item["vendor_sell_copper"] == 345
    assert item["vendor_buy_copper"] == 678


def test_item_market_metrics_table_and_upsert(memory_db):
    """Verify item_market_metrics DDL, index idx_metrics_realm_item, and upsert logic (Ticket 04)."""
    # 1. Verify table exists in SQLite catalog
    cursor = memory_db.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = {row[0] for row in cursor.fetchall()}
    assert "item_market_metrics" in tables

    # Verify table schema columns
    cursor.execute("PRAGMA table_info(item_market_metrics);")
    cols = {row["name"] for row in cursor.fetchall()}
    assert {
        "item_id", "realm", "median_price", "mad", "sigma_mad",
        "ewma_price", "liquidity_score", "sample_count", "updated_at"
    }.issubset(cols)

    # 2. Verify index exists and indexes (realm, item_id)
    cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='item_market_metrics';")
    indexes = {row[0] for row in cursor.fetchall()}
    assert "idx_metrics_realm_item" in indexes

    cursor.execute("PRAGMA index_info(idx_metrics_realm_item);")
    idx_cols = [row["name"] for row in cursor.fetchall()]
    assert idx_cols == ["realm", "item_id"]

    # 3. Test upsert with new metrics (including foreign key auto-handling when item_id not in items)
    # Items 77701 and 77702 do NOT exist in items yet
    test_metrics = [
        {
            "item_id": 77701,
            "realm": "Everlook",
            "median_price": 150,
            "mad": 10,
            "sigma_mad": 15,
            "ewma_price": 155,
            "liquidity_score": 0.75,
            "sample_count": 20,
        },
        {
            "item_id": 77702,
            "realm": "Everlook",
            "median_price": 300,
            "mad": 25,
            "sigma_mad": 37,
            "ewma_price": 290,
            "liquidity_score": 0.45,
            "sample_count": 8,
        },
    ]

    inserted_count = upsert_item_market_metrics(memory_db, test_metrics)
    assert inserted_count == 2

    # Verify items table was automatically populated to satisfy foreign keys
    item_row = memory_db.execute("SELECT name FROM items WHERE item_id = 77701;").fetchone()
    assert item_row is not None
    assert item_row["name"] == "Item 77701"

    # Verify rows in item_market_metrics
    cursor.execute(
        "SELECT item_id, realm, median_price, mad, sigma_mad, ewma_price, liquidity_score, sample_count "
        "FROM item_market_metrics ORDER BY item_id ASC;"
    )
    rows = cursor.fetchall()
    assert len(rows) == 2
    assert rows[0]["item_id"] == 77701
    assert rows[0]["realm"] == "Everlook"
    assert rows[0]["median_price"] == 150
    assert rows[0]["mad"] == 10
    assert rows[0]["sigma_mad"] == 15
    assert rows[0]["ewma_price"] == 155
    assert rows[0]["liquidity_score"] == pytest.approx(0.75)
    assert rows[0]["sample_count"] == 20

    assert rows[1]["item_id"] == 77702
    assert rows[1]["realm"] == "Everlook"
    assert rows[1]["median_price"] == 300
    assert rows[1]["mad"] == 25
    assert rows[1]["sigma_mad"] == 37
    assert rows[1]["ewma_price"] == 290
    assert rows[1]["liquidity_score"] == pytest.approx(0.45)
    assert rows[1]["sample_count"] == 8

    # 4. Verify update on conflict (item_id, realm)
    updated_metrics = [
        {
            "item_id": 77701,
            "realm": "Everlook",
            "median_price": 180,
            "mad": 12,
            "sigma_mad": 18,
            "ewma_price": 178,
            "liquidity_score": 0.80,
            "sample_count": 25,
        }
    ]
    upserted_count = upsert_item_market_metrics(memory_db, updated_metrics)
    assert upserted_count == 1

    # Check total rows count is still 2 (no duplicate rows)
    total_count = memory_db.execute("SELECT COUNT(*) FROM item_market_metrics;").fetchone()[0]
    assert total_count == 2

    # Check updated values
    cursor.execute(
        "SELECT median_price, mad, sigma_mad, ewma_price, liquidity_score, sample_count "
        "FROM item_market_metrics WHERE item_id = 77701 AND realm = 'Everlook';"
    )
    updated_row = cursor.fetchone()
    assert updated_row["median_price"] == 180
    assert updated_row["mad"] == 12
    assert updated_row["sigma_mad"] == 18
    assert updated_row["ewma_price"] == 178
    assert updated_row["liquidity_score"] == pytest.approx(0.80)
    assert updated_row["sample_count"] == 25

    # 5. Verify empty list handling
    assert upsert_item_market_metrics(memory_db, []) == 0




