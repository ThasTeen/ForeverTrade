"""Tests for src.analytics module under Ponytail doctrine.

Verifies real functional execution of:
- get_vendor_arbitrage (R09)
- get_crafting_margins (R10)
- get_zscore_anomalies (R11)
No paper tests (no source reading / regex); executes actual SQLite database transactions and math.
"""

import math
import sqlite3
import time
import pytest

from src.database import init_db, load_catalog, load_recipes
from src.analytics import (
    get_vendor_arbitrage,
    get_crafting_margins,
    get_zscore_anomalies,
)


@pytest.fixture
def memory_db():
    """Provide a clean in-memory database with schema initialized."""
    conn = init_db(":memory:")
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# R09: Vendor Arbitrage Tests
# ---------------------------------------------------------------------------

def test_vendor_arbitrage_basic_and_threshold(memory_db):
    """Verify vendor arbitrage finds lots where buyout < vendor_sell and profit >= 50 copper."""
    # Setup test items:
    # Item 1: buyout 100, vendor sell 200 -> profit 100 (>= 50) -> INCLUDED
    # Item 2: buyout 100, vendor sell 150 -> profit 50 (== 50) -> INCLUDED
    # Item 3: buyout 100, vendor sell 149 -> profit 49 (< 50) -> EXCLUDED
    # Item 4: buyout 100, vendor sell 100 -> profit 0 -> EXCLUDED
    # Item 5: buyout 120, vendor sell 100 -> profit -20 -> EXCLUDED
    now = int(time.time())
    with memory_db:
        memory_db.executemany(
            """INSERT INTO items (item_id, name, quality, vendor_sell_price, vendor_buy_price)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (101, "Profitable Item 1", 1, 200, 0),
                (102, "Exact Threshold Item", 1, 150, 0),
                (103, "Sub-threshold Item", 1, 149, 0),
                (104, "Break-even Item", 1, 100, 0),
                (105, "Loss Item", 1, 100, 0),
            ]
        )
        memory_db.executemany(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            [
                (101, "Everlook", 100, now),
                (102, "Everlook", 100, now),
                (103, "Everlook", 100, now),
                (104, "Everlook", 100, now),
                (105, "Everlook", 120, now),
            ]
        )

    results = get_vendor_arbitrage(memory_db, min_profit_copper=50)

    assert len(results) == 2
    item_ids = [r["item_id"] for r in results]
    assert item_ids == [101, 102]

    # Verify math and fields
    res_101 = results[0]
    assert res_101["item_id"] == 101
    assert res_101["buyout_price"] == 100
    assert res_101["vendor_sell_price"] == 200
    assert res_101["profit_copper"] == 100
    assert res_101["profit"] == 100
    assert res_101["realm"] == "Everlook"

    res_102 = results[1]
    assert res_102["item_id"] == 102
    assert res_102["profit_copper"] == 50


def test_vendor_arbitrage_sorting_descending(memory_db):
    """Verify vendor arbitrage results are strictly sorted by profit descending."""
    now = int(time.time())
    with memory_db:
        memory_db.executemany(
            """INSERT INTO items (item_id, name, quality, vendor_sell_price, vendor_buy_price)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (201, "Low Profit", 1, 160, 0),   # profit 60
                (202, "High Profit", 1, 500, 0),  # profit 400
                (203, "Med Profit", 1, 300, 0),   # profit 200
            ]
        )
        memory_db.executemany(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            [
                (201, "Everlook", 100, now),
                (202, "Everlook", 100, now),
                (203, "Everlook", 100, now),
            ]
        )

    results = get_vendor_arbitrage(memory_db, min_profit_copper=50)
    assert len(results) == 3
    profits = [r["profit_copper"] for r in results]
    assert profits == [400, 200, 60]
    assert results[0]["item_id"] == 202
    assert results[1]["item_id"] == 203
    assert results[2]["item_id"] == 201


def test_vendor_arbitrage_latest_snapshot_and_realm_filter(memory_db):
    """Verify only the latest snapshot is evaluated per item, and realm filtering works."""
    t1 = 1000
    t2 = 2000
    with memory_db:
        memory_db.executemany(
            """INSERT INTO items (item_id, name, quality, vendor_sell_price, vendor_buy_price)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (301, "Old Cheap New Expensive", 1, 200, 0),
                (302, "Old Expensive New Cheap", 1, 200, 0),
                (303, "Other Realm Profitable", 1, 200, 0),
            ]
        )
        memory_db.executemany(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            [
                # Item 301: t1=100 (profit 100), t2=180 (profit 20 < 50) -> latest is 180, EXCLUDED
                (301, "Everlook", 100, t1),
                (301, "Everlook", 180, t2),
                # Item 302: t1=180 (profit 20), t2=100 (profit 100 >= 50) -> latest is 100, INCLUDED
                (302, "Everlook", 180, t1),
                (302, "Everlook", 100, t2),
                # Item 303: on Warsong realm with profit 100
                (303, "Warsong", 100, t2),
            ]
        )

    # Filter by realm Everlook
    results_everlook = get_vendor_arbitrage(memory_db, min_profit_copper=50, realm="Everlook")
    assert len(results_everlook) == 1
    assert results_everlook[0]["item_id"] == 302
    assert results_everlook[0]["profit_copper"] == 100
    assert results_everlook[0]["timestamp"] == t2

    # Filter by realm Warsong
    results_warsong = get_vendor_arbitrage(memory_db, min_profit_copper=50, realm="Warsong")
    assert len(results_warsong) == 1
    assert results_warsong[0]["item_id"] == 303

    # All realms combined
    results_all = get_vendor_arbitrage(memory_db, min_profit_copper=50, realm=None)
    all_item_ids = {r["item_id"] for r in results_all}
    assert 302 in all_item_ids
    assert 303 in all_item_ids
    assert 301 not in all_item_ids


# ---------------------------------------------------------------------------
# R10: Crafting Margins Tests
# ---------------------------------------------------------------------------

def test_crafting_margins_bartering_discount(memory_db):
    """Verify 5% Bartering discount on vendor components (V_buy * 0.95 vs V_buy * 1.0)."""
    # Item 1001: Crafted potion, AH price = 200 copper
    # Item 1002: Vendor reagent, vendor_buy_price = 100 copper (requires 1)
    #
    # With Bartering:
    #   Cost = 100 * 0.95 = 95.0
    #   Revenue = 200 * 0.95 = 190.0
    #   Profit = 190.0 - 95.0 = 95.0
    #   ROI = 95.0 / 95.0 = 1.0 (100% >= 25%) -> INCLUDED
    #
    # Without Bartering:
    #   Cost = 100 * 1.0 = 100.0
    #   Revenue = 190.0
    #   Profit = 190.0 - 100.0 = 90.0
    #   ROI = 90.0 / 100.0 = 0.9 (90% >= 25%) -> INCLUDED
    #
    # Now let's tune a second item where bartering makes the difference between ROI >= 25% and ROI < 25%:
    # Item 1003: Crafted potion, AH price = 130 copper.
    # Item 1004: Vendor reagent, vendor_buy_price = 100 copper.
    # Revenue = 130 * 0.95 = 123.5 copper.
    # With Bartering: Cost = 95.0. Profit = 123.5 - 95 = 28.5. ROI = 28.5 / 95 = 0.30 (30% >= 25%) -> INCLUDED.
    # Without Bartering: Cost = 100.0. Profit = 123.5 - 100 = 23.5. ROI = 23.5 / 100 = 0.235 (23.5% < 25%) -> EXCLUDED.
    now = int(time.time())
    with memory_db:
        memory_db.executemany(
            """INSERT INTO items (item_id, name, quality, vendor_sell_price, vendor_buy_price)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (1001, "Alchemy Potion", 1, 10, 0),
                (1002, "Vendor Reagent A", 1, 5, 100),
                (1003, "Marginal Potion", 1, 10, 0),
                (1004, "Vendor Reagent B", 1, 5, 100),
            ]
        )
        memory_db.executemany(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            [
                (1001, "Everlook", 200, now),
                (1003, "Everlook", 130, now),
            ]
        )
        memory_db.executemany(
            """INSERT INTO recipes (recipe_id, name, profession, crafted_item_id, yield)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (1, "Recipe A", "Alchemy", 1001, 1.0),
                (2, "Recipe B", "Alchemy", 1003, 1.0),
            ]
        )
        memory_db.executemany(
            """INSERT INTO recipe_reagents (recipe_id, reagent_item_id, reagent_id, count, quantity)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (1, 1002, 1002, 1, 1),
                (2, 1004, 1004, 1, 1),
            ]
        )

    # 1. With bartering (default)
    results_barter = get_crafting_margins(memory_db, has_bartering=True, min_roi=0.25)
    barter_ids = [r["recipe_id"] for r in results_barter]
    assert 1 in barter_ids
    assert 2 in barter_ids

    recipe_b_barter = next(r for r in results_barter if r["recipe_id"] == 2)
    assert recipe_b_barter["cost"] == 95.0
    assert recipe_b_barter["revenue"] == 123.5
    assert recipe_b_barter["profit"] == 28.5
    assert recipe_b_barter["roi"] == 0.3
    assert recipe_b_barter["roi_percent"] == 30.0

    # 2. Without bartering
    results_no_barter = get_crafting_margins(memory_db, has_bartering=False, min_roi=0.25)
    no_barter_ids = [r["recipe_id"] for r in results_no_barter]
    assert 1 in no_barter_ids
    assert 2 not in no_barter_ids  # Excluded because ROI = 23.5% < 25%

    recipe_a_no_barter = next(r for r in results_no_barter if r["recipe_id"] == 1)
    assert recipe_a_no_barter["cost"] == 100.0
    assert recipe_a_no_barter["revenue"] == 190.0
    assert recipe_a_no_barter["profit"] == 90.0
    assert recipe_a_no_barter["roi"] == 0.9


def test_crafting_margins_master_chef_cooking_multiplier(memory_db):
    """Verify 1.1x multiplier applies to Cooking with Master Chef, but not to other professions."""
    # Item 2001: Cooked Food, AH price = 100 copper.
    # Reagent 2002: AH price = 40 copper, count = 2. Total cost = 80 copper.
    # Base yield = 1.0.
    #
    # With Master Chef (Cooking):
    #   Expected yield = 1.1 * 1.0 = 1.1
    #   Revenue = 100 * 0.95 * 1.1 = 104.5 copper.
    #   Profit = 104.5 - 80 = 24.5 copper.
    #   ROI = 24.5 / 80 = 0.30625 (30.63% >= 25%) -> INCLUDED
    #
    # Without Master Chef (Cooking):
    #   Expected yield = 1.0 * 1.0 = 1.0
    #   Revenue = 100 * 0.95 * 1.0 = 95.0 copper.
    #   Profit = 95.0 - 80 = 15.0 copper.
    #   ROI = 15.0 / 80 = 0.1875 (18.75% < 25%) -> EXCLUDED
    #
    # Control case: Alchemy recipe with same numbers.
    # Even if has_master_chef=True, Master Chef MUST NOT apply to Alchemy!
    now = int(time.time())
    with memory_db:
        memory_db.executemany(
            """INSERT INTO items (item_id, name, quality, vendor_sell_price, vendor_buy_price)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (2001, "Grilled Squid", 1, 10, 0),
                (2002, "Raw Squid", 1, 5, 0),
                (2003, "Alchemy Elixir", 1, 10, 0),
                (2004, "Elixir Herb", 1, 5, 0),
            ]
        )
        memory_db.executemany(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            [
                (2001, "Everlook", 100, now),
                (2002, "Everlook", 40, now),
                (2003, "Everlook", 100, now),
                (2004, "Everlook", 40, now),
            ]
        )
        memory_db.executemany(
            """INSERT INTO recipes (recipe_id, name, profession, crafted_item_id, yield)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (10, "Grilled Squid Recipe", "Cooking", 2001, 1.0),
                (20, "Elixir Recipe", "Alchemy", 2003, 1.0),
            ]
        )
        memory_db.executemany(
            """INSERT INTO recipe_reagents (recipe_id, reagent_item_id, reagent_id, count, quantity)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (10, 2002, 2002, 2, 2),
                (20, 2004, 2004, 2, 2),
            ]
        )

    # 1. With Master Chef
    res_master_chef = get_crafting_margins(memory_db, has_master_chef=True, min_roi=0.25)
    rec_ids = [r["recipe_id"] for r in res_master_chef]
    # Cooking recipe meets >= 25% thanks to 1.1x multiplier
    assert 10 in rec_ids
    # Alchemy recipe does NOT get 1.1x multiplier, so ROI is 18.75% < 25% -> EXCLUDED
    assert 20 not in rec_ids

    cooking_res = next(r for r in res_master_chef if r["recipe_id"] == 10)
    assert cooking_res["yield"] == 1.1
    assert cooking_res["expected_yield"] == 1.1
    assert cooking_res["revenue"] == 104.5
    assert cooking_res["profit"] == 24.5
    assert cooking_res["roi"] == 0.3062 or pytest.approx(cooking_res["roi"], 0.001) == 0.30625

    # 2. Without Master Chef
    res_no_chef = get_crafting_margins(memory_db, has_master_chef=False, min_roi=0.25)
    no_chef_ids = [r["recipe_id"] for r in res_no_chef]
    # Both fail the 25% ROI threshold
    assert 10 not in no_chef_ids
    assert 20 not in no_chef_ids


def test_crafting_margins_ah_commission_and_filtering(memory_db):
    """Verify 5% auction commission deduction, negative profit filtering, and sorting."""
    # Item 3001: P_out = 100, cost = 96. Revenue = 95. Profit = -1 -> EXCLUDED
    # Item 3002: P_out = 100, cost = 76. Revenue = 95. Profit = 19, ROI = 25.0% -> INCLUDED
    # Item 3003: P_out = 200, cost = 100. Revenue = 190. Profit = 90, ROI = 90.0% -> INCLUDED
    now = int(time.time())
    with memory_db:
        memory_db.executemany(
            """INSERT INTO items (item_id, name, quality, vendor_sell_price, vendor_buy_price)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (3001, "Loss Product", 1, 10, 0),
                (3002, "Threshold Product", 1, 10, 0),
                (3003, "High Profit Product", 1, 10, 0),
                (3004, "Reagent 1", 1, 5, 0),
                (3005, "Reagent 2", 1, 5, 0),
                (3006, "Reagent 3", 1, 5, 0),
            ]
        )
        memory_db.executemany(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            [
                (3001, "Everlook", 100, now),
                (3004, "Everlook", 96, now),
                (3002, "Everlook", 100, now),
                (3005, "Everlook", 76, now),
                (3003, "Everlook", 200, now),
                (3006, "Everlook", 100, now),
            ]
        )
        memory_db.executemany(
            """INSERT INTO recipes (recipe_id, name, profession, crafted_item_id, yield)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (31, "Loss Recipe", "Tailoring", 3001, 1.0),
                (32, "Threshold Recipe", "Tailoring", 3002, 1.0),
                (33, "High Profit Recipe", "Tailoring", 3003, 1.0),
            ]
        )
        memory_db.executemany(
            """INSERT INTO recipe_reagents (recipe_id, reagent_item_id, reagent_id, count, quantity)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (31, 3004, 3004, 1, 1),
                (32, 3005, 3005, 1, 1),
                (33, 3006, 3006, 1, 1),
            ]
        )

    results = get_crafting_margins(memory_db, min_roi=0.25)
    rec_ids = [r["recipe_id"] for r in results]
    assert 31 not in rec_ids
    assert rec_ids == [33, 32]  # Sorted by ROI / profit descending

    r33 = results[0]
    assert r33["recipe_id"] == 33
    assert r33["roi"] == 0.9

    r32 = results[1]
    assert r32["recipe_id"] == 32
    assert r32["roi"] == 0.25


def test_crafting_margins_missing_reagents_and_waylaid_crates(memory_db):
    """Verify recipes with missing reagent prices are skipped, and Waylaid Crate metadata is populated."""
    now = int(time.time())
    with memory_db:
        memory_db.executemany(
            """INSERT INTO items (
                   item_id, name, quality, vendor_sell_price, vendor_buy_price,
                   target_item_id, target_item_count, reward_copper, category
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                # Crafted item 4001 is target of Waylaid Crate 4010
                (4001, "Target Elixir", 1, 10, 0, None, 0, 0, None),
                (4002, "Existing Reagent", 1, 5, 0, None, 0, 0, None),
                # Waylaid Crate item
                (4010, "Waylaid Shipment: Target Elixirs", 2, 0, 0, 4001, 10, 50000, "waylaid_crate"),
                # Crafted item 4020 has a reagent with NO price and NO vendor buy price
                (4020, "Uncraftable Item", 1, 10, 0, None, 0, 0, None),
                (4021, "Missing Reagent", 1, 5, 0, None, 0, 0, None),
            ]
        )
        memory_db.executemany(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            [
                (4001, "Everlook", 300, now),
                (4002, "Everlook", 100, now),
                (4020, "Everlook", 300, now),
                # Notice: item 4021 has NO snapshot and vendor_buy_price is 0!
            ]
        )
        memory_db.executemany(
            """INSERT INTO recipes (recipe_id, name, profession, crafted_item_id, yield)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (41, "Waylaid Target Recipe", "Alchemy", 4001, 1.0),
                (42, "Missing Reagent Recipe", "Alchemy", 4020, 1.0),
            ]
        )
        memory_db.executemany(
            """INSERT INTO recipe_reagents (recipe_id, reagent_item_id, reagent_id, count, quantity)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (41, 4002, 4002, 1, 1),
                (42, 4021, 4021, 1, 1),
            ]
        )

    results = get_crafting_margins(memory_db, min_roi=0.25)
    rec_ids = [r["recipe_id"] for r in results]
    assert 42 not in rec_ids  # Missing reagent skipped cleanly
    assert 41 in rec_ids

    r41 = next(r for r in results if r["recipe_id"] == 41)
    assert r41["is_waylaid_target"] is True
    assert r41["waylaid_crate_name"] == "Waylaid Shipment: Target Elixirs"
    assert r41["waylaid_crate_id"] == 4010
    assert r41["waylaid_crate"]["reward_copper"] == 50000


# ---------------------------------------------------------------------------
# R11: Statistical Sniping (Z-score) Tests
# ---------------------------------------------------------------------------

def test_zscore_anomalies_mean_sigma_and_signals(memory_db):
    """Verify mean, standard deviation, and STRONG_BUY (Z <= -1.8) / STRONG_SELL (Z >= 1.6) signals."""
    # We construct exact mathematical distributions for 10 snapshots:
    #
    # Item 501: Dump / Crash
    # 9 snapshots at 100, newest snapshot (rn=1) at 10:
    # prices = [10, 100, 100, 100, 100, 100, 100, 100, 100, 100]
    # N = 10, sum = 910, mu = 91.0
    # var = ((10-91)^2 + 9*(100-91)^2) / 10 = (6561 + 729) / 10 = 729.0
    # sigma = sqrt(729) = 27.0
    # Z = (10 - 91) / 27 = -81 / 27 = -3.0
    # Since Z = -3.0 <= -1.8 -> signal = "STRONG_BUY"
    #
    # Item 502: Spike / Pump
    # 9 snapshots at 10, newest snapshot (rn=1) at 100:
    # prices = [100, 10, 10, 10, 10, 10, 10, 10, 10, 10]
    # N = 10, sum = 190, mu = 19.0
    # var = ((100-19)^2 + 9*(10-19)^2) / 10 = (6561 + 729) / 10 = 729.0
    # sigma = sqrt(729) = 27.0
    # Z = (100 - 19) / 27 = +81 / 27 = +3.0
    # Since Z = +3.0 >= 1.6 -> signal = "STRONG_SELL"
    #
    # Item 503: Normal price oscillation (no signal)
    # prices around 100: [101, 100, 99, 100, 101, 100, 99, 100, 101, 100]
    # |Z| < 1.0 -> no signal
    with memory_db:
        memory_db.executemany(
            """INSERT INTO items (item_id, name, quality, vendor_sell_price, vendor_buy_price)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (501, "Dump Herb", 1, 10, 0),
                (502, "Spike Ore", 1, 10, 0),
                (503, "Normal Cloth", 1, 10, 0),
            ]
        )

        snapshots = []
        base_time = 10000

        # Item 501: rn=1 is at t=10009 with price 10, earlier t=10000..10008 price 100
        for i in range(9):
            snapshots.append((501, "Everlook", 100, base_time + i))
        snapshots.append((501, "Everlook", 10, base_time + 9))

        # Item 502: rn=1 is at t=10009 with price 100, earlier t=10000..10008 price 10
        for i in range(9):
            snapshots.append((502, "Everlook", 10, base_time + i))
        snapshots.append((502, "Everlook", 100, base_time + 9))

        # Item 503: steady prices
        for i in range(10):
            snapshots.append((503, "Everlook", 100 + (i % 2), base_time + i))

        memory_db.executemany(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            snapshots
        )

    results = get_zscore_anomalies(memory_db, window_size=15, z_buy_threshold=-1.8, z_sell_threshold=1.6)

    # 503 must produce no anomaly
    assert len(results) == 2
    item_ids = [r["item_id"] for r in results]
    assert item_ids == [501, 502]  # STRONG_BUY sorted before STRONG_SELL

    buy_entry = results[0]
    assert buy_entry["item_id"] == 501
    assert buy_entry["signal"] == "STRONG_BUY"
    assert buy_entry["current_price"] == 10
    assert buy_entry["mean_price"] == 91.0
    assert buy_entry["std_dev"] == 27.0
    assert buy_entry["z_score"] == -3.0
    assert buy_entry["sample_count"] == 10

    sell_entry = results[1]
    assert sell_entry["item_id"] == 502
    assert sell_entry["signal"] == "STRONG_SELL"
    assert sell_entry["current_price"] == 100
    assert sell_entry["mean_price"] == 19.0
    assert sell_entry["std_dev"] == 27.0
    assert sell_entry["z_score"] == 3.0
    assert sell_entry["sample_count"] == 10


def test_zscore_anomalies_zero_variance_protection(memory_db):
    """Verify sigma = 0 protection: no ZeroDivisionError and Z is set to 0.0 without crash."""
    with memory_db:
        memory_db.execute(
            "INSERT INTO items (item_id, name, quality, vendor_sell_price, vendor_buy_price) VALUES (?, ?, ?, ?, ?)",
            (601, "Flat Price Item", 1, 10, 0)
        )
        # 5 snapshots with the exact same price
        memory_db.executemany(
            "INSERT INTO price_snapshots (item_id, realm, price, timestamp) VALUES (?, ?, ?, ?)",
            [
                (601, "Everlook", 500, 1000 + i) for i in range(5)
            ]
        )

    # Execution must NOT raise ZeroDivisionError
    results = get_zscore_anomalies(memory_db, window_size=10)
    # Since Z = 0.0, it is between -1.8 and 1.6, so no signal is generated
    assert len(results) == 0


def test_zscore_anomalies_window_size_and_minimum_samples(memory_db):
    """Verify window_size limits history and items with < 2 snapshots are excluded."""
    base_time = 1000
    with memory_db:
        memory_db.executemany(
            """INSERT INTO items (item_id, name, quality, vendor_sell_price, vendor_buy_price)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (701, "Single Snapshot Item", 1, 10, 0),
                (702, "Window Tested Item", 1, 10, 0),
            ]
        )
        # Item 701: only 1 snapshot (< 2 samples must be skipped)
        memory_db.execute(
            "INSERT INTO price_snapshots (item_id, realm, price, timestamp) VALUES (?, ?, ?, ?)",
            (701, "Everlook", 10, base_time)
        )

        # Item 702: insert 15 snapshots:
        # 12 older snapshots (t=1000..1011) with price 100
        # 3 newest snapshots (t=1012..1014) with price 10
        #
        # If window_size = 3:
        #   Only latest 3 snapshots (all price 10) are used:
        #   mu = 10, sigma = 0, Z = 0.0 -> no anomaly.
        #
        # If window_size = 15:
        #   All 15 snapshots used (12 of 100, 3 of 10):
        #   N = 15, sum = 1230, mu = 82.0
        #   var = (12*(18^2) + 3*(-72^2)) / 15 = (3888 + 15552) / 15 = 1296.0
        #   sigma = sqrt(1296) = 36.0
        #   Z = (10 - 82) / 36 = -72 / 36 = -2.0 <= -1.8 -> STRONG_BUY!
        snapshots_702 = []
        for i in range(12):
            snapshots_702.append((702, "Everlook", 100, base_time + i))
        for i in range(3):
            snapshots_702.append((702, "Everlook", 10, base_time + 12 + i))

        memory_db.executemany(
            "INSERT INTO price_snapshots (item_id, realm, price, timestamp) VALUES (?, ?, ?, ?)",
            snapshots_702
        )

    # Item 701 with 1 snapshot is skipped in both cases
    # Window size 3: latest 3 snapshots are all 10, sigma = 0, no signal
    results_w3 = get_zscore_anomalies(memory_db, window_size=3)
    assert len(results_w3) == 0

    # Window size 15: includes 12 older prices of 100, producing Z = -2.0
    results_w15 = get_zscore_anomalies(memory_db, window_size=15)
    assert len(results_w15) == 1
    assert results_w15[0]["item_id"] == 702
    assert results_w15[0]["signal"] == "STRONG_BUY"
    assert results_w15[0]["mean_price"] == 82.0
    assert results_w15[0]["std_dev"] == 36.0
    assert results_w15[0]["z_score"] == -2.0
    assert results_w15[0]["sample_count"] == 15


def test_analytics_with_real_catalog_and_recipes(memory_db):
    """Integration test: load real Classic Era catalog + recipes and verify analytics queries run cleanly."""
    load_catalog(memory_db)
    load_recipes(memory_db)

    now = int(time.time())

    # Copper Bar is item_id = 2840, vendor sell is 10 copper
    # If buyout is 5 copper, profit is 5 copper (< 50 copper), so excluded by default
    with memory_db:
        memory_db.execute(
            "INSERT INTO price_snapshots (item_id, realm, price, timestamp) VALUES (?, ?, ?, ?)",
            (2840, "Everlook", 5, now)
        )

    # Default threshold 50: Copper Bar excluded
    arb_default = get_vendor_arbitrage(memory_db, min_profit_copper=50)
    assert not any(r["item_id"] == 2840 for r in arb_default)

    # Lower threshold 5: Copper Bar included
    arb_low = get_vendor_arbitrage(memory_db, min_profit_copper=5)
    copper_res = next((r for r in arb_low if r["item_id"] == 2840), None)
    assert copper_res is not None
    assert copper_res["profit_copper"] == 5

    # Run get_crafting_margins with real catalog
    # Since only Copper Bar has a snapshot, most recipes will be skipped without error
    margins = get_crafting_margins(memory_db)
    assert isinstance(margins, list)

    # Run get_zscore_anomalies with real catalog
    anomalies = get_zscore_anomalies(memory_db)
    assert isinstance(anomalies, list)
