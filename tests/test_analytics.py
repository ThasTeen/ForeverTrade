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
    get_waylaid_crate_arbitrage,
    VENDOR_REAGENTS_WHITELIST,
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
    # Item 1001: Crafted potion, AH price = 200 copper, vendor_sell = 10 -> deposit = 15.
    # Item 1002: Vendor reagent, vendor_buy_price = 100 copper (requires 1)
    #
    # With Bartering:
    #   Cost = 100 * 0.95 = 95.0
    #   Revenue = 200 * 0.95 = 190.0
    #   Deposit = 15.0
    #   Profit = 190.0 - 95.0 - 15.0 = 80.0
    #   ROI = 80.0 / 95.0 = 0.8421 (84.21% >= 25%) -> INCLUDED
    #
    # Without Bartering:
    #   Cost = 100 * 1.0 = 100.0
    #   Revenue = 190.0
    #   Deposit = 15.0
    #   Profit = 190.0 - 100.0 - 15.0 = 75.0
    #   ROI = 75.0 / 100.0 = 0.75 (75% >= 25%) -> INCLUDED
    #
    # Now let's tune a second item where bartering makes the difference between ROI >= 25% and ROI < 25%:
    # Item 1003: Crafted potion, AH price = 145 copper, vendor_sell = 10 -> deposit = 15.
    # Item 1004: Vendor reagent, vendor_buy_price = 100 copper.
    # Revenue = 145 * 0.95 = 137.75 copper.
    # With Bartering: Cost = 95.0, Deposit = 15. Profit = 137.75 - 95 - 15 = 27.75.
    #   ROI = 27.75 / 95 = 0.2921 (29.21% >= 25%) -> INCLUDED.
    # Without Bartering: Cost = 100.0, Deposit = 15. Profit = 137.75 - 100 - 15 = 22.75.
    #   ROI = 22.75 / 100 = 0.2275 (22.75% < 25%) -> EXCLUDED.
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
                (1003, "Everlook", 145, now),
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
    assert recipe_b_barter["revenue"] == 137.75
    assert recipe_b_barter["deposit"] == 15
    assert recipe_b_barter["profit"] == 27.75
    assert recipe_b_barter["roi"] == 0.2921
    assert recipe_b_barter["roi_percent"] == 29.21

    # 2. Without bartering
    results_no_barter = get_crafting_margins(memory_db, has_bartering=False, min_roi=0.25)
    no_barter_ids = [r["recipe_id"] for r in results_no_barter]
    assert 1 in no_barter_ids
    assert 2 not in no_barter_ids  # Excluded because ROI = 22.75% < 25%

    recipe_a_no_barter = next(r for r in results_no_barter if r["recipe_id"] == 1)
    assert recipe_a_no_barter["cost"] == 100.0
    assert recipe_a_no_barter["revenue"] == 190.0
    assert recipe_a_no_barter["deposit"] == 15
    assert recipe_a_no_barter["profit"] == 75.0
    assert recipe_a_no_barter["roi"] == 0.75


def test_crafting_margins_master_chef_cooking_multiplier(memory_db):
    """Verify 1.1x multiplier applies to Cooking with Master Chef, but not to other professions."""
    # Item 2001: Cooked Food, AH price = 120 copper, vendor_sell = 10 -> deposit = 15.
    # Reagent 2002: AH price = 40 copper, count = 2. Total cost = 80 copper.
    # Base yield = 1.0.
    #
    # With Master Chef (Cooking):
    #   Expected yield = 1.1 * 1.0 = 1.1
    #   Revenue = 120 * 0.95 * 1.1 = 125.4 copper.
    #   Profit = 125.4 - 80 - 15 = 30.4 copper.
    #   ROI = 30.4 / 80 = 0.38 (38.0% >= 25%) -> INCLUDED
    #
    # Without Master Chef (Cooking):
    #   Expected yield = 1.0 * 1.0 = 1.0
    #   Revenue = 120 * 0.95 * 1.0 = 114.0 copper.
    #   Profit = 114.0 - 80 - 15 = 19.0 copper.
    #   ROI = 19.0 / 80 = 0.2375 (23.75% < 25%) -> EXCLUDED
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
                (2001, "Everlook", 120, now),
                (2002, "Everlook", 40, now),
                (2003, "Everlook", 120, now),
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
    # Alchemy recipe does NOT get 1.1x multiplier, so ROI is 23.75% < 25% -> EXCLUDED
    assert 20 not in rec_ids

    cooking_res = next(r for r in res_master_chef if r["recipe_id"] == 10)
    assert cooking_res["yield"] == 1.1
    assert cooking_res["expected_yield"] == 1.1
    assert cooking_res["revenue"] == 125.4
    assert cooking_res["deposit"] == 15
    assert cooking_res["profit"] == 30.4
    assert cooking_res["roi"] == 0.38
    assert cooking_res["roi_percent"] == 38.0

    # 2. Without Master Chef
    res_no_chef = get_crafting_margins(memory_db, has_master_chef=False, min_roi=0.25)
    no_chef_ids = [r["recipe_id"] for r in res_no_chef]
    # Both fail the 25% ROI threshold
    assert 10 not in no_chef_ids
    assert 20 not in no_chef_ids


def test_crafting_margins_ah_commission_and_filtering(memory_db):
    """Verify 5% auction commission deduction, AH deposit fee, negative profit filtering, and sorting."""
    # Item 3001: P_out = 100, cost = 95. Revenue = 95. Deposit = 15. Profit = 95 - 95 - 15 = -15 -> EXCLUDED
    # Item 3002: P_out = 200, cost = 140. Revenue = 190. Deposit = 15. Profit = 190 - 140 - 15 = 35, ROI = 25.0% -> INCLUDED
    # Item 3003: P_out = 300, cost = 100. Revenue = 285. Deposit = 15. Profit = 285 - 100 - 15 = 170, ROI = 170.0% -> INCLUDED
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
                (3004, "Everlook", 95, now),
                (3002, "Everlook", 200, now),
                (3005, "Everlook", 140, now),
                (3003, "Everlook", 300, now),
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
    assert r33["roi"] == 1.7
    assert r33["profit"] == 170.0

    r32 = results[1]
    assert r32["recipe_id"] == 32
    assert r32["roi"] == 0.25
    assert r32["profit"] == 35.0


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
    """Verify mean, unbiased sample standard deviation (ddof=1), and STRONG_BUY / STRONG_SELL signals."""
    # We construct exact mathematical distributions for 10 snapshots (N >= 6, ddof=1):
    #
    # Item 501: Dump / Crash
    # 9 snapshots at 100, newest snapshot (rn=1) at 10:
    # prices = [10, 100, 100, 100, 100, 100, 100, 100, 100, 100]
    # N = 10, sum = 910, mu = 91.0
    # var = ((10-91)^2 + 9*(100-91)^2) / (10 - 1) = (6561 + 729) / 9 = 810.0
    # sigma = sqrt(810) = 28.4604989... -> 28.4605
    # Z = (10 - 91) / 28.4604989... = -81 / 28.4605 = -2.846
    # Since Z = -2.846 <= -1.8 -> signal = "STRONG_BUY"
    #
    # Item 502: Spike / Pump
    # 9 snapshots at 10, newest snapshot (rn=1) at 100:
    # prices = [100, 10, 10, 10, 10, 10, 10, 10, 10, 10]
    # N = 10, sum = 190, mu = 19.0
    # var = ((100-19)^2 + 9*(10-19)^2) / 9 = (6561 + 729) / 9 = 810.0
    # sigma = sqrt(810) = 28.4605
    # Z = (100 - 19) / 28.4605 = +81 / 28.4605 = +2.846
    # Since Z = +2.846 >= 1.6 -> signal = "STRONG_SELL"
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
    assert buy_entry["std_dev"] == 28.4605
    assert buy_entry["z_score"] == -2.846
    assert buy_entry["sample_count"] == 10

    sell_entry = results[1]
    assert sell_entry["item_id"] == 502
    assert sell_entry["signal"] == "STRONG_SELL"
    assert sell_entry["current_price"] == 100
    assert sell_entry["mean_price"] == 19.0
    assert sell_entry["std_dev"] == 28.4605
    assert sell_entry["z_score"] == 2.846
    assert sell_entry["sample_count"] == 10


def test_zscore_anomalies_zero_variance_protection(memory_db):
    """Verify sigma = 0 protection: no ZeroDivisionError and Z is set to 0.0 without crash."""
    with memory_db:
        memory_db.execute(
            "INSERT INTO items (item_id, name, quality, vendor_sell_price, vendor_buy_price) VALUES (?, ?, ?, ?, ?)",
            (601, "Flat Price Item", 1, 10, 0)
        )
        # 6 snapshots with the exact same price (reaches min_samples=6)
        memory_db.executemany(
            "INSERT INTO price_snapshots (item_id, realm, price, timestamp) VALUES (?, ?, ?, ?)",
            [
                (601, "Everlook", 500, 1000 + i) for i in range(6)
            ]
        )

    # Execution must NOT raise ZeroDivisionError
    results = get_zscore_anomalies(memory_db, window_size=10)
    # Since Z = 0.0, it is between -1.8 and 1.6, so no signal is generated
    assert len(results) == 0


def test_zscore_anomalies_window_size_and_minimum_samples(memory_db):
    """Verify window_size limits history and min_samples requirement."""
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
        # Item 701: only 1 snapshot (< min_samples must be skipped)
        memory_db.execute(
            "INSERT INTO price_snapshots (item_id, realm, price, timestamp) VALUES (?, ?, ?, ?)",
            (701, "Everlook", 10, base_time)
        )

        # Item 702: insert 15 snapshots:
        # 12 older snapshots (t=1000..1011) with price 100
        # 3 newest snapshots (t=1012..1014) with price 10
        #
        # If window_size = 3:
        #   Only latest 3 snapshots are selected (< min_samples=6) -> skipped, len = 0.
        #
        # If window_size = 15:
        #   All 15 snapshots used (12 of 100, 3 of 10):
        #   N = 15, sum = 1230, mu = 82.0
        #   var (ddof=1) = (12*(18^2) + 3*(-72^2)) / 14 = (3888 + 15552) / 14 = 1388.5714...
        #   sigma = sqrt(1388.5714) = 37.2635...
        #   Z = (10 - 82) / 37.2635 = -72 / 37.2635 = -1.9322 <= -1.8 -> STRONG_BUY!
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
    # Window size 3: only 3 snapshots (< 6 samples) -> no signal
    results_w3 = get_zscore_anomalies(memory_db, window_size=3)
    assert len(results_w3) == 0

    # Window size 15: includes 12 older prices of 100, producing Z = -1.9322
    results_w15 = get_zscore_anomalies(memory_db, window_size=15)
    assert len(results_w15) == 1
    assert results_w15[0]["item_id"] == 702
    assert results_w15[0]["signal"] == "STRONG_BUY"
    assert results_w15[0]["mean_price"] == 82.0
    assert results_w15[0]["std_dev"] == 37.2635
    assert results_w15[0]["z_score"] == -1.9322
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


# ---------------------------------------------------------------------------
# Task 08 (R29, R30, R31, R34) Specific Math, Reagents & Crate Tests
# ---------------------------------------------------------------------------

def test_zscore_samuelson_min_samples_and_realm_isolation(memory_db):
    """Verify Samuelson's inequality protection (< 6 snapshots -> empty) and realm isolation (R29)."""
    base_time = 50000
    with memory_db:
        memory_db.executemany(
            """INSERT INTO items (item_id, name, quality, vendor_sell_price, vendor_buy_price)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (901, "Samuelson Test Item", 1, 10, 0),
                (902, "Multi-Realm Item", 1, 10, 0),
            ]
        )

        # 1. Test Samuelson protection on 901 with 3, 4, 5 snapshots
        # 3 snapshots: 2 at 100, newest at 10
        memory_db.executemany(
            "INSERT INTO price_snapshots (item_id, realm, price, timestamp) VALUES (?, ?, ?, ?)",
            [
                (901, "Everlook", 100, base_time),
                (901, "Everlook", 100, base_time + 1),
                (901, "Everlook", 10, base_time + 10),
            ]
        )

    # 3 snapshots (< 6) -> list of anomalies must be strictly empty (Samuelson violation prevented)
    assert get_zscore_anomalies(memory_db, realm="Everlook") == []

    # 4 snapshots (< 6) -> still empty
    with memory_db:
        memory_db.execute(
            "INSERT INTO price_snapshots (item_id, realm, price, timestamp) VALUES (?, ?, ?, ?)",
            (901, "Everlook", 100, base_time + 2)
        )
    assert get_zscore_anomalies(memory_db, realm="Everlook") == []

    # 5 snapshots (< 6) -> still empty
    with memory_db:
        memory_db.execute(
            "INSERT INTO price_snapshots (item_id, realm, price, timestamp) VALUES (?, ?, ?, ?)",
            (901, "Everlook", 100, base_time + 3)
        )
    assert get_zscore_anomalies(memory_db, realm="Everlook") == []

    # 6 snapshots (>= 6) -> valid sample size, triggers anomaly using unbiased sample variance (ddof=1)
    # 5 older snapshots at 100 (t=base_time..base_time+4), 1 newest snapshot at 10 (t=base_time+10)
    with memory_db:
        memory_db.execute(
            "INSERT INTO price_snapshots (item_id, realm, price, timestamp) VALUES (?, ?, ?, ?)",
            (901, "Everlook", 100, base_time + 4)
        )

    results_6 = get_zscore_anomalies(memory_db, realm="Everlook")
    assert len(results_6) == 1
    anom = results_6[0]
    assert anom["item_id"] == 901
    assert anom["signal"] == "STRONG_BUY"
    assert anom["sample_count"] == 6
    assert anom["current_price"] == 10
    # N=6: mu = (5*100 + 10)/6 = 85.0
    assert anom["mean_price"] == 85.0
    # var (ddof=1) = (5*(15^2) + (-75)^2) / 5 = (1125 + 5625) / 5 = 1350.0
    # sigma = sqrt(1350) = 36.7423...
    assert anom["std_dev"] == 36.7423
    # Z = (10 - 85) / 36.742346... = -2.04124...
    assert anom["z_score"] == -2.0412

    # 2. Realm isolation test:
    # Item 902 has crash on Everlook (6 snapshots: 5 of 100, newest 10)
    # Item 902 has completely flat stable prices on Warsong (8 snapshots of 500)
    with memory_db:
        everlook_snaps = [(902, "Everlook", 100, base_time + i) for i in range(5)]
        everlook_snaps.append((902, "Everlook", 10, base_time + 10))
        warsong_snaps = [(902, "Warsong", 500, base_time + i) for i in range(8)]
        memory_db.executemany(
            "INSERT INTO price_snapshots (item_id, realm, price, timestamp) VALUES (?, ?, ?, ?)",
            everlook_snaps + warsong_snaps
        )

    # Everlook query: only detects anomalies on Everlook, stats not skewed by Warsong
    ev_anoms = get_zscore_anomalies(memory_db, realm="Everlook")
    ev_item_902 = next((a for a in ev_anoms if a["item_id"] == 902), None)
    assert ev_item_902 is not None
    assert ev_item_902["realm"] == "Everlook"
    assert ev_item_902["mean_price"] == 85.0
    assert ev_item_902["std_dev"] == 36.7423
    assert ev_item_902["current_price"] == 10

    # Warsong query: stable prices produce 0 anomalies
    ws_anoms = get_zscore_anomalies(memory_db, realm="Warsong")
    assert not any(a["item_id"] == 902 for a in ws_anoms)


def test_crafting_margins_vendor_whitelist_vs_wild_resources(memory_db):
    """Verify strict whitelist: wild fish and herbs with vendor_buy_price > 0 use AH price, not NPC buy (R30)."""
    now = int(time.time())
    with memory_db:
        memory_db.executemany(
            """INSERT INTO items (item_id, name, quality, vendor_sell_price, vendor_buy_price)
               VALUES (?, ?, ?, ?, ?)""",
            [
                # Wild fish & herbs (have vendor_buy_price in some DB dumps, but are NOT in whitelist)
                (13759, "Raw Nightfin Snapper", 1, 100, 450),
                (13464, "Golden Sansam", 1, 50, 250),
                # Genuine vendor materials (present in VENDOR_REAGENTS_WHITELIST)
                (2320, "Coarse Thread", 1, 5, 100),
                (3371, "Empty Vial", 1, 2, 50),
                # Crafted products
                (13931, "Nightfin Soup", 1, 200, 0),
                (13444, "Major Healing Potion", 1, 200, 0),
            ]
        )
        memory_db.executemany(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            [
                # Raw resources have high AH prices
                (13759, "Everlook", 5000, now),
                (13464, "Everlook", 3000, now),
                # Crafted items sell on AH
                (13931, "Everlook", 10000, now),
                (13444, "Everlook", 8000, now),
                # Note: vendor items 2320 and 3371 have NO AH snapshots!
            ]
        )
        memory_db.executemany(
            """INSERT INTO recipes (recipe_id, name, profession, crafted_item_id, yield)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (501, "Nightfin Soup Recipe", "Cooking", 13931, 1.0),
                (502, "Major Healing Potion Recipe", "Alchemy", 13444, 1.0),
            ]
        )
        memory_db.executemany(
            """INSERT INTO recipe_reagents (recipe_id, reagent_item_id, reagent_id, count, quantity)
               VALUES (?, ?, ?, ?, ?)""",
            [
                # 501: 1x Raw Nightfin Snapper + 1x Coarse Thread
                (501, 13759, 13759, 1, 1),
                (501, 2320, 2320, 1, 1),
                # 502: 1x Golden Sansam + 1x Empty Vial
                (502, 13464, 13464, 1, 1),
                (502, 3371, 3371, 1, 1),
            ]
        )

    results = get_crafting_margins(memory_db, has_bartering=True, has_master_chef=False)
    assert len(results) == 2

    # Verify Recipe 501 (Cooking with wild fish)
    r501 = next(r for r in results if r["recipe_id"] == 501)
    # Expected cost: 13759 is AH price 5000 (NOT 450*0.95=427.5), 2320 is vendor price 100*0.95=95.0
    # Total cost = 5000 + 95 = 5095.0
    assert r501["cost"] == 5095.0
    reagents_501 = {rg["item_id"]: rg for rg in r501["reagents"]}
    assert reagents_501[13759]["is_vendor"] is False
    assert reagents_501[13759]["unit_cost"] == 5000.0
    assert reagents_501[2320]["is_vendor"] is True
    assert reagents_501[2320]["unit_cost"] == 95.0

    # Verify Recipe 502 (Alchemy with wild herb)
    r502 = next(r for r in results if r["recipe_id"] == 502)
    # Expected cost: 13464 is AH price 3000 (NOT 250*0.95=237.5), 3371 is vendor price 50*0.95=47.5
    # Total cost = 3000 + 47.5 = 3047.5
    assert r502["cost"] == 3047.5
    reagents_502 = {rg["item_id"]: rg for rg in r502["reagents"]}
    assert reagents_502[13464]["is_vendor"] is False
    assert reagents_502[13464]["unit_cost"] == 3000.0
    assert reagents_502[3371]["is_vendor"] is True
    assert reagents_502[3371]["unit_cost"] == 47.5


def test_crafting_margins_ah_deposit_fee(memory_db):
    """Verify AH deposit fee deduction: max(15, int(v_sell * 0.05)) (R31)."""
    now = int(time.time())
    with memory_db:
        memory_db.executemany(
            """INSERT INTO items (item_id, name, quality, vendor_sell_price, vendor_buy_price)
               VALUES (?, ?, ?, ?, ?)""",
            [
                # Product 6001: vendor_sell = 100 -> 5% = 5 < 15 -> deposit is 15 copper
                (6001, "Low Vendor Sell Product", 1, 100, 0),
                (6002, "Reagent A", 1, 5, 0),
                # Product 6003: vendor_sell = 2000 -> 5% = 100 > 15 -> deposit is 100 copper
                (6003, "High Vendor Sell Product", 1, 2000, 0),
                (6004, "Reagent B", 1, 5, 0),
            ]
        )
        memory_db.executemany(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            [
                # AH prices
                (6001, "Everlook", 500, now),
                (6002, "Everlook", 200, now),
                (6003, "Everlook", 2000, now),
                (6004, "Everlook", 800, now),
            ]
        )
        memory_db.executemany(
            """INSERT INTO recipes (recipe_id, name, profession, crafted_item_id, yield)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (601, "Low Sell Recipe", "Tailoring", 6001, 1.0),
                (602, "High Sell Recipe", "Tailoring", 6003, 1.0),
            ]
        )
        memory_db.executemany(
            """INSERT INTO recipe_reagents (recipe_id, reagent_item_id, reagent_id, count, quantity)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (601, 6002, 6002, 1, 1),
                (602, 6004, 6004, 1, 1),
            ]
        )

    # 1. With AH deposit fee enabled (default)
    res_with = get_crafting_margins(memory_db, include_ah_deposit=True)
    r601_with = next(r for r in res_with if r["recipe_id"] == 601)
    # Revenue = 500 * 0.95 = 475.0, Cost = 200.0, Deposit = max(15, int(100*0.05)) = 15
    # Profit = 475 - 200 - 15 = 260.0
    assert r601_with["deposit"] == 15
    assert r601_with["deposit_copper"] == 15
    assert r601_with["profit"] == 260.0

    r602_with = next(r for r in res_with if r["recipe_id"] == 602)
    # Revenue = 2000 * 0.95 = 1900.0, Cost = 800.0, Deposit = max(15, int(2000*0.05)) = 100
    # Profit = 1900 - 800 - 100 = 1000.0
    assert r602_with["deposit"] == 100
    assert r602_with["deposit_copper"] == 100
    assert r602_with["profit"] == 1000.0

    # 2. With AH deposit fee disabled
    res_without = get_crafting_margins(memory_db, include_ah_deposit=False)
    r601_no = next(r for r in res_without if r["recipe_id"] == 601)
    assert r601_no["deposit"] == 0
    assert r601_no["profit"] == 275.0
    assert r601_no["profit"] - r601_with["profit"] == 15.0

    r602_no = next(r for r in res_without if r["recipe_id"] == 602)
    assert r602_no["deposit"] == 0
    assert r602_no["profit"] == 1100.0
    assert r602_no["profit"] - r602_with["profit"] == 100.0


def test_waylaid_crates_economy_and_raw_resources(memory_db):
    """Verify Waylaid Crates model: target_item_count, reward_copper, and raw-resource crates 211501-211507 (R34)."""
    now = int(time.time())
    with memory_db:
        memory_db.executemany(
            """INSERT INTO items (
                   item_id, name, quality, vendor_sell_price, vendor_buy_price,
                   target_item_id, target_item_count, reward_copper, category
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                # 1. Crafted item and its Waylaid Crate
                (7001, "Target Heavy Belt", 1, 50, 0, None, 0, 0, None),
                (7002, "Heavy Leather", 1, 10, 0, None, 0, 0, None),
                (7010, "Waylaid Shipment: Heavy Belts", 2, 0, 0, 7001, 5, 12000, "waylaid_crate"),

                # 2. Raw-resource item and its crate (211501: Copper Bars, no crafting recipe)
                (2840, "Copper Bar", 1, 10, 0, None, 0, 0, None),
                (211501, "Waylaid Supplies: Copper Bars", 1, 100, 0, 2840, 20, 500, "waylaid_crate"),
            ]
        )
        memory_db.executemany(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            [
                # Crafted belt AH price = 1000, reagent = 400
                (7001, "Everlook", 1000, now),
                (7002, "Everlook", 400, now),
                # Raw Copper Bar AH price = 15
                (2840, "Everlook", 15, now),
            ]
        )
        memory_db.executemany(
            """INSERT INTO recipes (recipe_id, name, profession, crafted_item_id, yield)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (701, "Heavy Belt Recipe", "Leatherworking", 7001, 1.0),
                # Notice: NO recipe in DB for Copper Bar (2840) or crate 211501!
            ]
        )
        memory_db.executemany(
            """INSERT INTO recipe_reagents (recipe_id, reagent_item_id, reagent_id, count, quantity)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (701, 7002, 7002, 1, 1),
            ]
        )

    results = get_crafting_margins(memory_db, min_roi=0.25)

    # 1. Verify crafted item crate (7010)
    r_crafted = next((r for r in results if r["recipe_id"] == 701), None)
    assert r_crafted is not None
    assert r_crafted["is_waylaid_target"] is True
    assert r_crafted["waylaid_crate_id"] == 7010
    assert r_crafted["target_item_count"] == 5
    assert r_crafted["reward_copper"] == 12000
    # Pack cost: 5 * 1000 = 5000 (100% of AH buyout price p_out)
    assert r_crafted["pack_cost"] == 5000.0
    # Pack revenue on AH: 5 * 1000 * 0.95 = 4750
    assert r_crafted["pack_revenue"] == 4750.0
    # Turn-in profit vs AH: 12000 - 5000 = 7000
    assert r_crafted["turnin_profit"] == 7000.0
    assert r_crafted["waylaid_crate"]["turnin_profit"] == 7000.0

    # 2. Verify raw-resource crate (211501)
    r_raw = next((r for r in results if r.get("waylaid_crate_id") == 211501), None)
    assert r_raw is not None
    assert r_raw["recipe_id"] is None
    assert r_raw["profession"] == "Gathering"
    assert r_raw["crafted_item_id"] == 2840
    assert r_raw["target_item_count"] == 20
    assert r_raw["reward_copper"] == 500
    # Pack cost: 20 * 15 = 300
    assert r_raw["pack_cost"] == 300.0
    # Pack revenue on AH: 20 * 15 * 0.95 = 285
    assert r_raw["pack_revenue"] == 285.0
    # Turn-in profit vs AH: 500 - 300 = 200 (100% pack_cost deduction, not pack_revenue)
    assert r_raw["turnin_profit"] == 200.0
    assert r_raw["is_waylaid_target"] is True
    # Reagent details
    assert len(r_raw["reagents"]) == 1
    assert r_raw["reagents"][0]["item_id"] == 2840
    assert r_raw["reagents"][0]["count"] == 20
    assert r_raw["reagents"][0]["is_vendor"] is False

    # 3. Verify get_waylaid_crate_arbitrage helper
    crate_arb = get_waylaid_crate_arbitrage(memory_db)
    crate_ids = {c["waylaid_crate_id"] for c in crate_arb}
    assert 7010 in crate_ids
    assert 211501 in crate_ids


# ---------------------------------------------------------------------------
# Task 15 (R46, R47, R49, R50) Specific Math, Crate & Z-Score Tests
# ---------------------------------------------------------------------------

def test_waylaid_crates_audit_case_loss_excluded_no_fake_profit(memory_db):
    """Reproduce audit case: 20 items @ 21 copper = 420 copper pack cost, NPC reward = 400 copper.

    Net loss = 400 - 420 = -20 copper.
    Under buggy math (pack_revenue = 420 * 0.95 = 399), 400 - 399 produced +1 copper fake profit.
    Verify crate is NOT returned as a profitable turn-in in get_crafting_margins or get_waylaid_crate_arbitrage (R46, R47).
    """
    now = int(time.time())
    with memory_db:
        memory_db.executemany(
            """INSERT INTO items (
                   item_id, name, quality, vendor_sell_price, vendor_buy_price,
                   target_item_id, target_item_count, reward_copper, category
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                # Raw resource item and its loss crate (420 cost vs 400 reward -> -20 loss)
                (8101, "Audit Test Herb", 1, 10, 0, None, 0, 0, None),
                (8110, "Waylaid Supplies: Audit Loss Crate", 1, 100, 0, 8101, 20, 400, "waylaid_crate"),
                # Control item and its profitable crate (420 cost vs 500 reward -> +80 profit)
                (8102, "Audit Control Herb", 1, 10, 0, None, 0, 0, None),
                (8120, "Waylaid Supplies: Audit Profitable Crate", 1, 100, 0, 8102, 20, 500, "waylaid_crate"),
            ]
        )
        memory_db.executemany(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            [
                # Both items cost 21 copper per unit on AH (20 * 21 = 420 copper pack cost)
                (8101, "Everlook", 21, now),
                (8102, "Everlook", 21, now),
            ]
        )

    # 1. get_crafting_margins: crate 8110 must NOT appear even with min_roi=-999
    margins = get_crafting_margins(memory_db, min_roi=-999.0)
    crate_ids_in_margins = {
        m.get("waylaid_crate_id") for m in margins if m.get("is_waylaid_target")
    }
    assert 8110 not in crate_ids_in_margins, "Loss crate 8110 must not be returned with fake +1 profit"
    assert 8120 in crate_ids_in_margins, "Profitable crate 8120 must be included"

    # 2. get_waylaid_crate_arbitrage: crate 8110 must NOT appear
    crate_arb = get_waylaid_crate_arbitrage(memory_db)
    arb_crate_ids = {c["waylaid_crate_id"] for c in crate_arb}
    assert 8110 not in arb_crate_ids
    assert 8120 in arb_crate_ids

    # 3. Verify exact math on profitable crate 8120
    c8120 = next(c for c in crate_arb if c["waylaid_crate_id"] == 8120)
    assert c8120["pack_cost"] == 420.0  # 100% cost: 20 * 21
    assert c8120["pack_revenue"] == 399.0  # 20 * 21 * 0.95
    assert c8120["turnin_profit"] == 80.0  # 500 - 420 = +80 copper


def test_zscore_breakeven_strong_buy_audit_case(memory_db):
    """Reproduce audit case: prices [100, 100, 100, 100, 100, 96].

    Statistical Z = -2.04 <= -1.8, but net proceeds after 5% AH fee and deposit yield a net loss:
      expected_revenue = 99.33 * 0.95 = 94.37 copper
      deposit = max(15, int(v_sell * 0.05)) = 15 copper
      net_profit = 94.37 - 96 - 15 = -16.63 copper (loss!)
    Ensure STRONG_BUY signal is strictly suppressed (R49).
    Also verify deep discount [100, 100, 100, 100, 100, 50] with net profit > 0 triggers STRONG_BUY.
    """
    base_time = 10000
    with memory_db:
        memory_db.executemany(
            """INSERT INTO items (item_id, name, quality, vendor_sell_price, vendor_buy_price)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (8201, "Unprofitable Dip Item", 1, 10, 0),
                (8202, "Profitable Dip Item", 1, 10, 0),
            ]
        )

        snapshots = []
        # Item 8201: [100, 100, 100, 100, 100, 96]
        # 5 older snapshots at 100, newest at 96
        for i in range(5):
            snapshots.append((8201, "Everlook", 100, base_time + i))
        snapshots.append((8201, "Everlook", 96, base_time + 10))

        # Item 8202: [100, 100, 100, 100, 100, 50]
        # 5 older snapshots at 100, newest at 50
        for i in range(5):
            snapshots.append((8202, "Everlook", 100, base_time + i))
        snapshots.append((8202, "Everlook", 50, base_time + 10))

        memory_db.executemany(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            snapshots
        )

    results = get_zscore_anomalies(
        memory_db, window_size=15, z_buy_threshold=-1.8, min_profit_copper=1
    )

    # Item 8201 must NOT have any STRONG_BUY signal despite Z <= -1.8
    item_8201_signals = [r for r in results if r["item_id"] == 8201]
    assert len(item_8201_signals) == 0, (
        "Item 8201 has negative net profit (-16.63 copper); STRONG_BUY must NOT be emitted"
    )

    # Item 8202 must trigger STRONG_BUY
    item_8202_signals = [r for r in results if r["item_id"] == 8202]
    assert len(item_8202_signals) == 1
    sig_8202 = item_8202_signals[0]
    assert sig_8202["signal"] == "STRONG_BUY"
    assert sig_8202["current_price"] == 50
    assert sig_8202["mean_price"] == 91.67
    assert sig_8202["z_score"] == -2.0412
    assert sig_8202["expected_revenue"] == 87.08
    assert sig_8202["deposit"] == 15
    assert sig_8202["expected_net_profit"] == 22.08
    assert sig_8202["expected_profit"] == 22.08
    assert sig_8202["expected_roi"] == 0.4417


def test_crafting_margins_perk_defaults_and_activation(memory_db):
    """Verify R50: default perks are False (has_bartering=False, has_master_chef=False)

    and explicit perk flags activate 5% vendor discount and 1.1x Cooking yield multiplier.
    """
    now = int(time.time())
    with memory_db:
        memory_db.executemany(
            """INSERT INTO items (item_id, name, quality, vendor_sell_price, vendor_buy_price)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (8301, "Special Roast", 1, 10, 0),        # Crafted item, vendor sell = 10 -> deposit = 15
                (8302, "Fresh Meat", 1, 5, 0),            # AH reagent
                (2678, "Hot Spices", 1, 2, 100),          # Whitelisted vendor reagent, v_buy = 100
            ]
        )
        memory_db.executemany(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            [
                (8301, "Everlook", 240, now),
                (8302, "Everlook", 100, now),
            ]
        )
        memory_db.execute(
            """INSERT INTO recipes (recipe_id, name, profession, crafted_item_id, yield)
               VALUES (?, ?, ?, ?, ?)""",
            (830, "Special Roast Recipe", "Cooking", 8301, 1.0)
        )
        memory_db.executemany(
            """INSERT INTO recipe_reagents (recipe_id, reagent_item_id, reagent_id, count, quantity)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (830, 8302, 8302, 1, 1),
                (830, 2678, 2678, 1, 1),
            ]
        )

    # 1. Default invocation (no perk arguments):
    # has_bartering defaults to False -> Hot Spices cost = 100.0, total cost = 100 + 100 = 200.0
    # has_master_chef defaults to False -> yield = 1.0, revenue = 240 * 0.95 = 228.0
    # deposit = 15, profit = 228 - 200 - 15 = 13.0, ROI = 13 / 200 = 0.065
    res_default = get_crafting_margins(memory_db, min_roi=0.0)
    assert len(res_default) == 1
    rd = res_default[0]
    assert rd["recipe_id"] == 830
    assert rd["cost"] == 200.0
    assert rd["yield"] == 1.0
    assert rd["revenue"] == 228.0
    assert rd["deposit"] == 15
    assert rd["profit"] == 13.0
    assert rd["roi"] == 0.065

    # 2. Explicit activation: has_bartering=True, has_master_chef=True
    # has_bartering=True -> Hot Spices cost = 100 * 0.95 = 95.0, total cost = 100 + 95 = 195.0
    # has_master_chef=True -> yield = 1.1, revenue = 240 * 0.95 * 1.1 = 250.8
    # deposit = 15, profit = 250.8 - 195 - 15 = 40.8, ROI = 40.8 / 195 = 0.2092
    res_perks = get_crafting_margins(memory_db, has_bartering=True, has_master_chef=True, min_roi=0.0)
    assert len(res_perks) == 1
    rp = res_perks[0]
    assert rp["recipe_id"] == 830
    assert rp["cost"] == 195.0
    assert rp["yield"] == 1.1
    assert rp["revenue"] == 250.8
    assert rp["deposit"] == 15
    assert rp["profit"] == 40.8
    assert rp["roi"] == 0.2092


def test_analytics_max_age_hours_ttl_filtering(memory_db):
    """Verify max_age_hours filters out stale snapshots across all analytics functions."""
    now = int(time.time())
    ts_fresh = now - 3600          # 1 hour ago
    ts_stale = now - (30 * 3600)   # 30 hours ago

    with memory_db:
        memory_db.executemany(
            """INSERT INTO items (
                   item_id, name, quality, vendor_sell_price, vendor_buy_price,
                   target_item_id, target_item_count, reward_copper, category
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                # For vendor arbitrage
                (8401, "Fresh Arb Item", 1, 200, 0, None, 0, 0, None),
                (8402, "Stale Arb Item", 1, 200, 0, None, 0, 0, None),
                # For crafting margins & crates
                (8403, "Fresh Craft Product", 1, 10, 0, None, 0, 0, None),
                (8404, "Fresh Reagent", 1, 5, 0, None, 0, 0, None),
                (8405, "Stale Craft Product", 1, 10, 0, None, 0, 0, None),
                (8406, "Stale Reagent", 1, 5, 0, None, 0, 0, None),
                # Raw crates
                (8410, "Waylaid Supplies: Fresh Product", 1, 100, 0, 8403, 10, 5000, "waylaid_crate"),
                (8420, "Waylaid Supplies: Stale Product", 1, 100, 0, 8405, 10, 5000, "waylaid_crate"),
                # For Z-score
                (8407, "Stale Z-score Item", 1, 10, 0, None, 0, 0, None),
            ]
        )
        memory_db.executemany(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            [
                # Arb snapshots
                (8401, "Everlook", 100, ts_fresh),
                (8402, "Everlook", 100, ts_stale),
                # Crafting snapshots
                (8403, "Everlook", 300, ts_fresh),
                (8404, "Everlook", 50, ts_fresh),
                (8405, "Everlook", 300, ts_stale),
                (8406, "Everlook", 50, ts_stale),
                # Z-score: 6 stale snapshots
                (8407, "Everlook", 100, ts_stale - 50),
                (8407, "Everlook", 100, ts_stale - 40),
                (8407, "Everlook", 100, ts_stale - 30),
                (8407, "Everlook", 100, ts_stale - 20),
                (8407, "Everlook", 100, ts_stale - 10),
                (8407, "Everlook", 10, ts_stale),
            ]
        )
        memory_db.executemany(
            """INSERT INTO recipes (recipe_id, name, profession, crafted_item_id, yield)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (841, "Fresh Recipe", "Alchemy", 8403, 1.0),
                (842, "Stale Recipe", "Alchemy", 8405, 1.0),
            ]
        )
        memory_db.executemany(
            """INSERT INTO recipe_reagents (recipe_id, reagent_item_id, reagent_id, count, quantity)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (841, 8404, 8404, 1, 1),
                (842, 8406, 8406, 1, 1),
            ]
        )

    # 1. get_vendor_arbitrage: max_age_hours=24 excludes stale 8402
    arb_ttl = get_vendor_arbitrage(memory_db, min_profit_copper=50, max_age_hours=24)
    arb_ttl_ids = {r["item_id"] for r in arb_ttl}
    assert 8401 in arb_ttl_ids
    assert 8402 not in arb_ttl_ids

    # Without max_age_hours: both fresh and stale are returned
    arb_all = get_vendor_arbitrage(memory_db, min_profit_copper=50, max_age_hours=None)
    arb_all_ids = {r["item_id"] for r in arb_all}
    assert 8401 in arb_all_ids
    assert 8402 in arb_all_ids

    # 2. get_crafting_margins: max_age_hours=24 excludes stale recipe 842
    margins_ttl = get_crafting_margins(memory_db, min_roi=0.25, max_age_hours=24)
    recipe_ids_ttl = {m["recipe_id"] for m in margins_ttl if m["recipe_id"] is not None}
    assert 841 in recipe_ids_ttl
    assert 842 not in recipe_ids_ttl

    # 3. get_waylaid_crate_arbitrage: max_age_hours=24 excludes stale crate 8420
    crates_ttl = get_waylaid_crate_arbitrage(memory_db, max_age_hours=24)
    crate_ids_ttl = {c["waylaid_crate_id"] for c in crates_ttl}
    assert 8410 in crate_ids_ttl
    assert 8420 not in crate_ids_ttl

    # 4. get_zscore_anomalies: max_age_hours=24 excludes stale 8407 (0 fresh samples < min_samples=6)
    anoms_ttl = get_zscore_anomalies(memory_db, max_age_hours=24)
    anom_ids_ttl = {a["item_id"] for a in anoms_ttl}
    assert 8407 not in anom_ids_ttl

    # Without TTL: 8407 has 6 snapshots with a deep price drop (10 vs 100) -> emitted
    anoms_all = get_zscore_anomalies(memory_db, max_age_hours=None)
    anom_ids_all = {a["item_id"] for a in anoms_all}
    assert 8407 in anom_ids_all
