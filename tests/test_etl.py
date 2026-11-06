"""Tests for File Watcher and ETL Pipeline (Ticket 02, R05, R06, R07, R08, R48, R51)."""
import argparse
import subprocess
import sys
import unittest.mock
from run_watcher import initial_ingest, main as watcher_main

import hashlib
import logging
import os
import sqlite3
import threading
import time
from unittest.mock import MagicMock
import json
import urllib.error
import urllib.request
from src.wowhead import WowheadResolver, fetch_wowhead_item

import pytest
from watchdog.events import FileModifiedEvent

from src.database import init_db
from src.etl import (
    AuctionatorEventHandler,
    ingest_price_snapshot,
    load_profitcrafter_data,
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

    def test_normalize_composite_auctionator_keys(self):
        """Verify normalization of composite Auctionator keys and safe handling of malformed keys."""
        assert normalize_item_id("g:1234:567") == 1234
        assert normalize_item_id("p:3858") == 3858
        assert normalize_item_id("item:2450:0") == 2450
        assert normalize_item_id("2452:0:0:0") == 2452

        # Invalid/malformed keys safely return None
        assert normalize_item_id("g:") is None
        assert normalize_item_id("p:") is None
        assert normalize_item_id("item:") is None
        assert normalize_item_id(":::") is None
        assert normalize_item_id("g:abc:def") is None
        assert normalize_item_id("p:xyz") is None
        assert normalize_item_id("unknown:format") is None
        assert normalize_item_id("") is None
        assert normalize_item_id(None) is None

    def test_normalize_sqlite_integer_bounds(self):
        """Verify item_id exceeding signed 64-bit int max is rejected safely (Audit §17)."""
        assert normalize_item_id(9223372036854775807) == 9223372036854775807
        assert normalize_item_id(9223372036854775808) is None
        assert normalize_item_id("9223372036854775808") is None
        assert normalize_item_id("9223372036854775808:0:0") is None


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

    def test_parse_older_bak_discarded_when_mtime_le_recorded_source_mtime(self, tmp_path):
        """Verify an older .bak file (mtime <= last imported mtime) is discarded to prevent rollback (Audit §4)."""
        db_file = str(tmp_path / "test_bak.db")
        conn = init_db(db_file)
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS _etl_metadata (
                realm TEXT PRIMARY KEY,
                content_hash TEXT,
                source_mtime INTEGER,
                last_imported_time INTEGER
            )
            """
        )
        cur.execute(
            "INSERT INTO _etl_metadata (realm, content_hash, source_mtime, last_imported_time) VALUES (?, ?, ?, ?)",
            ("Everlook", "hash_t2000", 2000, 2000),
        )
        conn.commit()

        primary_file = tmp_path / "Auctionator.lua"
        primary_file.write_text("corrupted primary content {{{", encoding="utf-8")

        bak_file = tmp_path / "Auctionator.lua.bak"
        bak_file.write_text(
            'AUCTIONATOR_PRICE_DATABASE = { ["Everlook"] = { ["2589"] = { ["m"] = 500 } } }',
            encoding="utf-8",
        )
        now = int(time.time())
        # Record source_mtime = now - 100
        cur.execute(
            "UPDATE _etl_metadata SET source_mtime = ?, last_imported_time = ? WHERE realm = ?",
            (now - 100, now - 100, "Everlook"),
        )
        conn.commit()

        # Set backup mtime to now - 200 (within 2h window, but older than recorded now - 100)
        os.utime(bak_file, (now - 200, now - 200))

        # Fallback to older backup must be rejected and raise exception
        with pytest.raises(Exception):
            parse_auctionator_data(str(primary_file), conn=conn, realm="Everlook")

        # Direct parse of older backup file must raise ValueError
        with pytest.raises(ValueError) as exc_info:
            parse_auctionator_data(str(bak_file), conn=conn, realm="Everlook")
        assert "recorded source_mtime" in str(exc_info.value)
        conn.close()


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

    def test_ingest_price_snapshot_realm_autodiscovery(self, memory_db, caplog):
        """Verify ingest_price_snapshot auto-discovers realm with max item count when target realm is missing."""
        raw_data = {
            "__dbversion": 1,
            "_source_mtime": 1700000000,
            "SmallRealm": {
                "1001": {"m": 150},
            },
            "DominantRealm": {
                "2001": {"m": 250},
                "2002": {"m": 350},
                "2003": {"m": 450},
            },
        }

        with caplog.at_level(logging.WARNING, logger="watcher"):
            count = ingest_price_snapshot(
                memory_db,
                raw_data,
                realm="NonExistentRealm",
                current_time=1700000000,
            )

        assert count == 3
        assert raw_data.get("_discovered_realm") == "DominantRealm"
        assert any(
            "NonExistentRealm" in record.message
            and "DominantRealm" in record.message
            and "3 items" in record.message
            for record in caplog.records
        )

        cur = memory_db.cursor()
        cur.execute(
            "SELECT item_id, realm, price FROM price_snapshots WHERE realm = 'DominantRealm' ORDER BY item_id"
        )
        rows = [tuple(r) for r in cur.fetchall()]
        assert len(rows) == 3
        assert rows[0] == (2001, "DominantRealm", 250)
        assert rows[1] == (2002, "DominantRealm", 350)
        assert rows[2] == (2003, "DominantRealm", 450)

    def test_ingest_price_snapshot_realm_autodiscovery_nested_table(self, memory_db):
        """Verify realm auto-discovery works when table is nested inside AUCTIONATOR_PRICE_DATABASE."""
        nested_data = {
            "AUCTIONATOR_PRICE_DATABASE": {
                "DiscoveredRealm": {
                    "3001": {"m": 500},
                    "3002": {"m": 600},
                }
            }
        }
        count = ingest_price_snapshot(memory_db, nested_data, realm="MissingRealm", current_time=1000)
        assert count == 2

        cur = memory_db.cursor()
        cur.execute("SELECT item_id, realm, price FROM price_snapshots WHERE realm = 'DiscoveredRealm'")
        rows = cur.fetchall()
        assert len(rows) == 2

    def test_ingest_historical_price_picks_highest_timestamp_unsorted(self, memory_db):
        """Verify ingest_price_snapshot selects the price for the latest timestamp in unsorted h/l history (Audit §7)."""
        raw_data = {
            "Everlook": {
                # Nested 'h' history with reverse order timestamps: {"1700000000": 780, "1700003600": 900}
                "2589": {
                    "h": {
                        "1700000000": 780,
                        "1700003600": 900,
                    }
                },
                # Nested 'l' history with unsorted timestamps
                "1205": {
                    "l": {
                        "1700001000": 200,
                        "1700009999": 350,
                        "1700005000": 300,
                    }
                },
            }
        }

        count = ingest_price_snapshot(memory_db, raw_data, realm="Everlook", current_time=1000)
        assert count == 2

        cur = memory_db.cursor()
        cur.execute("SELECT item_id, price FROM price_snapshots WHERE realm = 'Everlook' ORDER BY item_id")
        rows = dict(cur.fetchall())

        assert rows[2589] == 900  # highest timestamp 1700003600 picks 900, not 780
        assert rows[1205] == 350  # highest timestamp 1700009999 picks 350

    def test_ingest_sqlite_integer_bounds_overflow_error_handled_safely(self, memory_db):
        """Verify ingest_price_snapshot safely discards item_id or price >= 9223372036854775808 without OverflowError (Audit §17)."""
        overflow_val = 9223372036854775808  # 2^63, exceeds SQLite signed 64-bit int max
        raw_data = {
            "Everlook": {
                # Overflow item_id (string & numeric)
                "9223372036854775808": {"m": 500},
                overflow_val: {"m": 500},
                # Overflow price in 'm'
                "2589": {"m": overflow_val},
                # Overflow price as direct scalar
                "1205": overflow_val,
                # Overflow price in 'mr'
                "774": {"mr": overflow_val},
                # Overflow price in 'price'
                "888": {"price": overflow_val},
                # Overflow price in nested 'h'
                "999": {"h": {"1700000000": overflow_val}},
                # Overflow price in nested 'l'
                "1000": {"l": {"1700000000": overflow_val}},
                # Valid normal item to verify batch ingestion continues safely
                "3858": {"m": 1200},
            }
        }

        # Must not raise OverflowError or crash
        count = ingest_price_snapshot(memory_db, raw_data, realm="Everlook", current_time=1000)
        assert count == 1

        cur = memory_db.cursor()
        cur.execute("SELECT item_id, price FROM price_snapshots WHERE realm = 'Everlook'")
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == 3858
        assert rows[0][1] == 1200

    def test_atomic_deduplication_with_begin_immediate_concurrent(self, tmp_path):
        """Verify concurrent ingestion with BEGIN IMMEDIATE ensures atomicity and no duplicate snapshots (Audit §18)."""
        db_file = str(tmp_path / "concurrent_market.db")
        init_db(db_file).close()

        raw_data = {
            "_content_hash": "hash_concurrent_test_begin_immediate",
            "_source_mtime": 1700000000,
            "Everlook": {
                "2589": {"m": 1500},
                "1205": {"m": 320},
                "3858": {"m": 450},
            },
        }

        results = []
        barrier = threading.Barrier(2)

        def worker():
            barrier.wait()
            conn = sqlite3.connect(db_file, timeout=15.0)
            try:
                inserted = ingest_price_snapshot(
                    conn,
                    raw_data,
                    realm="Everlook",
                    current_time=1700000000,
                    db_path=db_file,
                )
                results.append(inserted)
            finally:
                conn.close()

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join(timeout=10.0)
        t2.join(timeout=10.0)

        assert len(results) == 2
        # One thread should insert 3 items, the second should be deduplicated and insert 0 items
        assert sorted(results) == [0, 3]

        # Verify exact snapshot count in database: exactly 3 rows, no duplicate snapshots
        verify_conn = sqlite3.connect(db_file)
        cur = verify_conn.cursor()
        cur.execute("SELECT COUNT(*) FROM price_snapshots WHERE realm = 'Everlook'")
        total_snapshots = cur.fetchone()[0]
        cur.execute("SELECT item_id, COUNT(*) FROM price_snapshots WHERE realm = 'Everlook' GROUP BY item_id")
        per_item_counts = cur.fetchall()
        verify_conn.close()

        assert total_snapshots == 3
        for item_id, count in per_item_counts:
            assert count == 1, f"Item {item_id} has duplicate snapshot count {count}"


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

    def test_wait_for_file_ready_stability_and_closing_brace(self, tmp_path):
        """Verify _wait_for_file_ready verifies size stability and closing brace '}' before proceeding."""
        test_file = tmp_path / "Auctionator.lua"
        db_path = str(tmp_path / "market.db")
        init_db(db_path).close()

        handler = AuctionatorEventHandler(
            target_filename="Auctionator.lua",
            full_target_path=str(test_file),
            db_path=db_path,
            realm="Everlook",
            debounce_seconds=0.02,
        )

        # 1. Non-existent file returns False
        assert handler._wait_for_file_ready(str(tmp_path / "nonexistent.lua")) is False

        # 2. Complete file ending with '}' stabilizes and returns True
        test_file.write_text('AUCTIONATOR_PRICE_DATABASE = {\n  ["Everlook"] = {}\n}', encoding="utf-8")
        assert handler._wait_for_file_ready(str(test_file)) is True

        # 3. Size stability & closing brace wait: write incomplete file, then append closing brace in background thread
        incomplete_file = tmp_path / "Incomplete.lua"
        incomplete_file.write_text('AUCTIONATOR_PRICE_DATABASE = {\n  ["Everlook"] = {', encoding="utf-8")

        def append_closing():
            time.sleep(0.03)
            with open(incomplete_file, "a", encoding="utf-8") as f:
                f.write(' ["100"] = { ["m"] = 50 } }\n}\n')

        t = threading.Thread(target=append_closing)
        t.start()
        try:
            ready = handler._wait_for_file_ready(str(incomplete_file))
            assert ready is True
        finally:
            t.join()

    def test_wait_for_file_ready_incomplete_file_returns_false(self, tmp_path):
        """Verify _wait_for_file_ready returns False when trailing closing brace is missing (Audit §4)."""
        test_file = tmp_path / "IncompleteAuctionator.lua"
        test_file.write_text(
            'AUCTIONATOR_PRICE_DATABASE = {\n  ["Everlook"] = {\n    ["2589"] = { ["mr"] = 1500\n',
            encoding="utf-8",
        )

        handler = AuctionatorEventHandler(
            target_filename="IncompleteAuctionator.lua",
            full_target_path=str(test_file),
            db_path=str(tmp_path / "market.db"),
            realm="Everlook",
            debounce_seconds=0.01,
        )
        ready = handler._wait_for_file_ready(str(test_file))
        assert ready is False

    def test_realm_discovery_isolation_preserves_handler_realm(self, tmp_path):
        """Verify realm auto-discovery does not mutate handler.realm on AuctionatorEventHandler (Audit §5)."""
        watch_dir = tmp_path / "SavedVariables"
        watch_dir.mkdir()
        target_file = watch_dir / "Auctionator.lua"
        db_path = str(tmp_path / "market.db")
        init_db(db_path).close()

        handler = AuctionatorEventHandler(
            target_filename="Auctionator.lua",
            full_target_path=str(target_file),
            db_path=db_path,
            realm="AllianceRealm",
            debounce_seconds=0.01,
        )

        lua_content = (
            'AUCTIONATOR_PRICE_DATABASE = {\n'
            '  ["HordeRealm"] = {\n'
            '    ["2589:0:0:0:0"] = { ["mr"] = 7777 }\n'
            '  }\n'
            '}\n'
        )
        target_file.write_text(lua_content, encoding="utf-8")

        event = FileModifiedEvent(str(target_file))
        handler.on_modified(event)

        # Handler's configured realm must remain untouched
        assert handler.realm == "AllianceRealm"


class TestInitialIngestAndCLI:
    """Tests for initial database ingestion, realm validation, and CLI configuration (R51)."""

    def test_initial_ingest_from_existing_directory_populates_db(self, tmp_path, memory_db):
        """Verify initial_ingest reads existing Auctionator.lua in watch_dir and inserts rows into price_snapshots."""
        lua_content = (
            'AUCTIONATOR_PRICE_DATABASE = {\n'
            '  ["ForeverRealm_Alliance"] = {\n'
            '    ["2589:0:0:0:0"] = { ["mr"] = 1500 },\n'
            '    ["1205:0:0:0:0"] = { ["mr"] = 320 }\n'
            '  }\n'
            '}\n'
        )
        lua_file = tmp_path / "Auctionator.lua"
        lua_file.write_text(lua_content, encoding="utf-8")

        count = initial_ingest(memory_db, str(tmp_path), "ForeverRealm_Alliance")
        assert count == 2

        cur = memory_db.cursor()
        cur.execute(
            "SELECT item_id, price, realm FROM price_snapshots WHERE realm = 'ForeverRealm_Alliance' ORDER BY item_id"
        )
        rows = cur.fetchall()
        assert len(rows) == 2
        assert tuple(rows[0]) == (1205, 320, "ForeverRealm_Alliance")
        assert tuple(rows[1]) == (2589, 1500, "ForeverRealm_Alliance")

    def test_initial_ingest_direct_file_path(self, tmp_path, memory_db):
        """Verify initial_ingest accepts direct path to .lua file."""
        lua_content = (
            'AUCTIONATOR_PRICE_DATABASE = {\n'
            '  ["ForeverRealm_Alliance"] = {\n'
            '    ["774"] = { ["mr"] = 450 }\n'
            '  }\n'
            '}\n'
        )
        lua_file = tmp_path / "CustomAuctionator.lua"
        lua_file.write_text(lua_content, encoding="utf-8")

        count = initial_ingest(memory_db, str(lua_file), "ForeverRealm_Alliance")
        assert count == 1

        cur = memory_db.cursor()
        cur.execute("SELECT item_id, price FROM price_snapshots WHERE item_id = 774")
        assert tuple(cur.fetchone()) == (774, 450)

    def test_initial_ingest_missing_file_returns_zero(self, tmp_path, memory_db):
        """Verify initial_ingest gracefully returns 0 when file does not exist."""
        count = initial_ingest(memory_db, str(tmp_path), "ForeverRealm_Alliance")
        assert count == 0

    def test_initial_ingest_corrupted_file_returns_zero_without_crash(self, tmp_path, memory_db):
        """Verify corrupted Lua file is handled safely without crashing initial_ingest."""
        lua_file = tmp_path / "Auctionator.lua"
        lua_file.write_text("invalid lua syntax {{{{", encoding="utf-8")

        count = initial_ingest(memory_db, str(tmp_path), "ForeverRealm_Alliance")
        assert count == 0

    def test_initial_ingest_realm_autodiscovery_when_realm_not_found(self, tmp_path, memory_db, caplog):
        """Verify initial_ingest auto-discovers and imports from realm with max item count when target realm is missing."""
        lua_content = (
            'AUCTIONATOR_PRICE_DATABASE = {\n'
            '  ["SmallRealm"] = {\n'
            '    ["1205"] = { ["mr"] = 320 }\n'
            '  },\n'
            '  ["Everlook"] = {\n'
            '    ["2589"] = { ["mr"] = 1500 },\n'
            '    ["2450"] = { ["mr"] = 600 }\n'
            '  }\n'
            '}\n'
        )
        lua_file = tmp_path / "Auctionator.lua"
        lua_file.write_text(lua_content, encoding="utf-8")

        with caplog.at_level(logging.WARNING, logger="watcher"):
            count = initial_ingest(memory_db, str(tmp_path), "ForeverRealm_Alliance")

        assert count == 2
        assert any(
            "ForeverRealm_Alliance" in record.message
            and "Everlook" in record.message
            and "2 items" in record.message
            for record in caplog.records
        )

        cur = memory_db.cursor()
        cur.execute(
            "SELECT item_id, price, realm FROM price_snapshots WHERE realm = 'Everlook' ORDER BY item_id"
        )
        rows = cur.fetchall()
        assert len(rows) == 2
        assert tuple(rows[0]) == (2450, 600, "Everlook")
        assert tuple(rows[1]) == (2589, 1500, "Everlook")

    def test_initial_ingest_no_realms_available_returns_zero(self, tmp_path, memory_db):
        """Verify initial_ingest returns 0 when no realms exist in parsed Lua data."""
        lua_content = 'AUCTIONATOR_PRICE_DATABASE = {}\n'
        lua_file = tmp_path / "Auctionator.lua"
        lua_file.write_text(lua_content, encoding="utf-8")

        count = initial_ingest(memory_db, str(tmp_path), "ForeverRealm_Alliance")
        assert count == 0

        cur = memory_db.cursor()
        cur.execute("SELECT COUNT(*) FROM price_snapshots")
        assert cur.fetchone()[0] == 0

    def test_initial_ingest_space_and_underscore_realm_normalization(self, tmp_path, memory_db):
        """Verify initial_ingest matches realm names with spaces or underscores without false mismatch warning."""
        lua_content = (
            'AUCTIONATOR_PRICE_DATABASE = {\n'
            '  ["ForeverRealm Alliance"] = {\n'
            '    ["2589"] = { ["mr"] = 1500 }\n'
            '  }\n'
            '}\n'
        )
        lua_file = tmp_path / "Auctionator.lua"
        lua_file.write_text(lua_content, encoding="utf-8")

        count = initial_ingest(memory_db, str(tmp_path), "ForeverRealm_Alliance")
        assert count == 1

        cur = memory_db.cursor()
        cur.execute("SELECT item_id, price FROM price_snapshots WHERE item_id = 2589")
        assert tuple(cur.fetchone()) == (2589, 1500)

    def test_cli_argument_parsing_perks_and_ttl(self):
        """Verify main correctly parses --bartering, --master-chef, and --max-age-hours CLI flags."""
        captured_args = []
        orig_parse_args = argparse.ArgumentParser.parse_args

        def intercept_parse_args(parser_self, *args, **kwargs):
            res = orig_parse_args(parser_self, *args, **kwargs)
            captured_args.append(res)
            raise StopIteration("cli_intercepted")

        with unittest.mock.patch("argparse.ArgumentParser.parse_args", intercept_parse_args):
            with pytest.raises(StopIteration, match="cli_intercepted"):
                watcher_main(["--bartering", "--master-chef", "--max-age-hours", "18.5"])

        assert len(captured_args) == 1
        parsed = captured_args[0]
        assert parsed.bartering is True
        assert parsed.master_chef is True
        assert parsed.max_age_hours == 18.5

    def test_cli_argument_defaults(self):
        """Verify main defaults: bartering=False, master_chef=False, max_age_hours=48.0."""
        captured_args = []
        orig_parse_args = argparse.ArgumentParser.parse_args

        def intercept_parse_args(parser_self, *args, **kwargs):
            res = orig_parse_args(parser_self, *args, **kwargs)
            captured_args.append(res)
            raise StopIteration("cli_intercepted")

        with unittest.mock.patch("argparse.ArgumentParser.parse_args", intercept_parse_args):
            with pytest.raises(StopIteration, match="cli_intercepted"):
                watcher_main([])

        assert len(captured_args) == 1
        parsed = captured_args[0]
        assert parsed.bartering is False
        assert parsed.master_chef is False
        assert parsed.max_age_hours == 48.0
        assert parsed.realm == "ForeverRealm_Alliance"

    def test_cli_argument_custom_realm_and_paths(self, tmp_path):
        """Verify main correctly parses --realm, --watch-dir, --db, and --dashboard-out."""
        captured_args = []
        orig_parse_args = argparse.ArgumentParser.parse_args

        def intercept_parse_args(parser_self, *args, **kwargs):
            res = orig_parse_args(parser_self, *args, **kwargs)
            captured_args.append(res)
            raise StopIteration("cli_intercepted")

        custom_dir = str(tmp_path / "custom_wtf")
        with unittest.mock.patch("argparse.ArgumentParser.parse_args", intercept_parse_args):
            with pytest.raises(StopIteration, match="cli_intercepted"):
                watcher_main([
                    "--realm", "Everlook",
                    "--watch-dir", custom_dir,
                    "--db", "custom.db",
                    "--dashboard-out", "custom_portal.html",
                ])

        assert len(captured_args) == 1
        parsed = captured_args[0]
        assert parsed.realm == "Everlook"
        assert parsed.watch_dir == custom_dir
        assert parsed.db == "custom.db"
        assert parsed.dashboard_out == "custom_portal.html"

    def test_cli_run_once_argument_and_execution(self, tmp_path):
        """Verify --run-once CLI flag exists, executes without unrecognized arguments, and exits with code 0 (Audit §16)."""
        # 1. Subprocess check with --run-once and --help
        res_help = subprocess.run(
            [sys.executable, "run_watcher.py", "--run-once", "--help"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert res_help.returncode == 0
        assert "unrecognized arguments" not in res_help.stderr.lower()
        assert "--run-once" in res_help.stdout

        # 2. Main argument parsing check
        captured_args = []
        orig_parse_args = argparse.ArgumentParser.parse_args

        def intercept_parse_args(parser_self, *args, **kwargs):
            res = orig_parse_args(parser_self, *args, **kwargs)
            captured_args.append(res)
            raise StopIteration("cli_intercepted")

        with unittest.mock.patch("argparse.ArgumentParser.parse_args", intercept_parse_args):
            with pytest.raises(StopIteration, match="cli_intercepted"):
                watcher_main(["--run-once"])

        assert len(captured_args) == 1
        assert captured_args[0].run_once is True

        # 3. Execution check: watcher_main with --run-once exits immediately without starting watcher observer
        db_file = str(tmp_path / "cli_run_once.db")
        portal_file = str(tmp_path / "portal.html")
        mock_advice = {"title": "Test Plan", "summary": "Test Summary", "actions": []}
        with unittest.mock.patch("run_watcher.start_watcher") as mock_watcher, \
             unittest.mock.patch("run_watcher.generate_market_advice", return_value=mock_advice):
            watcher_main([
                "--run-once",
                "--watch-dir", str(tmp_path),
                "--db", db_file,
                "--dashboard-out", portal_file,
                "--realm", "Everlook",
            ])
            mock_watcher.assert_not_called()

    def test_watcher_wow_addon_path_argument(self, tmp_path):
        """Verify --wow-addon-path CLI argument parsing, default None, and auto-export to WoW AddOns directory."""
        import luadata

        # 1. Verify default value is None
        captured_args = []
        orig_parse_args = argparse.ArgumentParser.parse_args

        def intercept_parse_args(parser_self, *args, **kwargs):
            res = orig_parse_args(parser_self, *args, **kwargs)
            captured_args.append(res)
            raise StopIteration("cli_intercepted")

        with unittest.mock.patch("argparse.ArgumentParser.parse_args", intercept_parse_args):
            with pytest.raises(StopIteration, match="cli_intercepted"):
                watcher_main([])

        assert len(captured_args) == 1
        assert captured_args[0].wow_addon_path is None

        # 2. Verify CLI argument parsing with explicit path
        custom_addon_dir = str(tmp_path / "Interface" / "AddOns")
        captured_args.clear()
        with unittest.mock.patch("argparse.ArgumentParser.parse_args", intercept_parse_args):
            with pytest.raises(StopIteration, match="cli_intercepted"):
                watcher_main(["--wow-addon-path", custom_addon_dir])

        assert len(captured_args) == 1
        assert captured_args[0].wow_addon_path == custom_addon_dir

        # 3. Verify --help documentation displays --wow-addon-path
        res_help = subprocess.run(
            [sys.executable, "run_watcher.py", "--help"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert res_help.returncode == 0
        assert "--wow-addon-path" in res_help.stdout

        # 4. Verify end-to-end execution of watcher_main with --run-once and --wow-addon-path exports config.lua
        os.makedirs(custom_addon_dir, exist_ok=True)
        watch_dir = str(tmp_path / "SavedVariables")
        os.makedirs(watch_dir, exist_ok=True)
        db_file = str(tmp_path / "test_watcher.db")
        portal_file = str(tmp_path / "portal.html")

        with unittest.mock.patch("run_watcher.generate_market_advice", return_value={"opportunities": []}), \
             unittest.mock.patch("run_watcher.start_watcher"):
            watcher_main([
                "--run-once",
                "--realm", "Everlook",
                "--watch-dir", watch_dir,
                "--db", db_file,
                "--dashboard-out", portal_file,
                "--wow-addon-path", custom_addon_dir,
            ])

        expected_addon_config = os.path.join(custom_addon_dir, "ForeverTrade_Sniper", "config.lua")
        assert os.path.isfile(expected_addon_config)

        parsed_config = luadata.read(expected_addon_config)
        assert isinstance(parsed_config, dict)
        assert parsed_config.get("version") == 2
        assert "targets" in parsed_config


class TestWowheadFetcherAndResolver:
    """Tests for Wowhead item fetcher, background resolver, and ETL integration."""

    @staticmethod
    def _create_mock_response(content: bytes, status: int = 200):
        class MockResponse:
            def __init__(self, data: bytes, code: int):
                self.data = data
                self.status = code

            def read(self) -> bytes:
                return self.data

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                pass

        return MockResponse(content, status)

    def test_fetch_wowhead_item_xml_parsing(self):
        """Verify fetch_wowhead_item fetches and parses English and Russian XML responses."""
        en_xml = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<wowhead>\n'
            '  <item id="3858">\n'
            '    <name><![CDATA[Mithril Ore]]></name>\n'
            '    <quality id="1">Common</quality>\n'
            '    <class id="7">Trade Goods</class>\n'
            '    <subclass id="7">Metal &amp; Stone</subclass>\n'
            '    <icon>inv_ore_mithril_02</icon>\n'
            '    <jsonEquip><![CDATA["sellprice":250,"buyprice":0]]></jsonEquip>\n'
            '  </item>\n'
            '</wowhead>'
        ).encode("utf-8")

        ru_xml = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<wowhead>\n'
            '  <item id="3858">\n'
            '    <name><![CDATA[Мифриловая руда]]></name>\n'
            '  </item>\n'
            '</wowhead>'
        ).encode("utf-8")

        def mock_urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if "ru.wowhead.com" in url:
                return self._create_mock_response(ru_xml, 200)
            elif "www.wowhead.com" in url:
                return self._create_mock_response(en_xml, 200)
            return self._create_mock_response(b"", 404)

        with unittest.mock.patch("urllib.request.urlopen", side_effect=mock_urlopen):
            res = fetch_wowhead_item(3858)

        assert res is not None
        assert res["item_id"] == 3858
        assert res["name"] == "Mithril Ore"
        assert res["name_ru"] == "Мифриловая руда"
        assert res["quality"] == 1
        assert res["item_class"] == 7
        assert res["item_subclass"] == 7
        assert res["icon"] == "inv_ore_mithril_02"
        assert res["vendor_sell_price"] == 250
        assert res["vendor_sell_copper"] == 250
        assert res["vendor_buy_price"] == 0
        assert res["vendor_buy_copper"] == 0

    def test_fetch_wowhead_item_nether_json_fallback(self):
        """Verify fetch_wowhead_item falls back to Nether tooltip JSON when XML endpoint fails."""
        nether_payload = {
            "name": "Mithril Ore",
            "quality": 1,
            "icon": "inv_ore_mithril_02",
            "tooltip": (
                '<table><tr><td><b>Mithril Ore</b><div class="whtt-sellprice">'
                'Sell Price: <span class="moneysilver">2</span> '
                '<span class="moneycopper">50</span></div></td></tr></table>'
            ),
        }
        nether_bytes = json.dumps(nether_payload).encode("utf-8")

        def mock_urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if "nether.wowhead.com" in url:
                return self._create_mock_response(nether_bytes, 200)
            raise urllib.error.URLError("XML endpoint offline")

        with unittest.mock.patch("urllib.request.urlopen", side_effect=mock_urlopen):
            res = fetch_wowhead_item(3858)

        assert res is not None
        assert res["item_id"] == 3858
        assert res["name"] == "Mithril Ore"
        assert res["quality"] == 1
        assert res["icon"] == "inv_ore_mithril_02"
        assert res["vendor_sell_price"] == 250
        assert res["vendor_sell_copper"] == 250
        assert res["name_ru"] == "Мифриловая руда"

    def test_fetch_wowhead_item_invalid_and_missing(self):
        """Verify fetch_wowhead_item returns None for invalid item IDs or error responses."""
        assert fetch_wowhead_item(-1) is None
        assert fetch_wowhead_item(0) is None
        assert fetch_wowhead_item("invalid") is None

        def mock_error(req, timeout=None):
            return self._create_mock_response(b'<wowhead><error>Item not found</error></wowhead>', 200)

        with unittest.mock.patch("urllib.request.urlopen", side_effect=mock_error):
            res = fetch_wowhead_item(9999999)
            assert res is None

    def test_wowhead_resolver_updates_sqlite_items(self, tmp_path):
        """Verify WowheadResolver background worker updates SQLite items table upon enqueue."""
        db_file = str(tmp_path / "resolver_test.db")
        conn = init_db(db_file)
        with conn:
            conn.execute("INSERT INTO items (item_id, name) VALUES (3858, 'Item 3858')")
        conn.close()

        resolved_item = {
            "item_id": 3858,
            "name": "Mithril Ore",
            "name_ru": "Мифриловая руда",
            "quality": 1,
            "item_class": 7,
            "item_subclass": 7,
            "vendor_sell_price": 250,
            "vendor_sell_copper": 250,
            "vendor_buy_price": 0,
            "vendor_buy_copper": 0,
            "icon": "inv_ore_mithril_02",
        }

        resolver = WowheadResolver(rate_limit_seconds=0.01)
        try:
            with unittest.mock.patch("src.wowhead.fetch_wowhead_item", return_value=resolved_item):
                resolver.enqueue(3858, db_file)

                deadline = time.time() + 5.0
                updated = False
                while time.time() < deadline:
                    with sqlite3.connect(db_file) as check_conn:
                        row = check_conn.execute(
                            "SELECT name, name_ru, vendor_sell_copper, quality FROM items WHERE item_id = 3858"
                        ).fetchone()
                        if row and row[0] == "Mithril Ore":
                            updated = True
                            break
                    time.sleep(0.05)

                assert updated is True
                assert row[0] == "Mithril Ore"
                assert row[1] == "Мифриловая руда"
                assert row[2] == 250
                assert row[3] == 1
        finally:
            resolver.stop(timeout=2.0)

    def test_wowhead_resolver_inserts_unseen_item(self, tmp_path):
        """Verify WowheadResolver inserts a new row if item_id was not yet in items table."""
        db_file = str(tmp_path / "resolver_insert_test.db")
        conn = init_db(db_file)
        conn.close()

        resolved_item = {
            "item_id": 3858,
            "name": "Mithril Ore",
            "name_ru": "Мифриловая руда",
            "quality": 1,
            "item_class": 7,
            "item_subclass": 7,
            "vendor_sell_price": 250,
            "vendor_sell_copper": 250,
            "vendor_buy_price": 0,
            "vendor_buy_copper": 0,
            "icon": "inv_ore_mithril_02",
        }

        resolver = WowheadResolver(rate_limit_seconds=0.01)
        try:
            with unittest.mock.patch("src.wowhead.fetch_wowhead_item", return_value=resolved_item):
                resolver.enqueue(3858, db_file)

                deadline = time.time() + 5.0
                inserted = False
                while time.time() < deadline:
                    with sqlite3.connect(db_file) as check_conn:
                        row = check_conn.execute(
                            "SELECT name, name_ru, vendor_sell_copper FROM items WHERE item_id = 3858"
                        ).fetchone()
                        if row and row[0] == "Mithril Ore":
                            inserted = True
                            break
                    time.sleep(0.05)

                assert inserted is True
                assert row[0] == "Mithril Ore"
                assert row[1] == "Мифриловая руда"
                assert row[2] == 250
        finally:
            resolver.stop(timeout=2.0)

    def test_ingest_price_snapshot_triggers_resolver_for_unresolved_items(self, tmp_path):
        """Verify ingest_price_snapshot automatically enqueues unresolved items into WowheadResolver."""
        db_file = str(tmp_path / "market_resolver_trigger.db")
        conn = init_db(db_file)

        mock_resolver = MagicMock()
        raw_data = {
            "AUCTIONATOR_PRICE_DATABASE": {
                "Everlook": {
                    "3858": {"m": 1200}
                }
            }
        }

        with unittest.mock.patch("src.wowhead.WowheadResolver.get_instance", return_value=mock_resolver):
            count = ingest_price_snapshot(conn, raw_data, "Everlook", db_path=db_file)

        assert count == 1
        mock_resolver.enqueue.assert_called_once_with(3858, db_file)

        cur = conn.cursor()
        cur.execute("SELECT name FROM items WHERE item_id = 3858")
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "Item 3858"
        conn.close()

    def test_ingest_price_snapshot_graceful_without_db_path_or_resolver_failure(self, memory_db):
        """Verify ingest_price_snapshot operates gracefully when resolver raises or db_path is omitted."""
        raw_data = {
            "AUCTIONATOR_PRICE_DATABASE": {
                "Everlook": {
                    "3858": {"m": 1800}
                }
            }
        }

        # 1. Graceful when db_path is None (e.g. in-memory)
        count = ingest_price_snapshot(memory_db, raw_data, "Everlook", db_path=None)
        assert count == 1

        cur = memory_db.cursor()
        cur.execute("SELECT price FROM price_snapshots WHERE item_id = 3858")
        assert cur.fetchone()[0] == 1800

        # 2. Graceful when WowheadResolver raises an exception
        with unittest.mock.patch("src.wowhead.WowheadResolver.get_instance", side_effect=RuntimeError("queue offline")):
            count_fallback = ingest_price_snapshot(
                memory_db,
                {"AUCTIONATOR_PRICE_DATABASE": {"Everlook": {"3859": {"m": 2200}}}},
                "Everlook",
                db_path="/tmp/dummy.db",
                source_mtime=int(time.time()) + 1000,
            )
            assert count_fallback == 1

        cur.execute("SELECT price FROM price_snapshots WHERE item_id = 3859")
        assert cur.fetchone()[0] == 2200


def test_ap_is_ours_identifies_own_server():
    """Verify is_ours from .autopilot/ap.py accurately distinguishes project server from foreign ones (Audit §20)."""
    import importlib.util
    from pathlib import Path

    ap_path = Path(__file__).resolve().parent.parent / ".autopilot" / "ap.py"
    spec = importlib.util.spec_from_file_location("autopilot_ap", str(ap_path))
    assert spec is not None and spec.loader is not None
    ap_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ap_mod)

    assert ap_mod.is_ours("python /other/project/serve_permanent.py") is False
    assert ap_mod.is_ours(f"python {ap_mod._norm(ap_mod.A)}/serve_permanent.py") is True


def test_cbor_encoded_auctionator_price_database_ingestion(tmp_path, memory_db):
    """Verify Auctionator dbversion 8 CBOR-serialized price string is correctly decoded and ingested."""
    # Lua table containing dbversion 8 and CBOR-encoded realm data
    # b'\xa1d2835\xa1am\x11' -> {'2835': {'m': 17}}
    lua_content = (
        'AUCTIONATOR_PRICE_DATABASE = {\n'
        '  ["__dbversion"] = 8,\n'
        '  ["PvP"] = "\\161d2835\\161am\\17"\n'
        '}\n'
    )
    lua_file = tmp_path / "Auctionator.lua"
    lua_file.write_text(lua_content, encoding="utf-8")

    from run_watcher import initial_ingest
    count = initial_ingest(memory_db, str(tmp_path), "PvP")
    assert count == 1

    cur = memory_db.cursor()
    cur.execute("SELECT item_id, realm, price FROM price_snapshots WHERE item_id = 2835")
    row = cur.fetchone()
    assert row is not None
    assert row[0] == 2835
    assert row[1] == "PvP"
    assert row[2] == 17


def test_initial_ingest_selects_account_wide_over_character_savedvariables(tmp_path, memory_db):
    """Verify initial_ingest prefers account-wide SavedVariables over tiny per-character files."""
    # Character file:
    char_dir = tmp_path / "Account" / "123#1" / "PvP" / "MyChar" / "SavedVariables"
    char_dir.mkdir(parents=True)
    char_file = char_dir / "Auctionator.lua"
    char_file.write_text("AUCTIONATOR_CHARACTER_CONFIG = nil\n", encoding="utf-8")

    # Account-wide file:
    acct_dir = tmp_path / "Account" / "123#1" / "SavedVariables"
    acct_dir.mkdir(parents=True)
    acct_file = acct_dir / "Auctionator.lua"
    acct_file.write_text(
        'AUCTIONATOR_PRICE_DATABASE = {\n'
        '  ["PvP"] = {\n'
        '    ["2835"] = { ["m"] = 50 }\n'
        '  }\n'
        '}\n',
        encoding="utf-8",
    )

    from run_watcher import initial_ingest
    count = initial_ingest(memory_db, str(tmp_path), "PvP")
    assert count == 1

    cur = memory_db.cursor()
    cur.execute("SELECT item_id, price FROM price_snapshots WHERE item_id = 2835")
    assert cur.fetchone()[1] == 50


def test_load_profitcrafter_data_mock_fixture(tmp_path):
    """Verify load_profitcrafter_data parses character, professions, recipes with reagents and floats."""
    char_sv_dir = tmp_path / "Account" / "123#1" / "Everlook" / "TestChar" / "SavedVariables"
    char_sv_dir.mkdir(parents=True)
    pc_file = char_sv_dir / "ProfitCrafterForever.lua"
    pc_file.write_text(
        'ProfitCrafterCharDB = {\n'
        '  ["professions"] = {\n'
        '    [171] = {\n'
        '      ["name"] = "Alchemy",\n'
        '      ["rank"] = 150,\n'
        '      ["max"] = 225,\n'
        '    },\n'
        '    [185] = {\n'
        '      ["name"] = "Cooking",\n'
        '      ["rank"] = 75,\n'
        '      ["max"] = 150,\n'
        '    },\n'
        '  },\n'
        '  ["recipes"] = {\n'
        '    [2331] = {\n'
        '      ["name"] = "Elixir of Minor Defense",\n'
        '      ["output"] = 2454,\n'
        '      ["qty"] = 1,\n'
        '      ["prof"] = "Alchemy",\n'
        '      ["cost"] = 14.75,\n'
        '      ["reagents"] = {\n'
        '        { ["id"] = 2447, ["n"] = 2 },\n'
        '        { ["id"] = 2449, ["n"] = 1 },\n'
        '      },\n'
        '    },\n'
        '    [2540] = {\n'
        '      ["name"] = "Herb Baked Egg",\n'
        '      ["output"] = 6888,\n'
        '      ["qty"] = 1,\n'
        '      ["prof"] = "Cooking",\n'
        '      ["profit_margin"] = 0.35,\n'
        '      ["reagents"] = {\n'
        '        { ["id"] = 6889, ["n"] = 1 },\n'
        '        { ["id"] = 769, ["n"] = 1 },\n'
        '      },\n'
        '    },\n'
        '  },\n'
        '}\n',
        encoding="utf-8",
    )

    data = load_profitcrafter_data(str(tmp_path))
    assert isinstance(data, dict)
    assert data.get("character") == "TestChar"

    professions = data.get("professions", {})
    assert 171 in professions
    assert professions[171]["name"] == "Alchemy"
    assert professions[171]["rank"] == 150
    assert professions[171]["max"] == 225

    assert 185 in professions
    assert professions[185]["name"] == "Cooking"
    assert professions[185]["rank"] == 75
    assert professions[185]["max"] == 150

    recipes = data.get("recipes", {})
    assert 2331 in recipes
    r_elixir = recipes[2331]
    assert r_elixir["name"] == "Elixir of Minor Defense"
    assert r_elixir["output"] == 2454
    assert r_elixir["qty"] == 1
    assert r_elixir["prof"] == "Alchemy"
    assert len(r_elixir["reagents"]) == 2
    assert r_elixir["reagents"][0] == {"id": 2447, "n": 2}
    assert r_elixir["reagents"][1] == {"id": 2449, "n": 1}

    assert 2540 in recipes
    r_egg = recipes[2540]
    assert r_egg["name"] == "Herb Baked Egg"
    assert r_egg["output"] == 6888
    assert r_egg["qty"] == 1
    assert r_egg["prof"] == "Cooking"
    assert len(r_egg["reagents"]) == 2
    assert r_egg["reagents"][0] == {"id": 6889, "n": 1}
    assert r_egg["reagents"][1] == {"id": 769, "n": 1}


def test_load_profitcrafter_data_nonexistent_path(tmp_path):
    """Verify load_profitcrafter_data returns empty dict for nonexistent paths."""
    with unittest.mock.patch.dict(os.environ, {"WOW_WTF_PATH": ""}):
        orig_exists = os.path.exists

        def fake_exists(p):
            if str(p) == ".env":
                return False
            return orig_exists(p)

        with unittest.mock.patch("os.path.exists", side_effect=fake_exists):
            res_dir = load_profitcrafter_data(str(tmp_path / "nonexistent_directory_12345"))
            assert res_dir == {}

            res_file = load_profitcrafter_data(str(tmp_path / "nonexistent_file.lua"))
            assert res_file == {}


def test_load_profitcrafter_data_live_wow_wtf_path():
    """Verify load_profitcrafter_data functions cleanly against live WOW_WTF_PATH if configured."""
    env_wtf = os.getenv("WOW_WTF_PATH")
    if env_wtf and os.path.isdir(env_wtf):
        res = load_profitcrafter_data(env_wtf)
        assert isinstance(res, dict)
        if res:
            assert "character" in res
            assert "professions" in res
            assert "recipes" in res

    # Calling with None or default searches data/ or fallback without crashing
    default_res = load_profitcrafter_data(None)
    assert isinstance(default_res, dict)


