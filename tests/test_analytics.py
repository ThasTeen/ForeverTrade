"""Tests for src.analytics module under Ponytail doctrine.

Verifies real functional execution of:
- get_vendor_arbitrage (R09)
- get_crafting_margins (R10)
- get_zscore_anomalies (R11)
No paper tests (no source reading / regex); executes actual SQLite database transactions and math.
"""

import json
import math
import os
import sqlite3
import time
import pytest
import luadata

from src.database import init_db, load_catalog, load_recipes
from src.analytics import (
    get_vendor_arbitrage,
    get_vendor_shuffle,
    get_crafting_margins,
    get_zscore_anomalies,
    get_waylaid_crate_arbitrage,
    check_and_reload_overrides,
    reload_overrides,
    load_disenchant_tables,
    compute_liquidity_score,
    get_strong_buys,
    VENDOR_REAGENTS_WHITELIST,
    calculate_disenchant_value,
    export_sniper_addon_config,
    _CATALOG_CACHE,
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


def test_crafting_margins_real_graph_smelting_and_gear(memory_db):
    """Verify crafting margins calculations on the real Classic Era 1.15 recipe graph and sub-50 gear."""
    load_catalog(memory_db)
    load_recipes(memory_db)

    now = int(time.time())

    # Snapshot prices for:
    # 1. Smelt Bronze (recipe 2659, yield 2.0):
    #    Copper Bar (2840): 50 copper
    #    Tin Bar (3576): 70 copper
    #    Bronze Bar (2841): 150 copper
    # 2. Rough Bronze Cuirass (recipe 2670, yield 1.0):
    #    Bronze Bar (2841): 150 copper (7 bars needed)
    #    Rough Bronze Cuirass (2866): 2500 copper
    # 3. Nightscape Shoulders (recipe 10516, yield 1.0):
    #    Thick Leather (4304): 200 copper (8 needed)
    #    Mageweave Cloth (4338): 150 copper (6 needed)
    #    Silken Thread (4291): vendor reagent, vendor_buy_price = 500 copper (3 needed)
    #    Nightscape Shoulders (8192): 5500 copper
    snapshots = [
        (2840, "Everlook", 50, now),
        (3576, "Everlook", 70, now),
        (2841, "Everlook", 150, now),
        (2866, "Everlook", 2500, now),
        (4304, "Everlook", 200, now),
        (4338, "Everlook", 150, now),
        (8192, "Everlook", 5500, now),
    ]
    memory_db.executemany(
        "INSERT INTO price_snapshots (item_id, realm, price, timestamp) VALUES (?, ?, ?, ?)",
        snapshots,
    )

    # 1. Baseline without character perks (default)
    results = get_crafting_margins(
        memory_db,
        realm="Everlook",
        min_roi=0.1,
        has_bartering=False,
        has_master_chef=False,
    )
    by_recipe = {r["recipe_id"]: r for r in results}

    # Verify Smelt Bronze
    assert 2659 in by_recipe
    smelt = by_recipe[2659]
    assert smelt["name"] == "Smelt Bronze"
    assert smelt["profession"] == "Smelting"
    assert smelt["crafted_item_id"] == 2841
    assert smelt["yield"] == 2.0
    assert smelt["expected_yield"] == 2.0
    # Cost: 1x Copper Bar (50) + 1x Tin Bar (70) = 120.0
    assert smelt["cost"] == 120.0
    # Revenue: 150 * 0.95 * 2.0 = 285.0
    assert smelt["revenue"] == 285.0
    # Deposit: Bronze Bar v_sell is 100, 5% is 5 < 15 -> deposit = 15
    assert smelt["deposit"] == 15
    # Profit: 285.0 - 120.0 - 15 = 150.0
    assert smelt["profit"] == 150.0
    assert smelt["roi"] == 1.25
    assert smelt["roi_percent"] == 125.0

    # Verify Rough Bronze Cuirass
    assert 2670 in by_recipe
    cuirass = by_recipe[2670]
    assert cuirass["name"] == "Rough Bronze Cuirass"
    assert cuirass["profession"] == "Blacksmithing"
    assert cuirass["crafted_item_id"] == 2866
    assert cuirass["yield"] == 1.0
    # Cost: 7x Bronze Bar (150) = 1050.0
    assert cuirass["cost"] == 1050.0
    # Revenue: 2500 * 0.95 * 1.0 = 2375.0
    assert cuirass["revenue"] == 2375.0
    # Deposit: Cuirass v_sell is 603, 5% is 30.15 -> deposit = 30
    assert cuirass["deposit"] == 30
    # Profit: 2375.0 - 1050.0 - 30 = 1295.0
    assert cuirass["profit"] == 1295.0
    assert cuirass["roi"] == 1.2333
    assert cuirass["roi_percent"] == 123.33

    # Verify Nightscape Shoulders
    assert 10516 in by_recipe
    shoulders = by_recipe[10516]
    assert shoulders["name"] == "Nightscape Shoulders"
    assert shoulders["profession"] == "Leatherworking"
    assert shoulders["crafted_item_id"] == 8192
    assert shoulders["yield"] == 1.0
    # Cost: 8x Thick Leather (200) + 6x Mageweave (150) + 3x Silken Thread (500 vendor price) = 1600 + 900 + 1500 = 4000.0
    assert shoulders["cost"] == 4000.0
    # Revenue: 5500 * 0.95 * 1.0 = 5225.0
    assert shoulders["revenue"] == 5225.0
    # Deposit: v_sell is 4782, 5% is 239.1 -> deposit = 239
    assert shoulders["deposit"] == 239
    # Profit: 5225.0 - 4000.0 - 239 = 986.0
    assert shoulders["profit"] == 986.0
    assert shoulders["roi"] == 0.2465
    assert shoulders["roi_percent"] == 24.65

    # 2. With Bartering perk (5% discount on Silken Thread from NPC: 500 * 0.95 = 475)
    results_barter = get_crafting_margins(
        memory_db,
        realm="Everlook",
        min_roi=0.1,
        has_bartering=True,
    )
    by_recipe_barter = {r["recipe_id"]: r for r in results_barter}
    shoulders_barter = by_recipe_barter[10516]
    # Cost with Bartering: 1600 + 900 + 3 * 475 = 3925.0
    assert shoulders_barter["cost"] == 3925.0
    # Profit with Bartering: 5225.0 - 3925.0 - 239 = 1061.0
    assert shoulders_barter["profit"] == 1061.0
    assert shoulders_barter["roi"] == 0.2703


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


# ---------------------------------------------------------------------------
# Ticket 19 Tests
# ---------------------------------------------------------------------------

def test_crafting_margins_reagent_cost_capped_at_vendor_buy(memory_db):
    """Verify reagent unit cost is capped at min(P_AH, P_vendor_buy) when vendor_buy > 0."""
    now = int(time.time())
    with memory_db:
        # Items setup:
        # 7001: Crafted potion, AH price = 400 copper, vendor_sell_price = 10 -> deposit = 15.
        # 7002: Reagent A with vendor_buy_price = 100. Inflated AH price = 250 -> must cap at 100.
        # 7003: Reagent B with vendor_buy_price = 80. Discounted AH price = 50 -> must take AH 50.
        # 7004: Reagent C with vendor_buy_copper = 40 (only copper column set). No AH snapshot -> takes vendor 40.
        memory_db.executemany(
            """INSERT INTO items (
                   item_id, name, quality, vendor_sell_price, vendor_buy_price,
                   vendor_sell_copper, vendor_buy_copper
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                (7001, "Test Crafted Elixir", 1, 10, 0, 10, 0),
                (7002, "Inflated AH Vendor Reagent", 1, 5, 100, 5, 100),
                (7003, "Discounted AH Vendor Reagent", 1, 5, 80, 5, 80),
                (7004, "No AH Vendor Reagent", 1, 5, 0, 5, 40),
            ]
        )
        memory_db.executemany(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            [
                (7001, "Everlook", 400, now),
                (7002, "Everlook", 250, now),
                (7003, "Everlook", 50, now),
            ]
        )
        memory_db.execute(
            """INSERT INTO recipes (recipe_id, name, profession, crafted_item_id, yield)
               VALUES (?, ?, ?, ?, ?)""",
            (701, "Elixir of Capped Costs", "Alchemy", 7001, 1.0)
        )
        memory_db.executemany(
            """INSERT INTO recipe_reagents (recipe_id, reagent_item_id, reagent_id, count, quantity)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (701, 7002, 7002, 2, 2),  # 2x 7002: unit cost min(250, 100) = 100 -> total 200
                (701, 7003, 7003, 1, 1),  # 1x 7003: unit cost min(50, 80) = 50 -> total 50
                (701, 7004, 7004, 1, 1),  # 1x 7004: unit cost 40 (no AH) -> total 40
            ]
        )

    # 1. Base crafting (has_bartering=False)
    results = get_crafting_margins(memory_db, has_bartering=False, min_roi=0.25)
    matching = [r for r in results if r["recipe_id"] == 701]
    assert len(matching) == 1
    r701 = matching[0]

    # Total cost = 2*100 + 1*50 + 1*40 = 290.0
    # Net revenue = 400 * 0.95 = 380.0
    # Deposit = max(15, int(10 * 0.05)) = 15
    # Profit = 380.0 - 290.0 - 15 = 75.0
    # ROI = 75.0 / 290.0 = 0.2586 (25.86% >= 25%)
    assert r701["cost"] == 290.0
    assert r701["revenue"] == 380.0
    assert r701["deposit"] == 15
    assert r701["profit"] == 75.0
    assert r701["roi"] == 0.2586

    reagents_dict = {rg["item_id"]: rg for rg in r701["reagents"]}
    assert reagents_dict[7002]["unit_cost"] == 100.0
    assert reagents_dict[7002]["total_cost"] == 200.0
    assert reagents_dict[7002]["is_vendor"] is True

    assert reagents_dict[7003]["unit_cost"] == 50.0
    assert reagents_dict[7003]["total_cost"] == 50.0
    assert reagents_dict[7003]["is_vendor"] is True

    assert reagents_dict[7004]["unit_cost"] == 40.0
    assert reagents_dict[7004]["total_cost"] == 40.0
    assert reagents_dict[7004]["is_vendor"] is True

    # 2. With bartering (has_bartering=True): 5% vendor discount on vendor buy prices
    # 7002: eff_v_buy = 95 -> min(250, 95) = 95 -> 2*95 = 190
    # 7003: eff_v_buy = 76 -> min(50, 76) = 50 -> 1*50 = 50
    # 7004: eff_v_buy = 38 -> 1*38 = 38
    # Total cost = 190 + 50 + 38 = 278.0
    # Profit = 380.0 - 278.0 - 15 = 87.0
    # ROI = 87.0 / 278.0 = 0.3129
    results_barter = get_crafting_margins(memory_db, has_bartering=True, min_roi=0.25)
    r701_barter = next(r for r in results_barter if r["recipe_id"] == 701)
    assert r701_barter["cost"] == 278.0
    assert r701_barter["profit"] == 87.0
    assert r701_barter["roi"] == 0.3129


def test_zscore_anomalies_blocks_vendor_markup_strong_buy(memory_db):
    """Verify get_zscore_anomalies blocks STRONG_BUY when vendor_buy_price > 0 and current_price >= vendor_buy_price."""
    now = int(time.time())
    with memory_db:
        memory_db.executemany(
            """INSERT INTO items (
                   item_id, name, quality, vendor_sell_price, vendor_buy_price,
                   vendor_sell_copper, vendor_buy_copper
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                # 7101: vendor item (vendor_buy = 100), current AH price 200 >= 100 -> BLOCKED
                (7101, "Vendor Markup Trap", 1, 10, 100, 10, 100),
                # 7102: vendor item (vendor_buy = 100), current AH price 30 < 100 -> ALLOWED STRONG_BUY
                (7102, "True Panic Dump Vendor Item", 1, 10, 100, 10, 100),
                # 7103: vendor item via vendor_buy_copper alias (100), current AH price 150 >= 100 -> BLOCKED
                (7103, "Copper Alias Markup Trap", 1, 10, 0, 10, 100),
                # 7104: non-vendor item (vendor_buy = 0), current AH price 200 -> ALLOWED STRONG_BUY
                (7104, "Normal Non-Vendor Item", 1, 10, 0, 10, 0),
            ]
        )

        # Insert 7 snapshots per item: 6 historical at 500, then recent drop
        snapshots = []
        for item_id in [7101, 7102, 7103, 7104]:
            for t_offset in range(60, 0, -10):
                snapshots.append((item_id, "Everlook", 500, now - t_offset))

        # Recent snapshot (current price):
        snapshots.append((7101, "Everlook", 200, now))  # 200 >= 100 -> trap
        snapshots.append((7102, "Everlook", 30, now))   # 30 < 100 -> real dump
        snapshots.append((7103, "Everlook", 150, now))  # 150 >= 100 -> trap
        snapshots.append((7104, "Everlook", 200, now))  # normal item -> real dump

        memory_db.executemany(
            "INSERT INTO price_snapshots (item_id, realm, price, timestamp) VALUES (?, ?, ?, ?)",
            snapshots
        )

    anomalies = get_zscore_anomalies(memory_db, window_size=15, min_samples=6)
    signals_by_id = {a["item_id"]: a["signal"] for a in anomalies}

    # 7101 and 7103 must be blocked from STRONG_BUY
    assert 7101 not in signals_by_id
    assert 7103 not in signals_by_id

    # 7102 (current_price 30 < vendor 100) must trigger STRONG_BUY
    assert signals_by_id.get(7102) == "STRONG_BUY"

    # 7104 (non-vendor item) must trigger STRONG_BUY
    assert signals_by_id.get(7104) == "STRONG_BUY"


def test_vendor_shuffle_signal_and_wrapper(memory_db):
    """Verify VENDOR_SHUFFLE signal in get_vendor_arbitrage and get_vendor_shuffle wrapper."""
    now = int(time.time())
    with memory_db:
        memory_db.executemany(
            """INSERT INTO items (item_id, name, quality, vendor_sell_price, vendor_buy_price)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (7201, "Vendor Shuffle Gold", 1, 200, 0),
                (7202, "Vendor Shuffle Silver", 1, 160, 0),
                (7203, "Sub-threshold Item", 1, 120, 0),
            ]
        )
        memory_db.executemany(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            [
                (7201, "Everlook", 80, now),   # profit 120 >= 50 -> INCLUDED
                (7202, "Everlook", 100, now),  # profit 60 >= 50 -> INCLUDED
                (7203, "Everlook", 100, now),  # profit 20 < 50 -> EXCLUDED
            ]
        )

    # 1. get_vendor_arbitrage outputs VENDOR_SHUFFLE signal
    arb_results = get_vendor_arbitrage(memory_db, min_profit_copper=50)
    assert len(arb_results) == 2
    for r in arb_results:
        assert r["signal"] == "VENDOR_SHUFFLE"
        assert r["profit_copper"] >= 50

    # 2. get_vendor_shuffle alias outputs identical results with VENDOR_SHUFFLE signal
    shuffle_results = get_vendor_shuffle(memory_db, min_profit_copper=50)
    assert shuffle_results == arb_results
    assert shuffle_results[0]["item_id"] == 7201
    assert shuffle_results[0]["signal"] == "VENDOR_SHUFFLE"
    assert shuffle_results[0]["profit_copper"] == 120
    assert shuffle_results[1]["item_id"] == 7202
    assert shuffle_results[1]["signal"] == "VENDOR_SHUFFLE"
    assert shuffle_results[1]["profit_copper"] == 60


def test_zscore_anomalies_day1_hardening_small_samples(memory_db):
    """Verify Day-1 hardening in get_zscore_anomalies: safe against N < min_samples and sigma == 0."""
    now = int(time.time())
    with memory_db:
        memory_db.executemany(
            """INSERT INTO items (item_id, name, quality, vendor_sell_price, vendor_buy_price)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (7400, "Zero Samples Item", 1, 10, 0),
                (7401, "1 Sample Item", 1, 10, 0),
                (7402, "2 Samples Item", 1, 10, 0),
                (7403, "3 Samples Item", 1, 10, 0),
                (7404, "4 Samples Item", 1, 10, 0),
                (7405, "5 Samples Item", 1, 10, 0),
                (7406, "Zero Variance 6 Samples", 1, 10, 0),
            ]
        )
        # 7401 has 1 snapshot
        memory_db.execute("INSERT INTO price_snapshots (item_id, realm, price, timestamp) VALUES (7401, 'Everlook', 100, ?)", (now,))
        # 7402 has 2 snapshots
        memory_db.executemany("INSERT INTO price_snapshots (item_id, realm, price, timestamp) VALUES (7402, 'Everlook', ?, ?)", [(100, now - 10), (100, now)])
        # 7403 has 3 snapshots
        memory_db.executemany("INSERT INTO price_snapshots (item_id, realm, price, timestamp) VALUES (7403, 'Everlook', ?, ?)", [(100, now - 20), (100, now - 10), (50, now)])
        # 7404 has 4 snapshots
        memory_db.executemany("INSERT INTO price_snapshots (item_id, realm, price, timestamp) VALUES (7404, 'Everlook', ?, ?)", [(100, now - 30), (100, now - 20), (100, now - 10), (50, now)])
        # 7405 has 5 snapshots
        memory_db.executemany("INSERT INTO price_snapshots (item_id, realm, price, timestamp) VALUES (7405, 'Everlook', ?, ?)", [(100, now - 40), (100, now - 30), (100, now - 20), (100, now - 10), (50, now)])
        # 7406 has 6 snapshots all identical price 100 (sigma = 0)
        memory_db.executemany("INSERT INTO price_snapshots (item_id, realm, price, timestamp) VALUES (7406, 'Everlook', 100, ?)", [(now - t,) for t in [50, 40, 30, 20, 10, 0]])

    # 1. Under default min_samples=6: items 7400-7405 have N < 6, 7406 has sigma=0
    # Must NOT raise any ZeroDivisionError, ValueError or KeyError; returns safe empty list
    results = get_zscore_anomalies(memory_db, min_samples=6)
    assert isinstance(results, list)
    assert len(results) == 0

    # 2. Test with min_samples=3: items with N=1, 2 safely skipped, items with N=3, 4, 5 evaluated without error
    results_min3 = get_zscore_anomalies(memory_db, min_samples=3)
    assert isinstance(results_min3, list)
    for r in results_min3:
        assert not math.isnan(r["z_score"])
        assert not math.isinf(r["z_score"])
        assert not math.isnan(r["std_dev"])

    # 3. Test with min_samples=1: len(entries) < 2 guard prevents crash on N=1
    results_min1 = get_zscore_anomalies(memory_db, min_samples=1)
    assert isinstance(results_min1, list)
    emitted_ids = {r["item_id"] for r in results_min1}
    assert 7401 not in emitted_ids  # N=1 skipped by n < 2 guard safely


def test_dynamic_reload_of_overrides_on_mtime_change(memory_db, tmp_path):
    """Verify check_and_reload_overrides detects mtime changes and updates database records."""
    overrides_file = tmp_path / "test_overrides.json"

    # Initial file state
    initial_data = [
        {
            "item_id": 7501,
            "name": "Waylaid Supplies: Initial Wool",
            "category": "waylaid_crate",
            "target_item_id": 2592,
            "target_item_count": 10,
            "reward_copper": 1000,
            "is_override": 1
        }
    ]
    overrides_file.write_text(json.dumps(initial_data), encoding="utf-8")

    # 1. Initial reload: returns True because mtime > 0
    loaded = check_and_reload_overrides(memory_db, overrides_path=str(overrides_file))
    assert loaded is True

    row = memory_db.execute(
        "SELECT name, reward_copper, category FROM items WHERE item_id = 7501"
    ).fetchone()
    assert row is not None
    assert row["name"] == "Waylaid Supplies: Initial Wool"
    assert row["reward_copper"] == 1000
    assert row["category"] == "waylaid_crate"

    # 2. Second check without file change: returns False
    loaded_again = check_and_reload_overrides(memory_db, overrides_path=str(overrides_file))
    assert loaded_again is False

    # 3. Modify file content and bump mtime into the future
    updated_data = [
        {
            "item_id": 7501,
            "name": "Waylaid Supplies: Updated Wool",
            "category": "waylaid_crate",
            "target_item_id": 2592,
            "target_item_count": 10,
            "reward_copper": 3500,
            "is_override": 1
        },
        {
            "item_id": 7502,
            "name": "Waylaid Supplies: Silk",
            "category": "waylaid_crate",
            "target_item_id": 4306,
            "target_item_count": 15,
            "reward_copper": 5000,
            "is_override": 1
        }
    ]
    overrides_file.write_text(json.dumps(updated_data), encoding="utf-8")
    future_mtime = time.time() + 100.0
    os.utime(str(overrides_file), (future_mtime, future_mtime))

    # 4. Check again: mtime changed -> reloads and returns True
    reloaded = check_and_reload_overrides(memory_db, overrides_path=str(overrides_file))
    assert reloaded is True

    row_7501 = memory_db.execute(
        "SELECT name, reward_copper FROM items WHERE item_id = 7501"
    ).fetchone()
    assert row_7501["name"] == "Waylaid Supplies: Updated Wool"
    assert row_7501["reward_copper"] == 3500

    row_7502 = memory_db.execute(
        "SELECT name, reward_copper FROM items WHERE item_id = 7502"
    ).fetchone()
    assert row_7502 is not None
    assert row_7502["name"] == "Waylaid Supplies: Silk"
    assert row_7502["reward_copper"] == 5000

    # 5. Force reload via reload_overrides returns True even if mtime hasn't changed
    forced = reload_overrides(memory_db, overrides_path=str(overrides_file))
    assert forced is True


# ---------------------------------------------------------------------------
# Ticket 21: Analytics & Math Hardening Tests (R71, R72, R73, R74, R75, R76, R90i, R91i)
# ---------------------------------------------------------------------------

def test_waylaid_raw_turnin_strict_100_percent_ah_price(memory_db):
    """R71: Verify raw Waylaid crate turn-in calculates pack_cost strictly at 100% AH price,
    never substituting vendor_buy_price, and excludes negative profit crates."""
    now = int(time.time())
    with memory_db:
        # Target item 7001 (e.g. Raw Peacebloom): vendor_buy = 10, vendor_sell = 2
        # AH price = 100
        memory_db.execute(
            """INSERT INTO items (item_id, name, quality, vendor_sell_price, vendor_buy_price, category)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (7001, "Raw Peacebloom", 1, 2, 10, "trade_goods")
        )
        # Raw crate 211501: target 7001, count 20, reward 1000
        memory_db.execute(
            """INSERT INTO items (item_id, name, quality, target_item_id, target_item_count, reward_copper, category)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (211501, "Waylaid Supplies: Peacebloom", 1, 7001, 20, 1000, "waylaid_crate")
        )
        # AH snapshot: price = 100
        memory_db.execute(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            (7001, "Everlook", 100, now)
        )

    # 1. Unprofitable turn-in:
    # If vendor_buy was erroneously used: pack_cost = 20 * 10 = 200, profit = 800 (profitable!)
    # But strictly using AH price: pack_cost = 20 * 100 = 2000, profit = 1000 - 2000 = -1000
    # Must be excluded from profitable margins and from get_waylaid_crate_arbitrage
    margins_default = get_crafting_margins(memory_db, realm="Everlook")
    assert not any(m.get("waylaid_crate_id") == 211501 for m in margins_default)

    crates_arbitrage = get_waylaid_crate_arbitrage(memory_db, realm="Everlook")
    assert not any(c.get("waylaid_crate_id") == 211501 for c in crates_arbitrage)

    # 2. Profitable turn-in when AH price drops to 30:
    # pack_cost = 20 * 30 = 600, profit = 1000 - 600 = 400
    memory_db.execute(
        """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
           VALUES (?, ?, ?, ?)""",
        (7001, "Everlook", 30, now + 10)
    )

    margins_profitable = get_crafting_margins(memory_db, realm="Everlook", min_roi=0.25)
    crate_matches = [m for m in margins_profitable if m.get("waylaid_crate_id") == 211501]
    assert len(crate_matches) == 1
    crate_res = crate_matches[0]
    assert crate_res["pack_cost"] == 600.0
    assert crate_res["profit"] == 400.0
    assert crate_res["turnin_profit"] == 400.0
    assert crate_res["cost"] == 600.0
    assert crate_res["reagents"][0]["unit_cost"] == 30.0
    assert crate_res["reagents"][0]["is_vendor"] is False


def test_strict_vendor_whitelist_enforcement(memory_db):
    """R72: Verify reagent cost uses AH price unless item_id is in VENDOR_REAGENTS_WHITELIST."""
    now = int(time.time())
    with memory_db:
        # Reagent 13759 (Raw Sunscale Salmon) - NOT in whitelist, has vendor_buy_price=50
        # Reagent 2320 (Coarse Thread) - IN whitelist, vendor_buy_price=50
        # Target crafted items: 8001, 8002
        memory_db.executemany(
            """INSERT INTO items (item_id, name, quality, vendor_sell_price, vendor_buy_price)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (13759, "Raw Sunscale Salmon", 1, 10, 50),
                (2320, "Coarse Thread", 1, 2, 50),
                (8001, "Cooked Salmon", 1, 100, 0),
                (8002, "Linen Bandage", 1, 50, 0),
            ]
        )
        memory_db.executemany(
            """INSERT INTO recipes (recipe_id, name, profession, crafted_item_id, yield)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (101, "Cooked Salmon Recipe", "Cooking", 8001, 1.0),
                (102, "Bandage Recipe", "Tailoring", 8002, 1.0),
            ]
        )
        memory_db.executemany(
            """INSERT INTO recipe_reagents (recipe_id, reagent_item_id, reagent_id, count, quantity)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (101, 13759, 13759, 1, 1),
                (102, 2320, 2320, 1, 1),
            ]
        )
        # Snapshots:
        # Crafted items priced high (1000)
        # Reagent 13759 AH price = 500 (vendor_buy is 50)
        # Reagent 2320 AH price = 500 (vendor_buy is 50)
        memory_db.executemany(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            [
                (8001, "Everlook", 1000, now),
                (8002, "Everlook", 1000, now),
                (13759, "Everlook", 500, now),
                (2320, "Everlook", 500, now),
            ]
        )

    assert 13759 not in VENDOR_REAGENTS_WHITELIST
    assert 2320 in VENDOR_REAGENTS_WHITELIST

    results = get_crafting_margins(memory_db, realm="Everlook", min_roi=0.0)
    by_id = {r["recipe_id"]: r for r in results}

    # Recipe 101 (uses 13759): not in whitelist -> must use AH price (500), NOT vendor_buy (50)
    res_101 = by_id[101]
    assert res_101["cost"] == 500.0
    reag_13759 = res_101["reagents"][0]
    assert reag_13759["is_vendor"] is False
    assert reag_13759["unit_cost"] == 500.0

    # Recipe 102 (uses 2320): in whitelist -> must use min(AH 500, vendor_buy 50) = 50.0
    res_102 = by_id[102]
    assert res_102["cost"] == 50.0
    reag_2320 = res_102["reagents"][0]
    assert reag_2320["is_vendor"] is True
    assert reag_2320["unit_cost"] == 50.0

    # Bartering perk check: applies to 2320 (50 * 0.95 = 47.5), but NOT to 13759 (remains 500.0)
    results_barter = get_crafting_margins(memory_db, realm="Everlook", min_roi=0.0, has_bartering=True)
    by_id_barter = {r["recipe_id"]: r for r in results_barter}
    assert by_id_barter[101]["cost"] == 500.0
    assert by_id_barter[102]["cost"] == 47.5

    # When AH price for 2320 is cheaper than vendor price (e.g. 30 < 50): uses min(30, 50) = 30.0
    memory_db.execute(
        """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
           VALUES (?, ?, ?, ?)""",
        (2320, "Everlook", 30, now + 10)
    )
    results_cheap = get_crafting_margins(memory_db, realm="Everlook", min_roi=0.0)
    by_id_cheap = {r["recipe_id"]: r for r in results_cheap}
    assert by_id_cheap[102]["cost"] == 30.0
    assert by_id_cheap[102]["reagents"][0]["unit_cost"] == 30.0


def test_zero_and_negative_price_filtering(memory_db):
    """R73: Verify snapshots with price <= 0 are completely ignored in both arbitrage and Z-score."""
    now = int(time.time())
    with memory_db:
        # Item 301: vendor_sell = 100
        # Corrupted snapshots: 0 and -10
        memory_db.executemany(
            """INSERT INTO items (item_id, name, quality, vendor_sell_price, vendor_buy_price)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (301, "Bugged Zero Price Item", 1, 100, 0),
                (302, "Zscore Target Item", 1, 10, 0),
            ]
        )
        memory_db.executemany(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            [
                (301, "Everlook", 0, now - 10),
                (301, "Everlook", -10, now),
            ]
        )
        # Snapshots for item 302: valid history, but latest are 0 and -10
        for i, p in enumerate([100, 105, 98, 102, 99, 101, 100]):
            memory_db.execute(
                """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
                   VALUES (?, ?, ?, ?)""",
                (302, "Everlook", p, now - 100 + i * 5)
            )
        memory_db.execute(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            (302, "Everlook", 0, now + 10)
        )
        memory_db.execute(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            (302, "Everlook", -10, now + 20)
        )

    # 1. Vendor arbitrage: item 301 must NOT produce VENDOR_SHUFFLE with buyout_price <= 0
    arb = get_vendor_arbitrage(memory_db, realm="Everlook", min_profit_copper=1)
    for r in arb:
        assert r["buyout_price"] > 0
        assert r["price"] > 0
    assert not any(r["item_id"] == 301 for r in arb)

    # 2. Z-score: non-positive prices must be ignored and not produce signals with current_price <= 0
    anomalies = get_zscore_anomalies(memory_db, realm="Everlook", min_samples=5)
    for a in anomalies:
        assert a["current_price"] > 0
        assert a["price"] > 0


def test_realm_isolation_when_realm_none(memory_db):
    """R74: Verify get_crafting_margins(realm=None) isolates realms and never mixes prices across realms."""
    now = int(time.time())
    with memory_db:
        # Item 401 (Crafted): vendor_sell = 20 (deposit = 15)
        # Item 402 (Reagent)
        memory_db.executemany(
            """INSERT INTO items (item_id, name, quality, vendor_sell_price, vendor_buy_price)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (401, "Cross-Realm Crafted Item", 1, 20, 0),
                (402, "Cross-Realm Reagent", 1, 5, 0),
            ]
        )
        memory_db.execute(
            """INSERT INTO recipes (recipe_id, name, profession, crafted_item_id, yield)
               VALUES (?, ?, ?, ?, ?)""",
            (4001, "Cross Realm Recipe", "Engineering", 401, 1.0)
        )
        memory_db.execute(
            """INSERT INTO recipe_reagents (recipe_id, reagent_item_id, reagent_id, count, quantity)
               VALUES (?, ?, ?, ?, ?)""",
            (4001, 402, 402, 1, 1)
        )
        # Everlook: Crafted 200, Reagent 190 -> cost 190, rev 190, profit = 190 - 190 - 15 = -15
        # Warsong: Crafted 10, Reagent 10 -> cost 10, rev 9.5, profit = 9.5 - 10 - 15 = -15.5
        # If mixed (Everlook crafted 200 + Warsong reagent 10): cost 10, rev 190 -> profit 165
        memory_db.executemany(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            [
                (401, "Everlook", 200, now),
                (402, "Everlook", 190, now),
                (401, "Warsong", 10, now),
                (402, "Warsong", 10, now),
            ]
        )

    # Calling with min_roi=0.1: should return empty list because neither realm has positive profit
    profitable = get_crafting_margins(memory_db, realm=None, min_roi=0.1)
    assert len(profitable) == 0

    # Calling with min_roi=-999.0: should return separate records for each realm, neither mixed!
    all_margins = get_crafting_margins(memory_db, realm=None, min_roi=-999.0)
    for m in all_margins:
        if m["realm"] == "Everlook":
            assert m["crafted_item_price"] == 200
            assert m["cost"] == 190.0
        elif m["realm"] == "Warsong":
            assert m["crafted_item_price"] == 10
            assert m["cost"] == 10.0


def test_multi_yield_crafting_math(memory_db):
    """R75 / R90i: Verify recipes with yield > 1 properly scale expected revenue and deposit,
    and Waylaid crate pack_craft_cost scales as (total_cost / yield) * target_count."""
    now = int(time.time())
    with memory_db:
        # Item 501: Crafted, vendor_sell = 2000 (deposit = max(15, 2000 * 0.05) = 100)
        # Item 502: Reagent
        # Crate 211510: target 501, count 4, reward 500
        memory_db.executemany(
            """INSERT INTO items (item_id, name, quality, vendor_sell_price, vendor_buy_price, category,
                                  target_item_id, target_item_count, reward_copper)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (501, "Multi-Yield Potion", 1, 2000, 0, "consumable", None, 0, 0),
                (502, "Herb Reagent", 1, 10, 0, "trade_goods", None, 0, 0),
                (211510, "Waylaid Supplies: Potions", 1, 0, 0, "waylaid_crate", 501, 4, 500),
            ]
        )
        memory_db.execute(
            """INSERT INTO recipes (recipe_id, name, profession, crafted_item_id, yield)
               VALUES (?, ?, ?, ?, ?)""",
            (5001, "Batch Potion Recipe", "Alchemy", 501, 2.0)
        )
        memory_db.execute(
            """INSERT INTO recipe_reagents (recipe_id, reagent_item_id, reagent_id, count, quantity)
               VALUES (?, ?, ?, ?, ?)""",
            (5001, 502, 502, 1, 1)
        )
        # Snapshots: Reagent 502 = 50, Crafted 501 = 100
        memory_db.executemany(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            [
                (501, "Everlook", 100, now),
                (502, "Everlook", 50, now),
            ]
        )

    results = get_crafting_margins(memory_db, realm="Everlook", min_roi=-999.0)
    assert len(results) == 1
    res = results[0]

    # Verify multi-yield recipe math:
    # yield = 2.0, cost = 50.0
    # revenue = 100 * 0.95 * 2.0 = 190.0
    # deposit = 100 (5% of 2000)
    # profit = 190.0 - 50.0 - 100 = 40.0
    assert res["expected_yield"] == 2.0
    assert res["cost"] == 50.0
    assert res["revenue"] == 190.0
    assert res["deposit"] == 100
    assert res["profit"] == 40.0

    # Verify Waylaid Crate pack_craft_cost:
    # Formula: (total_cost / expected_yield) * target_count
    # pack_craft_cost = (50.0 / 2.0) * 4 = 100.0
    # craft_turnin_profit = reward_copper (500) - pack_craft_cost (100.0) = 400.0
    assert res["is_waylaid_target"] is True
    assert res["pack_craft_cost"] == 100.0
    assert res["craft_turnin_profit"] == 400.0

    # pack_cost (raw AH turn-in) = target_count * crafted_price = 4 * 100 = 400.0
    assert res["pack_cost"] == 400.0
    assert res["turnin_profit"] == 100.0  # 500 - 400 = 100


def test_overrides_reload_retry_on_corrupted_json(memory_db, tmp_path):
    """R76 / R91i: Verify check_and_reload_overrides fails on corrupt JSON without advancing mtime,
    and succeeds once the file is fixed."""
    import src.analytics
    # Reset internal mtime cache to ensure clean test environment
    src.analytics._OVERRIDES_MTIME = 0.0

    overrides_file = tmp_path / "corrupt_overrides.json"

    # 1. Write corrupted JSON
    overrides_file.write_text("{ this is invalid json content: 123", encoding="utf-8")
    future_mtime = time.time() + 50.0
    os.utime(str(overrides_file), (future_mtime, future_mtime))

    # Initial check fails and returns False
    res = check_and_reload_overrides(memory_db, overrides_path=str(overrides_file))
    assert res is False

    # Check that mtime was NOT updated to future_mtime and item was NOT loaded
    assert src.analytics._OVERRIDES_MTIME == 0.0
    row = memory_db.execute("SELECT * FROM items WHERE item_id = 9991").fetchone()
    assert row is None

    # 2. Fix the file with valid JSON (even with same future_mtime)
    valid_data = [
        {
            "item_id": 9991,
            "name": "Waylaid Supplies: Heavy Iron",
            "category": "waylaid_crate",
            "target_item_id": 3577,
            "target_item_count": 15,
            "reward_copper": 6000,
            "is_override": 1,
        }
    ]
    overrides_file.write_text(json.dumps(valid_data), encoding="utf-8")
    os.utime(str(overrides_file), (future_mtime, future_mtime))

    # Second check succeeds, returns True, advances mtime, and loads item into DB
    res_fixed = check_and_reload_overrides(memory_db, overrides_path=str(overrides_file))
    assert res_fixed is True
    assert src.analytics._OVERRIDES_MTIME == future_mtime

    row_fixed = memory_db.execute(
        "SELECT name, reward_copper FROM items WHERE item_id = 9991"
    ).fetchone()
    assert row_fixed is not None
    assert row_fixed["name"] == "Waylaid Supplies: Heavy Iron"
    assert row_fixed["reward_copper"] == 6000


# ---------------------------------------------------------------------------
# Ticket 01: Disenchant Tables and Liquidity Shield
# ---------------------------------------------------------------------------


def test_load_disenchant_tables():
    """Verify load_disenchant_tables returns correct structure and Forever weapon rule (10939)."""
    data = load_disenchant_tables()
    assert isinstance(data, dict)
    assert set(data.keys()) >= {"uncommon", "rare", "epic"}

    # Must contain records in all quality tiers
    assert len(data["uncommon"]) > 0
    assert len(data["rare"]) > 0
    assert len(data["epic"]) > 0

    # Rule forever for weapons: Greater Magic Essence (item_id 10939)
    # Check that uncommon tables contain the forever weapon essence override
    forever_weapon_rows = [
        row for row in data["uncommon"]
        if row.get("forever_weapon_essence_id") == 10939
    ]
    assert len(forever_weapon_rows) >= 2, (
        f"Expected at least 2 uncommon brackets with forever_weapon_essence_id == 10939, got {len(forever_weapon_rows)}"
    )
    for row in forever_weapon_rows:
        assert 16 <= row["min_level"] <= 30
        assert 16 <= row["max_level"] <= 30
        assert row["forever_weapon_essence_id"] == 10939

    # Level 16-20 tier naturally yields 10939 as essence
    tier_16_20 = [r for r in data["uncommon"] if r.get("min_level") == 16 and r.get("max_level") == 20]
    assert len(tier_16_20) == 1
    assert tier_16_20[0]["essence_id"] == 10939
    assert tier_16_20[0]["weapon_essence_chance"] == 75

    # Caching verification and missing file fallback
    cached = load_disenchant_tables()
    assert cached is data

    fallback = load_disenchant_tables("non_existent_tables_path.json")
    assert fallback == {"uncommon": [], "rare": [], "epic": []}


def test_compute_liquidity_score():
    """Verify liquidity score computation: consumables >= 0.90, recipes <= 0.25, range [0.01, 1.0]."""
    # 1. Consumables and Trade Goods give score >= 0.90
    score_consumable = compute_liquidity_score(item_id=101, item_class="Consumable")
    assert score_consumable >= 0.90, f"Expected Consumable liquidity >= 0.90, got {score_consumable}"

    score_trade_goods = compute_liquidity_score(item_id=102, item_class="Trade Goods")
    assert score_trade_goods >= 0.90, f"Expected Trade Goods liquidity >= 0.90, got {score_trade_goods}"

    # Also check other consumable/reagent aliases
    assert compute_liquidity_score(item_id=103, item_class="trade_goods") >= 0.90
    assert compute_liquidity_score(item_id=104, item_class="reagent") >= 0.90

    # 2. Recipes give score <= 0.25
    score_recipe = compute_liquidity_score(item_id=201, item_class="Recipe")
    assert score_recipe <= 0.25, f"Expected Recipe liquidity <= 0.25, got {score_recipe}"

    assert compute_liquidity_score(item_id=202, item_class="Pattern") <= 0.25
    assert compute_liquidity_score(item_id=203, item_class="Plans") <= 0.25
    assert compute_liquidity_score(item_id=204, item_class="Schematic") <= 0.25

    # 3. Behavior with history and without history
    # Without history: returns intrinsic score
    recipe_no_hist = compute_liquidity_score(item_id=201, history=None, item_class="Recipe")
    assert recipe_no_hist == 0.20

    # With empty history: also returns intrinsic score
    recipe_empty_hist = compute_liquidity_score(item_id=201, history=[], item_class="Recipe")
    assert recipe_empty_hist == 0.20

    # With active history: blended observed liquidity increases the score
    history_active = [{"price": 100, "count": 5} for _ in range(10)]
    recipe_with_hist = compute_liquidity_score(item_id=201, history=history_active, item_class="Recipe")
    assert recipe_with_hist > recipe_no_hist
    assert recipe_with_hist == 0.60

    # 4. Range guarantee [0.01, 1.0] across all item types and histories
    test_cases = [
        (301, "Junk", None),
        (302, "Junk", [{"price": 10}]),
        (303, "Recipe", [{"price": 50, "count": 1}]),
        (304, "Armor", history_active),
        (305, "Consumable", history_active),
        (306, "UnknownClass123", None),
    ]
    for i_id, cls_name, hist in test_cases:
        score = compute_liquidity_score(item_id=i_id, history=hist, item_class=cls_name)
        assert 0.01 <= score <= 1.0, f"Score {score} out of bounds [0.01, 1.0] for item {i_id} ({cls_name})"


def test_get_strong_buys_liquidity_penalty_and_trap(memory_db):
    """Verify get_strong_buys applies liquidity penalty, flags illiquid traps (<0.20), and prunes <=0 profit when L<0.50."""
    # 1. Candidate with L < 0.20 (Junk / Trash item)
    # Intrinsic liquidity for Junk is 0.10 (< 0.20).
    trap_candidate = {
        "item_id": 9501,
        "name": "Ruined Leather Scrap",
        "item_class": "Junk",
        "current_price": 500,
        "mean_price": 1200,
        "expected_revenue": 1140,  # 1200 * 0.95
        "deposit": 50,
        "signal": "STRONG_BUY",
    }

    # Liquid candidate with L >= 0.50 (Consumable, L = 0.95)
    liquid_candidate = {
        "item_id": 9502,
        "name": "Major Healing Potion",
        "item_class": "Consumable",
        "current_price": 500,
        "mean_price": 1200,
        "expected_revenue": 1140,
        "deposit": 50,
        "signal": "STRONG_BUY",
    }

    # Marginal Recipe candidate with L = 0.20 (< 0.50):
    # Nominal profit before penalty is 1050 - 1000 = 50 > 0.
    # Liquidity penalty is deposit * (0.8/0.2) + 0.8 * 0.1 * 1000 = 100 * 4 + 80 = 480.
    # Net profit after penalty = 1050 - 1000 - 480 = -430 <= 0.
    # Must be PRUNED!
    pruned_recipe_candidate = {
        "item_id": 9503,
        "name": "Recipe: Minor Agility",
        "item_class": "Recipe",
        "current_price": 1000,
        "mean_price": 1105,
        "expected_revenue": 1050,
        "deposit": 100,
        "signal": "STRONG_BUY",
    }

    # Highly profitable Recipe with L = 0.20 (< 0.50):
    # Nominal profit before penalty is 2850 - 1000 = 1850.
    # Penalty is 480. Net profit = 1850 - 480 = 1370 > 0.
    # Must NOT be pruned.
    profitable_recipe_candidate = {
        "item_id": 9504,
        "name": "Recipe: Lionheart Helm",
        "item_class": "Recipe",
        "current_price": 1000,
        "mean_price": 3000,
        "expected_revenue": 2850,
        "deposit": 100,
        "signal": "STRONG_BUY",
    }

    candidates = [
        trap_candidate,
        liquid_candidate,
        pruned_recipe_candidate,
        profitable_recipe_candidate,
    ]

    # Run get_strong_buys with include_traps=True
    results_with_traps = get_strong_buys(candidates=candidates, include_traps=True)

    # 1. Check trap flagging: item 9501 has L = 0.10 < 0.20 -> is_illiquid_trap = True, signal = ILLIQUID_TRAP
    trap_res = next((r for r in results_with_traps if r["item_id"] == 9501), None)
    assert trap_res is not None
    assert trap_res["liquidity"] < 0.20
    assert trap_res["is_illiquid_trap"] is True
    assert trap_res["signal"] == "ILLIQUID_TRAP"

    # 2. Check liquidity penalty reduces expected_net_profit
    # For liquid item 9502 (L = 0.95):
    liquid_res = next((r for r in results_with_traps if r["item_id"] == 9502), None)
    assert liquid_res is not None
    assert liquid_res["liquidity"] >= 0.90
    assert liquid_res["is_illiquid_trap"] is False
    assert liquid_res["signal"] == "STRONG_BUY"
    # Nominal profit before penalty: 1140 - 500 = 640
    # Penalty: int(50 * (0.05 / 0.95) + 0.05 * 0.10 * 500) = int(2.63 + 2.5) = 5
    # expected_net_profit: 640 - 5 = 635
    assert liquid_res["liquidity_penalty"] == 5
    assert liquid_res["expected_net_profit"] == 635.0
    assert liquid_res["expected_net_profit"] < (1140 - 500)

    # 3. Check pruned item: 9503 had nominal profit > 0, but after penalty net profit <= 0 and L < 0.50
    pruned_res = [r for r in results_with_traps if r["item_id"] == 9503]
    assert len(pruned_res) == 0, "Item 9503 should be pruned because net_profit <= 0 and liquidity < 0.50"

    # 4. Check profitable recipe 9504: L < 0.50 but net_profit > 0 -> retained
    prof_recipe_res = next((r for r in results_with_traps if r["item_id"] == 9504), None)
    assert prof_recipe_res is not None
    assert prof_recipe_res["liquidity"] == 0.20
    assert prof_recipe_res["is_illiquid_trap"] is False
    assert prof_recipe_res["signal"] == "STRONG_BUY"
    assert prof_recipe_res["liquidity_penalty"] == 480
    assert prof_recipe_res["expected_net_profit"] == 1370.0

    # 5. When include_traps=False, trap item 9501 is completely omitted
    results_no_traps = get_strong_buys(candidates=candidates, include_traps=False)
    assert not any(r["item_id"] == 9501 for r in results_no_traps)
    assert any(r["item_id"] == 9502 for r in results_no_traps)
    assert any(r["item_id"] == 9504 for r in results_no_traps)

    # 6. Database integration test with real SQLite memory_db
    with memory_db:
        memory_db.executemany(
            """INSERT INTO items (item_id, name, quality, item_class, category, vendor_sell_price)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [
                (9601, "Illiquid Grey Item", 0, "Junk", "Junk", 100),
                (9602, "Liquid Consumable Item", 1, "Consumable", "Consumable", 100),
            ]
        )
        base_t = 20000
        snaps = []
        for i in range(5):
            snaps.append((9601, "Everlook", 1000, base_t + i))
            snaps.append((9602, "Everlook", 1000, base_t + i))
        snaps.append((9601, "Everlook", 100, base_t + 10))
        snaps.append((9602, "Everlook", 100, base_t + 10))
        memory_db.executemany(
            "INSERT INTO price_snapshots (item_id, realm, price, timestamp) VALUES (?, ?, ?, ?)",
            snaps
        )

    db_results = get_strong_buys(conn=memory_db, realm="Everlook", min_samples=6)
    db_9602 = next((r for r in db_results if r["item_id"] == 9602), None)
    assert db_9602 is not None
    assert db_9602["signal"] == "STRONG_BUY"
    assert db_9602["liquidity"] >= 0.70
    assert db_9602["is_illiquid_trap"] is False


# ---------------------------------------------------------------------------
# Ticket 02: Disenchant Matrix, Sniper Export & Addon Structure Tests
# ---------------------------------------------------------------------------

def test_calculate_disenchant_value(memory_db):
    """Verify expected disenchant value calculation for equipment across all quality tiers and data matrices."""
    # 1. Uncommon Green Armor (level 10, quality 2, item_class 4)
    # Tier: 5-15, dust_id 10940, essence_id 10938
    # dust_chance: 80%, avg_dust: 1.5. essence_chance: 20%, avg_essence: 1.5.
    # Prices: dust=100, essence=300
    # Expected: (0.80 * 1.5 * 100) + (0.20 * 1.5 * 300) = 120 + 90 = 210.0
    val_armor = calculate_disenchant_value(
        item_id=7001,
        item_meta={"quality": 2, "item_class": 4, "item_level": 10},
        latest_prices={10940: 100, 10938: 300},
    )
    assert val_armor == 210.0

    # 2. Uncommon Green Weapon (level 10, quality 2, item_class 2)
    # dust_chance: 20%, essence_chance: 80%
    # Expected: (0.20 * 1.5 * 100) + (0.80 * 1.5 * 300) = 30 + 360 = 390.0
    val_weapon = calculate_disenchant_value(
        item_id=7002,
        item_meta={"quality": 2, "item_class": 2, "item_level": 10},
        latest_prices={10940: 100, 10938: 300},
    )
    assert val_weapon == 390.0

    # 3. Uncommon Green with shard chance and forever_weapon_essence_id (level 22, weapon)
    # Tier 21-25: dust_id 10940 (avg 5.0), forever_weapon_essence_id 10939 (avg 1.5), shard_id 10978 (avg 1.0)
    # weapon_dust_chance: 15%, weapon_essence_chance: 75%, shard_chance: 100 - (15+75) = 10%
    # Prices: dust=50, essence=400, shard=1000
    # Expected: (0.15 * 5.0 * 50) + (0.75 * 1.5 * 400) + (0.10 * 1.0 * 1000) = 37.5 + 450 + 100 = 587.5
    val_shard_uncommon = calculate_disenchant_value(
        item_id=7003,
        item_meta={"quality": 2, "item_class": 2, "item_level": 22},
        latest_prices={10940: 50, 10939: 400, 10978: 1000},
    )
    assert val_shard_uncommon == 587.5

    # 4. Rare Blue Gear (level 33, quality 3, item_class 4)
    # Tier 31-35: shard_id 11138, chance: 100%
    # Price: shard=2500
    # Expected: 1.0 * 1.0 * 2500 = 2500.0
    val_rare = calculate_disenchant_value(
        item_id=7004,
        item_meta={"quality": 3, "item_class": 4, "item_level": 33},
        latest_prices={11138: 2500},
    )
    assert val_rare == 2500.0

    # 5. Rare High-Level Gear with Nexus Crystal chance (level 58, quality 3, item_class 2)
    # Tier 56-80: shard_id 14344 (99.5%), nexus_id 20725 (0.5%)
    # Prices: shard=10000, nexus=50000
    # Expected: 0.995 * 10000 + 0.005 * 50000 = 9950 + 250 = 10200.0
    val_nexus = calculate_disenchant_value(
        item_id=7005,
        item_meta={"quality": 3, "item_class": 2, "item_level": 58},
        latest_prices={14344: 10000, 20725: 50000},
    )
    assert val_nexus == 10200.0

    # 6. Epic Purple Gear (level 42, quality 4, item_class 4)
    # Tier 40-45: shard_id 11177, chance: 100%, min_qty: 2, max_qty: 4 -> avg_qty: 3.0
    # Price: shard=4000
    # Expected: 1.0 * 3.0 * 4000 = 12000.0
    val_epic = calculate_disenchant_value(
        item_id=7006,
        item_meta={"quality": 4, "item_class": 4, "item_level": 42},
        latest_prices={11177: 4000},
    )
    assert val_epic == 12000.0

    # 7. Non-equipment or white/grey items return 0.0
    assert calculate_disenchant_value(7007, item_meta={"quality": 1, "item_class": 4, "item_level": 20}) == 0.0
    assert calculate_disenchant_value(7008, item_meta={"quality": 2, "item_class": 7, "item_level": 20}) == 0.0
    assert calculate_disenchant_value(7009, item_meta={"quality": 2, "item_class": 4, "item_level": 0}) == 0.0
    assert calculate_disenchant_value(7010, item_meta={"quality": 2, "item_class": 4, "item_level": None}) == 0.0

    # 8. Real database integration with SQLite connection and latest_prices extraction
    with memory_db:
        memory_db.executemany(
            "INSERT INTO items (item_id, name, quality, item_class, category) VALUES (?, ?, ?, ?, ?)",
            [
                (7020, "Test DB Staff", 2, 2, "weapon"),
                (10940, "Strange Dust", 1, 7, "Trade Goods"),
                (10938, "Lesser Magic Essence", 1, 7, "Trade Goods"),
            ]
        )
        memory_db.executemany(
            "INSERT INTO price_snapshots (item_id, realm, price, timestamp) VALUES (?, 'Everlook', ?, 1000)",
            [
                (10940, 100),
                (10938, 300),
            ]
        )

    val_db = calculate_disenchant_value(
        item_id=7020,
        conn=memory_db,
        realm="Everlook",
        item_meta={"quality": 2, "item_class": 2, "item_level": 10},
    )
    assert val_db == 390.0


def test_export_sniper_addon_config_content_and_branches(tmp_path, memory_db):
    """Verify export_sniper_addon_config generates config.lua with version 2 and validates all 3 pricing branches."""
    # Ensure catalog cache holds item_level for equipment item 8003
    _CATALOG_CACHE[8003] = {
        "item_id": 8003,
        "name": "Green Chestpiece",
        "quality": 2,
        "item_class": 4,
        "item_level": 10,
        "category": "Armor",
    }

    with memory_db:
        memory_db.executemany(
            """INSERT INTO items (item_id, name, quality, item_class, category, vendor_sell_price)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [
                # 1. Direct flip liquid item (Trade Goods, class 7 -> intrinsic liquidity 0.95 >= 0.50)
                (8001, "Silk Cloth", 1, 7, "Trade Goods", 100),
                # 2. Illiquid item shielded from market branch (Recipe, class 9 -> liquidity 0.20 < 0.50)
                (8002, "Rare Recipe", 1, 9, "Recipe", 0),
                # 3. Disenchant equipment item (Green armor, quality 2, class 4, level 10)
                (8003, "Green Chestpiece", 2, 4, "Armor", 50),
                # 4. Vendor stream item with high vendor sell price
                (8004, "Vendor Trophy", 0, 15, "Junk", 5000),
                # 5. Low-value vendor trash (vendor_sell 10 - delta_vendor 75 < 0)
                (8005, "Low Trash", 0, 15, "Junk", 10),
                # Reagents for item 8003 disenchant calculation
                (10940, "Strange Dust", 1, 7, "Trade Goods", 10),
                (10938, "Lesser Magic Essence", 1, 7, "Trade Goods", 20),
            ]
        )
        memory_db.executemany(
            "INSERT INTO price_snapshots (item_id, realm, price, timestamp) VALUES (?, 'Everlook', ?, 5000)",
            [
                (8001, 10000),  # dbmarket = 10000
                (8002, 10000),  # dbmarket = 10000, but L = 0.20 -> term_market = 0
                (10940, 100),   # dust price = 100
                (10938, 300),   # essence price = 300
            ]
        )

    out_file = str(tmp_path / "custom_addon_dir" / "config.lua")
    result = export_sniper_addon_config(
        db_conn=memory_db,
        realm="Everlook",
        output_path=out_file,
        min_profit_copper=1250,
    )

    # 1. Verify output file exists
    assert os.path.exists(out_file)
    assert result["output_path"] == out_file
    assert result["min_profit_copper"] == 1250

    # 2. Parse file content using luadata
    config = luadata.read(out_file)
    assert isinstance(config, dict)
    assert config["version"] == 2
    assert config["min_profit_copper"] == 1250
    assert "targets" in config
    targets = config["targets"]
    assert isinstance(targets, dict)

    # 3. Direct flip branch: floor(0.65 * 10000) = 6500
    assert targets[8001] == 6500

    # 4. Illiquid shield: item 8002 has L < 0.50, so market stream is 0, MaxBuyout <= 0 -> omitted
    assert 8002 not in targets

    # 5. Disenchant branch: Green armor level 10 -> DE Value = 210.0 -> floor(0.80 * 210) = 168
    assert targets[8003] == 168

    # 6. Vendor branch: vendor_sell 5000, max_stack 20 -> delta_vendor = max(50, 75) = 75 -> 5000 - 75 = 4925
    assert targets[8004] == 4925

    # 7. MaxBuyout <= 0 cutoff: item 8005 has 10 - 75 = -65 <= 0 -> omitted
    assert 8005 not in targets


def test_sniper_addon_lua_and_toc_structure():
    """Verify addon folder files, TOC interface/version metadata, and valid Lua syntax parsing of config.lua."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    addon_dir = os.path.join(base_dir, "addons", "ForeverTrade_Sniper")

    toc_file = os.path.join(addon_dir, "ForeverTrade_Sniper.toc")
    lua_file = os.path.join(addon_dir, "ForeverTrade_Sniper.lua")
    cfg_file = os.path.join(addon_dir, "config.lua")

    # 1. Verify files exist
    assert os.path.isdir(addon_dir)
    assert os.path.isfile(toc_file)
    assert os.path.isfile(lua_file)
    assert os.path.isfile(cfg_file)

    # 2. Verify TOC metadata
    with open(toc_file, "r", encoding="utf-8") as f:
        toc_content = f.read()
    assert "## Title: ForeverTrade_Sniper" in toc_content
    assert "## Version: 2.1" in toc_content
    assert "## SavedVariables: FT_SNIPER_SAVED" in toc_content
    assert "config.lua" in toc_content
    assert "ForeverTrade_Sniper.lua" in toc_content

    # 3. Verify Lua addon logic exists
    with open(lua_file, "r", encoding="utf-8") as f:
        lua_content = f.read()
    assert len(lua_content) > 1000
    assert "ForeverTrade_Sniper" in lua_content
    assert "FT_SNIPER_CONFIG" in lua_content
    assert "AUCTION_ITEM_LIST_UPDATE" in lua_content
    assert "SetOverrideBindingClick" in lua_content
    assert "PlaceAuctionBid" in lua_content
    assert "QueryAuctionItems" in lua_content
    assert "FT_ExecuteVerifiedBuy" in lua_content

    # 4. Parse config.lua via luadata
    config = luadata.read(cfg_file)
    assert isinstance(config, dict)
    assert config.get("version") == 2
    assert "min_profit_copper" in config
    assert "targets" in config
    targets = config["targets"]
    assert isinstance(targets, dict)
    assert len(targets) > 0

    # Ensure all targets are integer keys with positive integer buyouts
    for item_id, max_buyout in targets.items():
        assert isinstance(item_id, int)
        assert isinstance(max_buyout, int)
        assert max_buyout > 0



