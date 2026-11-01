"""Tests for File Watcher and ETL Pipeline (Ticket 02, R05, R06, R07, R08, R48)."""

import hashlib
import logging
import os
import sqlite3
import threading
import time
from unittest.mock import MagicMock

import pytest
from watchdog.events import FileModifiedEvent

from src.database import init_db
from src.etl import (
    AuctionatorEventHandler,
    ingest_price_snapshot,
    normalize_item_id,
    parse_auctionator_data,
    start_watcher,
)


@pytest.fixture
def memory_db():
    """In-memory SQLite database initialized with schema."""
    conn = init_db(":memory:")
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def temp_db(tmp_path):
    """Temporary SQLite database on disk."""
    db_file = str(tmp_path / "test_market.db")
    conn = init_db(db_file)
    try:
        yield db_file, conn
    finally:
        conn.close()


class TestNormalizeItemId:
    """Tests for item ID normalization from various raw signatures (R07)."""

    def test_normalize_colon_signature(self):
        assert normalize_item_id("2589:0:0:0:0") == 2589
        assert normalize_item_id("12345:1:2:3") == 12345

    def test_normalize_with_item_prefix(self):
        assert normalize_item_id("item:2589:0:0:0") == 2589
        assert normalize_item_id("item:774") == 774

    def test_normalize_with_gear_random_suffix_prefix(self):
        """Verify gear with random properties signature gr:<item_id>:<suffix> (R32)."""
        assert normalize_item_id("gr:1234:567") == 1234
        assert normalize_item_id("gr:5555:0") == 5555
        assert normalize_item_id("gr:9999") == 9999

    def test_normalize_simple_string_and_int(self):
        assert normalize_item_id("2589") == 2589
        assert normalize_item_id(2589) == 2589
        assert normalize_item_id("1234:0:0") == 1234
        assert normalize_item_id("1234") == 1234

    def test_normalize_invalid_and_edge_cases(self):
        assert normalize_item_id(None) is None
        assert normalize_item_id(True) is None
        assert normalize_item_id(False) is None
        assert normalize_item_id("") is None
        assert normalize_item_id("   ") is None
        assert normalize_item_id("unknown:invalid") is None
        assert normalize_item_id(0) is None
        assert normalize_item_id("0") is None
        assert normalize_item_id("0:0:0:0") is None
        assert normalize_item_id(-10) is None
        assert normalize_item_id("-10:0:0") is None


class TestParseAuctionatorData:
    """Tests for safe Lua parsing and fallback mechanism (R06)."""

    def test_parse_valid_lua_table(self, tmp_path):
        lua_content = (
            'AUCTIONATOR_PRICE_DATABASE = {\n'
            '  ["Everlook"] = {\n'
            '    ["2589:0:0:0:0"] = { ["mr"] = 1500, ["id"] = "2589" },\n'
            '    ["1205:0:0:0:0"] = { ["mr"] = 320 }\n'
            '  }\n'
            '}\n'
        )
        lua_file = tmp_path / "Auctionator.lua"
        lua_file.write_text(lua_content, encoding="utf-8")

        data = parse_auctionator_data(str(lua_file))
        assert isinstance(data, dict)
        assert "Everlook" in data
        assert "2589:0:0:0:0" in data["Everlook"]
        assert data["Everlook"]["2589:0:0:0:0"]["mr"] == 1500

    def test_parse_corrupt_primary_with_valid_bak_fallback(self, tmp_path):
        primary_file = tmp_path / "Auctionator.lua"
        primary_file.write_text("AUCTIONATOR_PRICE_DATABASE = { broken lua syntax [[[", encoding="utf-8")

        bak_content = (
            'AUCTIONATOR_PRICE_DATABASE = {\n'
            '  ["Everlook"] = {\n'
            '    ["2589"] = { ["mr"] = 999 }\n'
            '  }\n'
            '}\n'
        )
        bak_file = tmp_path / "Auctionator.lua.bak"
        bak_file.write_text(bak_content, encoding="utf-8")

        data = parse_auctionator_data(str(primary_file))
        assert isinstance(data, dict)
        assert "Everlook" in data
        assert data["Everlook"]["2589"]["mr"] == 999

    def test_parse_fallback_alternate_bak_extension(self, tmp_path):
        """Verify fallback to Auctionator.bak when Auctionator.lua.bak is absent."""
        primary_file = tmp_path / "Auctionator.lua"
        primary_file.write_text("corrupted", encoding="utf-8")

        alt_bak_content = (
            'AUCTIONATOR_PRICE_DATABASE = {\n'
            '  ["Everlook"] = {\n'
            '    ["555"] = { ["mr"] = 333 }\n'
            '  }\n'
            '}\n'
        )
        alt_bak_file = tmp_path / "Auctionator.bak"
        alt_bak_file.write_text(alt_bak_content, encoding="utf-8")

        data = parse_auctionator_data(str(primary_file))
        assert isinstance(data, dict)
        assert data["Everlook"]["555"]["mr"] == 333

    def test_parse_missing_primary_with_valid_bak_fallback(self, tmp_path):
        primary_file = tmp_path / "Auctionator.lua"
        # primary_file does not exist on disk

        bak_content = (
            'AUCTIONATOR_PRICE_DATABASE = {\n'
            '  ["Everlook"] = {\n'
            '    ["774"] = { ["mr"] = 450 }\n'
            '  }\n'
            '}\n'
        )
        bak_file = tmp_path / "Auctionator.lua.bak"
        bak_file.write_text(bak_content, encoding="utf-8")

        data = parse_auctionator_data(str(primary_file))
        assert isinstance(data, dict)
        assert "Everlook" in data
        assert data["Everlook"]["774"]["mr"] == 450

    def test_parse_both_files_missing_raises_error(self, tmp_path):
        missing_file = tmp_path / "NonExistent.lua"
        with pytest.raises((FileNotFoundError, ValueError, Exception)) as exc_info:
            parse_auctionator_data(str(missing_file))
        assert "NonExistent.lua" in str(exc_info.value) or "not found" in str(exc_info.value).lower()

    def test_parse_both_files_corrupt_raises_error(self, tmp_path):
        primary_file = tmp_path / "Auctionator.lua"
        primary_file.write_text("NOT LUA DATA", encoding="utf-8")
        bak_file = tmp_path / "Auctionator.lua.bak"
        bak_file.write_text("ALSO CORRUPTED DATA", encoding="utf-8")

        with pytest.raises(Exception):
            parse_auctionator_data(str(primary_file))

    def test_parse_direct_table_literal(self, tmp_path):
        lua_content = (
            '{\n'
            '  ["Everlook"] = {\n'
            '    ["3000"] = { ["mr"] = 120 }\n'
            '  }\n'
            '}\n'
        )
        lua_file = tmp_path / "raw_table.lua"
        lua_file.write_text(lua_content, encoding="utf-8")

        data = parse_auctionator_data(str(lua_file))
        assert isinstance(data, dict)
        assert "Everlook" in data
        assert data["Everlook"]["3000"]["mr"] == 120

    def test_parse_stale_backup_older_than_two_hours_rejected(self, tmp_path):
        """Verify backup older than 2 hours is discarded to prevent time-series poisoning (R26)."""
        primary_file = tmp_path / "Auctionator.lua"
        primary_file.write_text("corrupted primary content", encoding="utf-8")

        bak_file = tmp_path / "Auctionator.lua.bak"
        bak_file.write_text(
            'AUCTIONATOR_PRICE_DATABASE = { ["Everlook"] = { ["2589"] = { ["m"] = 500 } } }',
            encoding="utf-8",
        )
        stale_mtime = time.time() - 7300  # > 2 hours old
        os.utime(bak_file, (stale_mtime, stale_mtime))

        # Primary failed, backup is older than 2 hours -> must reject and raise error
        with pytest.raises(Exception):
            parse_auctionator_data(str(primary_file))

        # Direct parse on stale backup must raise ValueError
        with pytest.raises(ValueError) as exc_info:
            parse_auctionator_data(str(bak_file))
        assert "older than 2 hours" in str(exc_info.value)

    def test_parse_fresh_backup_preserves_mtime(self, tmp_path):
        """Verify fresh backup parse sets _source_mtime to backup file mtime (R26)."""
        primary_file = tmp_path / "Auctionator.lua"
        primary_file.write_text("corrupted primary", encoding="utf-8")

        bak_file = tmp_path / "Auctionator.lua.bak"
        bak_file.write_text(
            'AUCTIONATOR_PRICE_DATABASE = { ["Everlook"] = { ["2589"] = { ["m"] = 500 } } }',
            encoding="utf-8",
        )
        fresh_mtime = int(time.time()) - 1800  # 30 minutes ago
        os.utime(bak_file, (fresh_mtime, fresh_mtime))

        data = parse_auctionator_data(str(primary_file))
        assert isinstance(data, dict)
        assert data.get("_source_mtime") == fresh_mtime

    def test_parse_returns_mtime_and_content_hash_metadata(self, tmp_path):
        """Verify parse_auctionator_data attaches _source_mtime (int) and _content_hash (sha256 hex) (R48)."""
        lua_content = (
            'AUCTIONATOR_PRICE_DATABASE = {\n'
            '  ["Everlook"] = {\n'
            '    ["2589"] = { ["m"] = 1500 }\n'
            '  }\n'
            '}\n'
        )
        lua_file = tmp_path / "Auctionator.lua"
        lua_file.write_text(lua_content, encoding="utf-8")

        expected_mtime = int(os.path.getmtime(str(lua_file)))
        expected_hash = hashlib.sha256(lua_file.read_bytes()).hexdigest()

        data = parse_auctionator_data(str(lua_file))
        assert isinstance(data, dict)
        assert "_source_mtime" in data
        assert isinstance(data["_source_mtime"], int)
        assert data["_source_mtime"] == expected_mtime
        assert "_content_hash" in data
        assert isinstance(data["_content_hash"], str)
        assert data["_content_hash"] == expected_hash
        assert len(data["_content_hash"]) == 64

    def test_parse_fallback_bak_preserves_content_hash_and_mtime(self, tmp_path):
        """Verify fallback to .bak also extracts valid _source_mtime and _content_hash (R48)."""
        primary_file = tmp_path / "Auctionator.lua"
        primary_file.write_text("invalid lua syntax !!!", encoding="utf-8")

        bak_content = (
            'AUCTIONATOR_PRICE_DATABASE = {\n'
            '  ["Everlook"] = {\n'
            '    ["2589"] = { ["m"] = 800 }\n'
            '  }\n'
            '}\n'
        )
        bak_file = tmp_path / "Auctionator.lua.bak"
        bak_file.write_text(bak_content, encoding="utf-8")

        expected_bak_mtime = int(os.path.getmtime(str(bak_file)))
        expected_bak_hash = hashlib.sha256(bak_file.read_bytes()).hexdigest()

        data = parse_auctionator_data(str(primary_file))
        assert isinstance(data, dict)
        assert data.get("_source_mtime") == expected_bak_mtime
        assert data.get("_content_hash") == expected_bak_hash


class TestIngestPriceSnapshot:
    """Tests for batch ingestion, field extraction, deduplication, and logging (R07, R08)."""

    def test_ingest_price_extraction_fields(self, memory_db):
        """Verify extraction from mr, price, H..., L..., and numeric strings."""
        raw_data = {
            "Everlook": {
                # 1. mr extraction
                "2589:0:0:0:0": {"mr": 1500, "id": "2589"},
                # 2. explicit price field
                "1205": {"price": 320},
                # 3. H-series high extremum
                "774": {"H1690000000": 450},
                # 4. L-series low extremum
                "888": {"L1690000000": 200},
                # 5. Direct scalar number
                "999": 600,
                # 6. Direct numeric string
                "1111": "750",
                # 7. Unmatched key with valid nested id
                "invalid_key": {"id": "2222:0:0", "mr": 880},
                # 8. Invalid item skipped
                "bad_item": {"mr": 100},
            }
        }
        count = ingest_price_snapshot(memory_db, raw_data, realm="Everlook", current_time=1000)
        assert count == 7

        cur = memory_db.cursor()
        cur.execute("SELECT item_id, price FROM price_snapshots WHERE realm = 'Everlook' ORDER BY item_id")
        rows = dict(cur.fetchall())

        assert rows[774] == 450
        assert rows[888] == 200
        assert rows[999] == 600
        assert rows[1111] == 750
        assert rows[1205] == 320
        assert rows[2222] == 880
        assert rows[2589] == 1500

    def test_batch_insertion_performance_and_integrity(self, memory_db):
        """Verify executemany batch insertion of multiple items with foreign key integrity."""
        raw_items = {}
        for item_id in range(1, 101):
            raw_items[f"{item_id}:0:0:0:0"] = {"mr": item_id * 10}

        raw_data = {"Everlook": raw_items}
        count = ingest_price_snapshot(memory_db, raw_data, realm="Everlook", current_time=2000)
        assert count == 100

        cur = memory_db.cursor()
        cur.execute("SELECT COUNT(*) FROM price_snapshots WHERE realm = 'Everlook'")
        assert cur.fetchone()[0] == 100

        # Verify items table entries were auto-populated to satisfy FK constraints
        cur.execute("SELECT COUNT(*) FROM items")
        assert cur.fetchone()[0] >= 100

    def test_deduplication_ten_minute_window(self, memory_db):
        """Verify identical prices within 10 minutes are skipped, but price change or time elapsed inserts."""
        raw_data_initial = {
            "Everlook": {
                "2589": {"mr": 1000},
                "1205": {"mr": 500},
            }
        }
        # First ingest at t = 1000: both inserted
        t0 = 1000
        inserted_1 = ingest_price_snapshot(
            memory_db, raw_data_initial, realm="Everlook", dedupe_window_minutes=10, current_time=t0
        )
        assert inserted_1 == 2

        # Second ingest at t = 1300 (5 minutes later): identical prices -> 0 inserted
        t1 = 1300
        inserted_2 = ingest_price_snapshot(
            memory_db, raw_data_initial, realm="Everlook", dedupe_window_minutes=10, current_time=t1
        )
        assert inserted_2 == 0

        # Third ingest at t = 1400 (6.6 minutes later): item 2589 changes price to 1100, 1205 unchanged
        t2 = 1400
        raw_data_changed = {
            "Everlook": {
                "2589": {"mr": 1100},
                "1205": {"mr": 500},
            }
        }
        inserted_3 = ingest_price_snapshot(
            memory_db, raw_data_changed, realm="Everlook", dedupe_window_minutes=10, current_time=t2
        )
        assert inserted_3 == 1  # Only 2589 inserted

        # Fourth ingest at t = 2050 (10.8 minutes after t2): 2589 unchanged at 1100, window elapsed -> inserted
        t3 = 2050
        inserted_4 = ingest_price_snapshot(
            memory_db, raw_data_changed, realm="Everlook", dedupe_window_minutes=10, current_time=t3
        )
        assert inserted_4 == 2  # Both inserted because > 10 minutes elapsed

        cur = memory_db.cursor()
        cur.execute("SELECT timestamp, price FROM price_snapshots WHERE item_id = 2589 ORDER BY timestamp")
        history_2589 = cur.fetchall()
        assert len(history_2589) == 3
        assert [h[1] for h in history_2589] == [1000, 1100, 1100]

    def test_structured_logging_to_watcher_log(self, memory_db):
        """Verify that ingestion writes structured log entries to watcher.log."""
        raw_data = {"Everlook": {"2589": {"mr": 1500}}}
        log_file = "watcher.log"

        count = ingest_price_snapshot(memory_db, raw_data, realm="Everlook", current_time=5000)
        assert count == 1

        assert os.path.exists(log_file)
        with open(log_file, "r", encoding="utf-8") as f:
            log_content = f.read()
        assert "Ingested 1 price snapshots for realm 'Everlook'" in log_content

    def test_ingest_empty_or_missing_realm(self, memory_db):
        """Verify graceful return 0 when realm data is empty or missing."""
        assert ingest_price_snapshot(memory_db, {}, realm="Everlook") == 0
        assert ingest_price_snapshot(memory_db, {"OtherRealm": {}}, realm="Everlook") == 0

    def test_ingest_auctionator_v341_and_bool_protection(self, memory_db):
        """Verify Auctionator v341 val['m'], nested h/l, and bool value protection (R32, R39)."""
        raw_data = {
            "Everlook": {
                # Auctionator v341 buyout price in 'm'
                "1001": {"m": 450},
                # Numeric string in 'm'
                "1002": {"m": "620"},
                # Nested 'h' table
                "1003": {"h": {"1700000000": 780}},
                # Bool values must be discarded and NOT recorded as 1 copper
                "1004": {"m": True},
                "1005": {"mr": False},
                "1006": {"price": True},
                "1007": True,
                "1008": False,
            }
        }
        count = ingest_price_snapshot(memory_db, raw_data, realm="Everlook", current_time=1000)
        assert count == 3

        cur = memory_db.cursor()
        cur.execute("SELECT item_id, price FROM price_snapshots WHERE realm = 'Everlook' ORDER BY item_id")
        rows = dict(cur.fetchall())
        assert rows == {1001: 450, 1002: 620, 1003: 780}
        # Ensure bool items were never inserted
        for bad_id in (1004, 1005, 1006, 1007, 1008):
            assert bad_id not in rows

    def test_ingest_realm_with_space_and_source_mtime(self, memory_db):
        """Verify realm with space and explicit source_mtime timestamp handling (R26, R33)."""
        target_realm = "ForeverRealm Alliance"
        fixed_mtime = 1700000000
        raw_data = {
            target_realm: {
                "2589": {"m": 1200},
                "1205": {"m": 350},
            }
        }
        count = ingest_price_snapshot(
            memory_db,
            raw_data,
            realm=target_realm,
            source_mtime=fixed_mtime,
            current_time=2000000000,
        )
        assert count == 2

        cur = memory_db.cursor()
        cur.execute(
            "SELECT item_id, realm, price, timestamp FROM price_snapshots WHERE realm = ? ORDER BY item_id",
            (target_realm,),
        )
        rows = cur.fetchall()
        assert len(rows) == 2
        assert tuple(rows[0]) == (1205, target_realm, 350, fixed_mtime)
        assert tuple(rows[1]) == (2589, target_realm, 1200, fixed_mtime)

    def test_ingest_source_mtime_from_raw_data_metadata(self, memory_db):
        """Verify _source_mtime in raw_data is used as snapshot timestamp (R26)."""
        fixed_mtime = 1700000123
        raw_data = {
            "_source_mtime": fixed_mtime,
            "Everlook": {
                "3000": {"m": 888},
            },
        }
        count = ingest_price_snapshot(memory_db, raw_data, realm="Everlook", current_time=2000000000)
        assert count == 1

        cur = memory_db.cursor()
        cur.execute("SELECT timestamp, price FROM price_snapshots WHERE item_id = 3000")
        row = cur.fetchone()
        assert tuple(row) == (fixed_mtime, 888)

    def test_ingest_discards_zero_and_negative_prices(self, memory_db):
        """Verify ingest_price_snapshot discards price <= 0 and records nothing into price_snapshots (R48)."""
        raw_data = {
            "Everlook": {
                # Valid positive prices
                "100": {"m": 500},
                "200": 1200,
                # Zero prices across different structures
                "101": {"m": 0},
                "102": {"mr": 0},
                "103": {"price": 0},
                "104": 0,
                "105": "0",
                "106": {"h": {"1700000000": 0}},
                # Negative prices
                "107": {"m": -50},
                "108": -100,
                "109": {"mr": -1},
            }
        }
        count = ingest_price_snapshot(memory_db, raw_data, realm="Everlook", current_time=1000)
        # Only items 100 and 200 should be ingested
        assert count == 2

        cur = memory_db.cursor()
        cur.execute("SELECT item_id, price FROM price_snapshots WHERE realm = 'Everlook' ORDER BY item_id")
        rows = [tuple(r) for r in cur.fetchall()]
        assert len(rows) == 2
        assert rows[0] == (100, 500)
        assert rows[1] == (200, 1200)

        # Confirm explicitly that zero or negative prices are never recorded
        cur.execute("SELECT COUNT(*) FROM price_snapshots WHERE price <= 0")
        assert cur.fetchone()[0] == 0

    def test_ingest_repeated_unchanged_file_prevents_false_observations(self, memory_db):
        """Verify unchanged content_hash / source_mtime returns 0 and prevents duplicate rows after > 600s (R48)."""
        file_hash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        source_mtime = 1700000000

        raw_data = {
            "_content_hash": file_hash,
            "_source_mtime": source_mtime,
            "Everlook": {
                "2589": {"m": 1500},
                "1205": {"m": 320},
            },
        }

        # First import at t = 1700000000: inserts both items
        inserted_1 = ingest_price_snapshot(
            memory_db, raw_data, realm="Everlook", current_time=source_mtime
        )
        assert inserted_1 == 2

        cur = memory_db.cursor()
        cur.execute("SELECT COUNT(*) FROM price_snapshots WHERE realm = 'Everlook'")
        assert cur.fetchone()[0] == 2

        # Second import at t = 1700001500 (1500 seconds later, > 600s dedupe window):
        # File has identical content_hash and source_mtime -> must return 0 inserts
        t_later = source_mtime + 1500
        inserted_2 = ingest_price_snapshot(
            memory_db, raw_data, realm="Everlook", current_time=t_later
        )
        assert inserted_2 == 0

        # Verify no duplicate entries were created in price_snapshots
        cur.execute("SELECT COUNT(*) FROM price_snapshots WHERE realm = 'Everlook'")
        assert cur.fetchone()[0] == 2

        # Third import with explicit arguments passing identical hash and mtime
        inserted_3 = ingest_price_snapshot(
            memory_db,
            raw_data,
            realm="Everlook",
            current_time=source_mtime + 3600,
            content_hash=file_hash,
            source_mtime=source_mtime,
        )
        assert inserted_3 == 0

        cur.execute("SELECT COUNT(*) FROM price_snapshots WHERE realm = 'Everlook'")
        assert cur.fetchone()[0] == 2

    def test_ingest_repeated_unchanged_file_persists_in_db_metadata(self, temp_db):
        """Verify _etl_metadata table in SQLite persists file hash across connections to prevent duplicates (R48)."""
        db_path, conn = temp_db
        file_hash = "abcde1234567890f" * 4
        fixed_mtime = 1710000000

        raw_data = {
            "_content_hash": file_hash,
            "_source_mtime": fixed_mtime,
            "Everlook": {
                "5001": {"m": 9999},
            },
        }

        # 1. Ingest on initial connection
        assert ingest_price_snapshot(conn, raw_data, realm="Everlook", current_time=fixed_mtime) == 1

        # 2. Open a brand new connection to simulate restarting process / new session
        conn2 = sqlite3.connect(db_path)
        try:
            # More than 600 seconds later (2000s later)
            count2 = ingest_price_snapshot(
                conn2, raw_data, realm="Everlook", current_time=fixed_mtime + 2000
            )
            assert count2 == 0

            cur = conn2.cursor()
            cur.execute("SELECT COUNT(*) FROM price_snapshots WHERE item_id = 5001")
            assert cur.fetchone()[0] == 1
        finally:
            conn2.close()


class TestWatcherEngine:
    """Tests for file system watcher and event handling (R05)."""

    def test_start_watcher_creates_and_stops_cleanly_via_stop_event(self, tmp_path):
        """Verify start_watcher launches Observer and terminates on stop_event without leaking threads."""
        watch_dir = tmp_path / "WTF"
        watch_dir.mkdir()
        db_path = str(tmp_path / "market.db")
        init_db(db_path).close()

        stop_event = threading.Event()
        watcher_thread = threading.Thread(
            target=start_watcher,
            args=(str(watch_dir), db_path, "Everlook", stop_event),
            daemon=True,
        )
        watcher_thread.start()

        # Let the watcher start
        time.sleep(0.15)
        assert watcher_thread.is_alive()

        # Signal stop
        stop_event.set()
        watcher_thread.join(timeout=3.0)
        assert not watcher_thread.is_alive()

    def test_start_watcher_returns_running_observer_without_stop_event(self, tmp_path):
        """Verify start_watcher returns active Observer when stop_event is None."""
        watch_dir = tmp_path / "WTF"
        watch_dir.mkdir()
        db_path = str(tmp_path / "market.db")
        init_db(db_path).close()

        observer = start_watcher(str(watch_dir), db_path, "Everlook", stop_event=None)
        try:
            assert observer.is_alive()
        finally:
            observer.stop()
            observer.join(timeout=2.0)
        assert not observer.is_alive()

    def test_start_watcher_with_direct_file_path(self, tmp_path):
        """Verify start_watcher works when pointing directly to a file path."""
        watch_dir = tmp_path / "SavedVariables"
        watch_dir.mkdir()
        file_path = str(watch_dir / "Auctionator.lua")
        db_path = str(tmp_path / "market.db")
        init_db(db_path).close()

        observer = start_watcher(file_path, db_path, "Everlook", stop_event=None)
        try:
            assert observer.is_alive()
        finally:
            observer.stop()
            observer.join(timeout=2.0)
        assert not observer.is_alive()

    def test_auctionator_event_handler_triggers_etl(self, tmp_path):
        """Verify AuctionatorEventHandler on_modified triggers ETL ingestion."""
        watch_dir = tmp_path / "SavedVariables"
        watch_dir.mkdir()
        target_file = watch_dir / "Auctionator.lua"
        db_path = str(tmp_path / "market.db")
        init_db(db_path).close()

        handler = AuctionatorEventHandler(
            target_filename="Auctionator.lua",
            full_target_path=str(target_file),
            db_path=db_path,
            realm="Everlook",
            debounce_seconds=0.01,
        )

        # Write valid Lua content
        lua_content = (
            'AUCTIONATOR_PRICE_DATABASE = {\n'
            '  ["Everlook"] = {\n'
            '    ["2589:0:0:0:0"] = { ["mr"] = 7777 }\n'
            '  }\n'
            '}\n'
        )
        target_file.write_text(lua_content, encoding="utf-8")

        # Simulate on_modified event
        event = FileModifiedEvent(str(target_file))
        handler.on_modified(event)

        # Check that ETL was triggered and populated the SQLite DB
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT item_id, price FROM price_snapshots WHERE realm = 'Everlook'")
        rows = cur.fetchall()
        conn.close()

        assert len(rows) == 1
        assert rows[0][0] == 2589
        assert rows[0][1] == 7777

    def test_auctionator_event_handler_callback_invoked(self, tmp_path):
        """Verify AuctionatorEventHandler invokes callback with (target_file, count) on successful ingest."""
        watch_dir = tmp_path / "SavedVariables"
        watch_dir.mkdir()
        target_file = watch_dir / "Auctionator.lua"
        db_path = str(tmp_path / "market.db")
        init_db(db_path).close()

        callback_mock = MagicMock()
        handler = AuctionatorEventHandler(
            target_filename="Auctionator.lua",
            full_target_path=str(target_file),
            db_path=db_path,
            realm="Everlook",
            debounce_seconds=0.01,
            callback=callback_mock,
        )

        lua_content = (
            'AUCTIONATOR_PRICE_DATABASE = {\n'
            '  ["Everlook"] = {\n'
            '    ["2589:0:0:0:0"] = { ["mr"] = 5555 },\n'
            '    ["1205:0:0:0:0"] = { ["mr"] = 1234 }\n'
            '  }\n'
            '}\n'
        )
        target_file.write_text(lua_content, encoding="utf-8")

        handler._trigger_etl(str(target_file))

        callback_mock.assert_called_once_with(str(target_file), 2)

    def test_auctionator_event_handler_callback_not_called_on_zero_ingest(self, tmp_path):
        """Verify callback is not invoked when count is 0."""
        watch_dir = tmp_path / "SavedVariables"
        watch_dir.mkdir()
        target_file = watch_dir / "Auctionator.lua"
        db_path = str(tmp_path / "market.db")
        init_db(db_path).close()

        callback_mock = MagicMock()
        handler = AuctionatorEventHandler(
            target_filename="Auctionator.lua",
            full_target_path=str(target_file),
            db_path=db_path,
            realm="Everlook",
            debounce_seconds=0.01,
            callback=callback_mock,
        )

        target_file.write_text("AUCTIONATOR_PRICE_DATABASE = {}", encoding="utf-8")

        handler._trigger_etl(str(target_file))
        assert not callback_mock.called

    def test_auctionator_event_handler_callback_exception_handled(self, tmp_path):
        """Verify that an exception inside the callback does not crash the event handler."""
        watch_dir = tmp_path / "SavedVariables"
        watch_dir.mkdir()
        target_file = watch_dir / "Auctionator.lua"
        db_path = str(tmp_path / "market.db")
        init_db(db_path).close()

        failing_callback = MagicMock(side_effect=RuntimeError("Portal rebuild failed"))
        handler = AuctionatorEventHandler(
            target_filename="Auctionator.lua",
            full_target_path=str(target_file),
            db_path=db_path,
            realm="Everlook",
            debounce_seconds=0.01,
            callback=failing_callback,
        )

        lua_content = (
            'AUCTIONATOR_PRICE_DATABASE = {\n'
            '  ["Everlook"] = {\n'
            '    ["2589:0:0:0:0"] = { ["mr"] = 5555 }\n'
            '  }\n'
            '}\n'
        )
        target_file.write_text(lua_content, encoding="utf-8")

        # Must not raise exception
        handler._trigger_etl(str(target_file))
        failing_callback.assert_called_once_with(str(target_file), 1)

    def test_start_watcher_recursive_nested_directories(self, tmp_path):
        """Verify start_watcher schedules a recursive observer that handles nested directory files."""
        root_watch_dir = tmp_path / "WTF"
        nested_dir = root_watch_dir / "Account" / "TEST_ACC" / "SavedVariables"
        nested_dir.mkdir(parents=True)
        nested_file = nested_dir / "Auctionator.lua"

        db_path = str(tmp_path / "market.db")
        init_db(db_path).close()

        callback_mock = MagicMock()
        observer = start_watcher(
            str(root_watch_dir),
            db_path=db_path,
            realm="Everlook",
            stop_event=None,
            callback=callback_mock,
        )

        try:
            # 1. Verify observer was scheduled with recursive=True on root_watch_dir
            watches = list(observer._watches)
            assert len(watches) >= 1
            watch = watches[0]
            assert watch.is_recursive is True
            assert os.path.abspath(str(root_watch_dir)) == os.path.abspath(watch.path)

            # 2. Verify event handler associated with observer handles nested file events
            handlers = list(observer._handlers[watch])
            assert len(handlers) >= 1
            handler = handlers[0]
            assert isinstance(handler, AuctionatorEventHandler)
            assert handler.callback == callback_mock

            # Write valid data to the nested file
            lua_content = (
                'AUCTIONATOR_PRICE_DATABASE = {\n'
                '  ["Everlook"] = {\n'
                '    ["3001:0:0:0:0"] = { ["mr"] = 8888 }\n'
                '  }\n'
                '}\n'
            )
            nested_file.write_text(lua_content, encoding="utf-8")

            # Simulate modified event on nested path
            event = FileModifiedEvent(str(nested_file))
            handler.on_modified(event)

            # Verify DB ingestion from nested path
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute("SELECT item_id, price FROM price_snapshots WHERE item_id = 3001")
            row = cur.fetchone()
            conn.close()

            assert row is not None
            assert row[0] == 3001
            assert row[1] == 8888

            # Verify callback received the nested file path and count
            callback_mock.assert_called_once_with(str(nested_file), 1)

        finally:
            observer.stop()
            observer.join(timeout=2.0)

    def test_start_watcher_creates_missing_directory_and_targets_auctionator_lua(self, tmp_path):
        """Verify start_watcher creates non-existent directory via os.makedirs and targets Auctionator.lua (R27)."""
        nonexistent_dir = tmp_path / "NonExistent" / "SavedVariables"
        assert not nonexistent_dir.exists()

        db_path = str(tmp_path / "market.db")
        init_db(db_path).close()

        observer = start_watcher(str(nonexistent_dir), db_path, "Everlook", stop_event=None)
        try:
            assert nonexistent_dir.exists()
            assert nonexistent_dir.is_dir()

            watches = list(observer._watches)
            assert len(watches) >= 1
            watch = watches[0]
            handlers = list(observer._handlers[watch])
            assert len(handlers) >= 1
            handler = handlers[0]
            assert isinstance(handler, AuctionatorEventHandler)
            assert handler.target_filename == "auctionator.lua"
            assert os.path.basename(handler.full_target_path).lower() == "auctionator.lua"
        finally:
            observer.stop()
            observer.join(timeout=2.0)
        assert not observer.is_alive()

