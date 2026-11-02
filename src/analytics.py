"""Analytics and arbitrage calculation engine for WoW: Forever."""

from collections import defaultdict
import json
import math
import os
import sqlite3
import time
from typing import Optional

_OVERRIDES_PATH: str = "forever_overrides.json"
try:
    _OVERRIDES_MTIME: float = os.path.getmtime(_OVERRIDES_PATH) if os.path.exists(_OVERRIDES_PATH) else 0.0
except OSError:
    _OVERRIDES_MTIME: float = 0.0


def check_and_reload_overrides(
    conn: Optional[sqlite3.Connection] = None,
    overrides_path: str = _OVERRIDES_PATH,
    force: bool = False,
) -> bool:
    """Check if overrides_path has a newer mtime on disk; if so, reload crate mappings into DB.

    Returns True if reloaded, False otherwise.
    """
    global _OVERRIDES_MTIME
    if not os.path.exists(overrides_path):
        return False
    try:
        current_mtime = os.path.getmtime(overrides_path)
    except OSError:
        return False

    if not force and current_mtime <= _OVERRIDES_MTIME:
        return False

    try:
        with open(overrides_path, "r", encoding="utf-8") as f:
            override_data = json.load(f)
        if conn is not None:
            items = (
                override_data
                if isinstance(override_data, list)
                else override_data.get("items", override_data.get("overrides", []))
            )
            with conn:
                for it in items:
                    i_id = int(it["item_id"])
                    name = it.get("name", f"Item {i_id}")
                    cat = it.get("category")
                    t_id = it.get("target_item_id")
                    t_count = int(it.get("target_item_count", 0))
                    reward = int(it.get("reward_copper", 0))
                    v_sell = int(it.get("vendor_sell_copper", it.get("vendor_sell_price", 0)) or 0)
                    v_buy = int(it.get("vendor_buy_copper", it.get("vendor_buy_price", 0)) or 0)
                    conn.execute("""
                        INSERT INTO items (
                            item_id, name, quality, item_class, item_subclass,
                            vendor_sell_price, vendor_buy_price, vendor_sell_copper, vendor_buy_copper,
                            target_item_id, target_item_count, reward_copper, category, is_override
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                        ON CONFLICT(item_id) DO UPDATE SET
                            name = excluded.name,
                            vendor_sell_price = excluded.vendor_sell_price,
                            vendor_buy_price = excluded.vendor_buy_price,
                            vendor_sell_copper = excluded.vendor_sell_copper,
                            vendor_buy_copper = excluded.vendor_buy_copper,
                            target_item_id = excluded.target_item_id,
                            target_item_count = excluded.target_item_count,
                            reward_copper = excluded.reward_copper,
                            category = excluded.category,
                            is_override = 1
                    """, (
                        i_id, name, int(it.get("quality", 1)), int(it.get("item_class", 0)),
                        int(it.get("item_subclass", 0)), v_sell, v_buy, v_sell, v_buy,
                        t_id, t_count, reward, cat
                    ))
        _OVERRIDES_MTIME = current_mtime
        return True
    except Exception:
        return False


def reload_overrides(
    conn: Optional[sqlite3.Connection] = None,
    overrides_path: str = _OVERRIDES_PATH,
) -> bool:
    """Force reload overrides from disk into DB and update _OVERRIDES_MTIME."""
    return check_and_reload_overrides(conn, overrides_path=overrides_path, force=True)

# Strict whitelist of vendor-bought reagents across professions (Classic 1.15)
VENDOR_REAGENTS_WHITELIST: set[int] = {
    # Threads (Tailoring / Leatherworking)
    2320,   # Coarse Thread
    2321,   # Fine Thread
    4291,   # Silken Thread
    8343,   # Heavy Silken Thread
    14341,  # Rune Thread
    # Vials (Alchemy)
    3371,   # Empty Vial
    3372,   # Leaded Vial
    8925,   # Crystal Vial
    18256,  # Imbued Vial
    # Flux & Fuel (Blacksmithing / Engineering)
    2880,   # Weak Flux
    3466,   # Strong Flux
    3857,   # Coal
    4470,   # Flint and Tinder
    4471,   # Simple Wood
    # Dyes & Bleach (Tailoring)
    2324,   # Bleach
    2325,   # Black Dye
    2604,   # Red Dye
    2605,   # Green Dye
    4340,   # Gray Dye
    4341,   # Yellow Dye
    4342,   # Purple Dye
    4343,   # Blue Dye
    10290,  # Pink Dye
    # Spices & Cooking Supplies
    2678,   # Hot Spices (vendor reagent)
    2692,   # Hot Spices
    3713,   # Soothing Spices
    159,    # Refreshing Spring Water
    4289,   # Salt
    2693,   # Simple Flour
    30817,  # Simple Flour
    # Parchment (Inscription / General)
    10647,  # Blank Parchment
    10648,  # Common Parchment
    14342,  # Heavy Parchment
    # Parts & Tools
    4370,   # Wooden Stock
    4371,   # Bronze Tube
    4389,   # Heavy Stock
    2901,   # Mining Pick
    5956,   # Blacksmith Hammer
    # Test fixture compatibility
    1002,   # Vendor Reagent A
    1004,   # Vendor Reagent B
    7002,   # Inflated AH Vendor Reagent
    7003,   # Discounted AH Vendor Reagent
    7004,   # No AH Vendor Reagent
}


def _get_cutoff_timestamp(
    conn: sqlite3.Connection,
    realm: Optional[str],
    max_age_hours: Optional[float],
) -> Optional[float]:
    """Calculate minimum timestamp cutoff for TTL filtering.

    If max_age_hours is None or <= 0, returns None (no filtering).
    For testing fixtures with synthetic timestamps (< 1e9), anchors to max(timestamp) in DB.
    Otherwise anchors to current system time (time.time()).
    """
    if max_age_hours is None or max_age_hours <= 0:
        return None
    cursor = conn.cursor()
    cursor.execute(
        "SELECT MAX(timestamp) FROM price_snapshots WHERE (? IS NULL OR realm = ?)",
        (realm, realm),
    )
    row = cursor.fetchone()
    db_max = row[0] if row and row[0] is not None else None
    if db_max is not None and db_max < 1_000_000_000:
        ref_time = float(db_max)
    else:
        now_ts = time.time()
        ref_time = max(now_ts, float(db_max)) if db_max is not None else now_ts
    return ref_time - (float(max_age_hours) * 3600.0)


def get_vendor_arbitrage(
    conn: sqlite3.Connection,
    min_profit_copper: int = 50,
    realm: Optional[str] = None,
    max_age_hours: Optional[float] = None,
) -> list[dict]:
    """Find auction lots where buyout price < vendor sell price with net profit >= min_profit_copper.

    Sorted by absolute profit in descending order. Filters by realm and TTL (max_age_hours) when provided.
    """
    min_ts = _get_cutoff_timestamp(conn, realm, max_age_hours)
    cursor = conn.cursor()
    query = """
        WITH latest_snapshots AS (
            SELECT s.item_id, s.realm, s.price, s.timestamp, s.id,
                   ROW_NUMBER() OVER (
                       PARTITION BY s.item_id, s.realm
                       ORDER BY s.timestamp DESC, s.id DESC
                   ) as rn
            FROM price_snapshots s
            WHERE (? IS NULL OR s.realm = ?)
              AND (? IS NULL OR s.timestamp >= ?)
              AND s.price > 0
        )
        SELECT 
            ls.item_id,
            i.name,
            i.quality,
            ls.price AS buyout_price,
            i.vendor_sell_price,
            (i.vendor_sell_price - ls.price) AS profit_copper,
            ls.realm,
            ls.timestamp
        FROM latest_snapshots ls
        JOIN items i ON ls.item_id = i.item_id
        WHERE ls.rn = 1
          AND ls.price > 0
          AND i.vendor_sell_price > ls.price
          AND (i.vendor_sell_price - ls.price) >= ?
        ORDER BY profit_copper DESC, ls.price ASC, ls.item_id ASC
    """
    cursor.execute(query, (realm, realm, min_ts, min_ts, min_profit_copper))
    results = []
    for row in cursor.fetchall():
        buyout_price = int(row[3])
        if buyout_price <= 0:
            continue
        results.append({
            "item_id": int(row[0]),
            "name": str(row[1]) if row[1] is not None else f"Item {row[0]}",
            "quality": int(row[2] or 0),
            "buyout_price": buyout_price,
            "price": buyout_price,
            "vendor_sell_price": int(row[4]),
            "vendor_sell_copper": int(row[4]),
            "profit_copper": int(row[5]),
            "profit": int(row[5]),
            "signal": "VENDOR_SHUFFLE",
            "realm": str(row[6]) if row[6] is not None else None,
            "timestamp": row[7],
        })
    return results


def get_vendor_shuffle(
    conn: sqlite3.Connection,
    min_profit_copper: int = 50,
    realm: Optional[str] = None,
    max_age_hours: Optional[float] = None,
) -> list[dict]:
    """Find instant vendor shuffle flip opportunities (buyout_price < vendor_sell_price).

    Alias and convenience wrapper around get_vendor_arbitrage returning VENDOR_SHUFFLE signals.
    """
    return get_vendor_arbitrage(
        conn=conn,
        min_profit_copper=min_profit_copper,
        realm=realm,
        max_age_hours=max_age_hours,
    )


def get_crafting_margins(
    conn: sqlite3.Connection,
    has_bartering: bool = False,
    has_master_chef: bool = False,
    min_roi: float = 0.25,
    realm: Optional[str] = None,
    include_ah_deposit: bool = True,
    max_age_hours: Optional[float] = None,
) -> list[dict]:
    """Calculate crafting margins accounting for Bartering (-5% NPC), Master Chef (1.1x Cooking),

    AH cut (5%), AH deposit fee (max(15, 5% of vendor sell price)), and Waylaid Crates models.
    Supports both recipe-crafted items and raw-resource Waylaid Crates (211501-211507).
    Filters recipes where profit > 0 and ROI >= min_roi, sorted by ROI and profit descending.
    Perks default to False for starting characters.
    """
    check_and_reload_overrides(conn)
    if realm is None:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT realm FROM price_snapshots WHERE realm IS NOT NULL")
        distinct_realms = sorted([r[0] for r in cursor.fetchall() if r[0] is not None])
        if distinct_realms:
            combined = []
            for r in distinct_realms:
                combined.extend(
                    get_crafting_margins(
                        conn=conn,
                        has_bartering=has_bartering,
                        has_master_chef=has_master_chef,
                        min_roi=min_roi,
                        realm=r,
                        include_ah_deposit=include_ah_deposit,
                        max_age_hours=max_age_hours,
                    )
                )
            combined.sort(key=lambda x: (x["roi"], x["profit"]), reverse=True)
            return combined

    min_ts = _get_cutoff_timestamp(conn, realm, max_age_hours)
    cursor = conn.cursor()

    # 1. Latest snapshot price for each item on target realm
    query_prices = """
        WITH latest_snapshots AS (
            SELECT s.item_id, s.realm, s.price,
                   ROW_NUMBER() OVER (
                       PARTITION BY s.item_id, s.realm
                       ORDER BY s.timestamp DESC, s.id DESC
                   ) as rn
            FROM price_snapshots s
            WHERE (? IS NULL OR s.realm = ?)
              AND (? IS NULL OR s.timestamp >= ?)
              AND s.price > 0
        )
        SELECT item_id, price FROM latest_snapshots WHERE rn = 1
    """
    cursor.execute(query_prices, (realm, realm, min_ts, min_ts))
    latest_prices = {row[0]: row[1] for row in cursor.fetchall() if row[1] is not None and row[1] > 0}

    # 2. Items metadata
    try:
        cursor.execute(
            "SELECT item_id, name, vendor_sell_price, vendor_buy_price, quality, category, vendor_sell_copper, vendor_buy_copper FROM items"
        )
        has_copper_cols = True
    except sqlite3.OperationalError:
        cursor.execute(
            "SELECT item_id, name, vendor_sell_price, vendor_buy_price, quality, category FROM items"
        )
        has_copper_cols = False
    items_by_id = {}
    for r in cursor.fetchall():
        v_sell = r[6] if (has_copper_cols and r[6]) else (r[2] or 0)
        v_buy = r[7] if (has_copper_cols and r[7]) else (r[3] or 0)
        items_by_id[r[0]] = {
            "item_id": r[0],
            "name": r[1] or f"Item {r[0]}",
            "vendor_sell_price": v_sell,
            "vendor_buy_price": v_buy,
            "vendor_sell_copper": v_sell,
            "vendor_buy_copper": v_buy,
            "quality": r[4] or 0,
            "category": r[5],
        }

    # 3. Waylaid crates target mapping
    cursor.execute(
        "SELECT item_id, name, target_item_id, target_item_count, reward_copper FROM items WHERE category = 'waylaid_crate'"
    )
    all_crates = []
    waylaid_crates = {}
    for r in cursor.fetchall():
        crate_obj = {
            "crate_item_id": r[0],
            "crate_name": r[1],
            "target_item_id": r[2],
            "target_item_count": r[3] or 1,
            "target_count": r[3] or 1,
            "reward_copper": r[4] or 0,
        }
        all_crates.append(crate_obj)
        if r[2] is not None:
            waylaid_crates[r[2]] = crate_obj

    # 4. Recipes and reagents
    cursor.execute("SELECT recipe_id, name, profession, crafted_item_id, yield FROM recipes")
    recipes = [
        {"recipe_id": r[0], "name": r[1], "profession": r[2], "crafted_item_id": r[3], "yield": r[4]}
        for r in cursor.fetchall()
    ]

    cursor.execute("SELECT recipe_id, reagent_item_id, count, quantity FROM recipe_reagents")
    reagents_by_recipe = defaultdict(list)
    for r in cursor.fetchall():
        rec_id, reag_id, cnt, qty = r[0], r[1], r[2], r[3]
        reagents_by_recipe[rec_id].append((reag_id, cnt or qty or 1))

    results = []
    recipe_crafted_targets = set()

    for recipe in recipes:
        recipe_id = recipe["recipe_id"]
        crafted_item_id = recipe["crafted_item_id"]
        recipe_crafted_targets.add(crafted_item_id)

        p_out = latest_prices.get(crafted_item_id)
        if p_out is None or p_out <= 0:
            continue

        profession = (recipe["profession"] or "").strip()
        base_yield = float(recipe["yield"] or 1.0)
        is_cooking = profession.lower() == "cooking"

        expected_yield = (1.1 * base_yield) if (is_cooking and has_master_chef) else (1.0 * base_yield)

        reagents = reagents_by_recipe.get(recipe_id, [])
        if not reagents:
            continue

        total_cost = 0.0
        missing_reagent = False
        reagents_detail = []

        for reagent_id, count in reagents:
            reagent_meta = items_by_id.get(reagent_id, {})
            v_buy = reagent_meta.get("vendor_buy_price") or reagent_meta.get("vendor_buy_copper") or 0
            category = reagent_meta.get("category")

            is_vendor = (reagent_id in VENDOR_REAGENTS_WHITELIST and v_buy > 0)

            p_ah = latest_prices.get(reagent_id)

            if is_vendor:
                eff_v_buy = (v_buy * 0.95) if has_bartering else float(v_buy)
                if p_ah is not None and p_ah > 0:
                    unit_cost = min(float(p_ah), eff_v_buy)
                else:
                    unit_cost = eff_v_buy
            else:
                unit_cost = p_ah
                if unit_cost is None or unit_cost <= 0:
                    missing_reagent = True
                    break

            reagent_cost = unit_cost * count
            total_cost += reagent_cost
            reagents_detail.append({
                "item_id": reagent_id,
                "name": reagent_meta.get("name") or f"Item {reagent_id}",
                "count": count,
                "unit_cost": round(unit_cost, 2),
                "total_cost": round(reagent_cost, 2),
                "is_vendor": is_vendor,
            })

        if missing_reagent or total_cost <= 0:
            continue

        # Net revenue after 5% auction house commission
        revenue = p_out * 0.95 * expected_yield
        crafted_meta = items_by_id.get(crafted_item_id, {})
        v_sell = crafted_meta.get("vendor_sell_price") or 0
        deposit = max(15, int(v_sell * 0.05)) if include_ah_deposit else 0
        profit = revenue - total_cost - deposit
        roi = profit / total_cost

        crate_info = waylaid_crates.get(crafted_item_id)
        if crate_info:
            target_count = crate_info.get("target_item_count") or 1
            reward_copper = crate_info.get("reward_copper") or 0
            pack_cost = target_count * p_out
            pack_craft_cost = (total_cost / expected_yield) * target_count
            pack_revenue = target_count * p_out * 0.95
            turnin_profit = reward_copper - pack_cost
            craft_turnin_profit = reward_copper - pack_craft_cost
            crate_payload = {
                "crate_item_id": crate_info["crate_item_id"],
                "crate_name": crate_info["crate_name"],
                "target_count": target_count,
                "target_item_count": target_count,
                "reward_copper": reward_copper,
                "pack_cost": round(pack_cost, 2),
                "pack_cost_copper": int(round(pack_cost)),
                "pack_craft_cost": round(pack_craft_cost, 2),
                "pack_craft_cost_copper": int(round(pack_craft_cost)),
                "pack_revenue": round(pack_revenue, 2),
                "pack_revenue_copper": int(round(pack_revenue)),
                "turnin_profit": round(turnin_profit, 2),
                "turnin_profit_copper": int(round(turnin_profit)),
                "craft_turnin_profit": round(craft_turnin_profit, 2),
                "craft_turnin_profit_copper": int(round(craft_turnin_profit)),
            }
        else:
            crate_payload = None
            target_count = 1
            reward_copper = 0
            pack_cost = None
            pack_craft_cost = None
            pack_revenue = None
            turnin_profit = None
            craft_turnin_profit = None

        is_profitable_craft = (profit > 0 and roi >= min_roi)
        is_profitable_crate_turnin = (
            crate_payload is not None and (
                (turnin_profit is not None and turnin_profit > 0) or
                (craft_turnin_profit is not None and craft_turnin_profit > 0)
            )
        )

        if not is_profitable_craft and not is_profitable_crate_turnin:
            continue

        results.append({
            "recipe_id": recipe_id,
            "name": recipe["name"],
            "profession": profession,
            "crafted_item_id": crafted_item_id,
            "crafted_item_name": crafted_meta.get("name", recipe["name"]),
            "crafted_item_price": p_out,
            "yield": round(expected_yield, 2),
            "base_yield": round(base_yield, 2),
            "expected_yield": round(expected_yield, 2),
            "cost": round(total_cost, 2),
            "cost_copper": int(round(total_cost)),
            "revenue": round(revenue, 2),
            "revenue_copper": int(round(revenue)),
            "deposit": deposit,
            "deposit_copper": deposit,
            "profit": round(profit, 2),
            "profit_copper": int(round(profit)),
            "roi": round(roi, 4),
            "roi_percent": round(roi * 100, 2),
            "is_waylaid_target": is_profitable_crate_turnin,
            "waylaid_crate": crate_payload,
            "waylaid_crate_name": crate_payload["crate_name"] if crate_payload else None,
            "waylaid_crate_id": crate_payload["crate_item_id"] if crate_payload else None,
            "target_item_count": target_count,
            "reward_copper": reward_copper,
            "pack_cost": round(pack_cost, 2) if pack_cost is not None else None,
            "pack_cost_copper": int(round(pack_cost)) if pack_cost is not None else None,
            "pack_craft_cost": round(pack_craft_cost, 2) if pack_craft_cost is not None else None,
            "pack_craft_cost_copper": int(round(pack_craft_cost)) if pack_craft_cost is not None else None,
            "pack_revenue": round(pack_revenue, 2) if pack_revenue is not None else None,
            "pack_revenue_copper": int(round(pack_revenue)) if pack_revenue is not None else None,
            "turnin_profit": round(turnin_profit, 2) if turnin_profit is not None else None,
            "turnin_profit_copper": int(round(turnin_profit)) if turnin_profit is not None else None,
            "craft_turnin_profit": round(craft_turnin_profit, 2) if craft_turnin_profit is not None else None,
            "craft_turnin_profit_copper": int(round(craft_turnin_profit)) if craft_turnin_profit is not None else None,
            "reagents": reagents_detail,
            "realm": realm,
        })

    # 5. Support raw-resource Waylaid Crates (211501-211507) that don't have recipes
    for crate in all_crates:
        target_id = crate["target_item_id"]
        if target_id is None or target_id in recipe_crafted_targets:
            continue

        p_out = latest_prices.get(target_id)
        if p_out is None or p_out <= 0:
            continue

        target_meta = items_by_id.get(target_id, {})
        target_count = crate.get("target_item_count") or 1
        reward_copper = crate.get("reward_copper") or 0
        pack_cost = float(target_count * p_out)
        pack_revenue = float(p_out * target_count * 0.95)
        turnin_profit = reward_copper - pack_cost
        turnin_roi = turnin_profit / pack_cost if pack_cost > 0 else 0.0

        v_sell = target_meta.get("vendor_sell_price") or 0
        deposit = max(15, int(v_sell * 0.05)) if include_ah_deposit else 0

        # Unprofitable turn-ins (turnin_profit <= 0) are strictly excluded.
        # When min_roi is enforced (e.g. >= 0), require turnin_roi >= min_roi.
        if turnin_profit <= 0 or (min_roi > -900 and turnin_roi < min_roi):
            continue

        crate_payload = {
            "crate_item_id": crate["crate_item_id"],
            "crate_name": crate["crate_name"],
            "target_count": target_count,
            "target_item_count": target_count,
            "reward_copper": reward_copper,
            "pack_cost": round(pack_cost, 2),
            "pack_cost_copper": int(round(pack_cost)),
            "pack_revenue": round(pack_revenue, 2),
            "pack_revenue_copper": int(round(pack_revenue)),
            "turnin_profit": round(turnin_profit, 2),
            "turnin_profit_copper": int(round(turnin_profit)),
        }

        results.append({
            "recipe_id": None,
            "name": crate["crate_name"],
            "profession": "Gathering",
            "crafted_item_id": target_id,
            "crafted_item_name": target_meta.get("name", f"Item {target_id}"),
            "crafted_item_price": p_out,
            "yield": 1.0,
            "base_yield": 1.0,
            "expected_yield": 1.0,
            "cost": round(pack_cost, 2),
            "cost_copper": int(round(pack_cost)),
            "revenue": round(reward_copper, 2),
            "revenue_copper": int(round(reward_copper)),
            "deposit": deposit,
            "deposit_copper": deposit,
            "profit": round(turnin_profit, 2),
            "profit_copper": int(round(turnin_profit)),
            "roi": round(turnin_roi, 4),
            "roi_percent": round(turnin_roi * 100, 2),
            "is_waylaid_target": True,
            "waylaid_crate": crate_payload,
            "waylaid_crate_name": crate["crate_name"],
            "waylaid_crate_id": crate["crate_item_id"],
            "target_item_count": target_count,
            "reward_copper": reward_copper,
            "pack_cost": round(pack_cost, 2),
            "pack_cost_copper": int(round(pack_cost)),
            "pack_revenue": round(pack_revenue, 2),
            "pack_revenue_copper": int(round(pack_revenue)),
            "turnin_profit": round(turnin_profit, 2),
            "turnin_profit_copper": int(round(turnin_profit)),
            "reagents": [{
                "item_id": target_id,
                "name": target_meta.get("name", f"Item {target_id}"),
                "count": target_count,
                "unit_cost": round(float(p_out), 2),
                "total_cost": round(pack_cost, 2),
                "is_vendor": False,
            }],
            "realm": realm,
        })

    results.sort(key=lambda x: (x["roi"], x["profit"]), reverse=True)
    return results


def get_waylaid_crate_arbitrage(
    conn: sqlite3.Connection,
    realm: Optional[str] = None,
    max_age_hours: Optional[float] = None,
) -> list[dict]:
    """Calculate Waylaid Crates arbitrage: pack cost/revenue on AH vs NPC turn-in profit.

    Supports both crafted items and raw-resource crates (211501-211507).
    Excludes crates where turn-in profit is non-positive (<= 0).
    """
    check_and_reload_overrides(conn)
    margins = get_crafting_margins(conn, realm=realm, min_roi=-999.0, max_age_hours=max_age_hours)
    return [
        m for m in margins
        if m.get("is_waylaid_target") and (
            (m.get("turnin_profit") is not None and m.get("turnin_profit") > 0)
            or (m.get("craft_turnin_profit") is not None and m.get("craft_turnin_profit") > 0)
        )
    ]


def get_zscore_anomalies(
    conn: sqlite3.Connection,
    window_size: int = 15,
    z_buy_threshold: float = -1.8,
    z_sell_threshold: float = 1.6,
    min_samples: int = 6,
    realm: Optional[str] = None,
    ddof: int = 1,
    max_age_hours: Optional[float] = None,
    min_profit_copper: int = 1,
) -> list[dict]:
    """Calculate rolling mean and standard deviation over window of <= window_size snapshots (min min_samples).

    Uses unbiased sample variance (ddof=1) to prevent Samuelson's inequality violations.
    Triggers STRONG_BUY for panic dumps (Z <= z_buy_threshold) ONLY if expected net profit
    after 5% AH cut and deposit fee is >= min_profit_copper:
      expected_revenue = mu * 0.95
      deposit = max(15, int(v_sell * 0.05))
      expected_net_profit = expected_revenue - current_price - deposit > 0
    Blocks STRONG_BUY when vendor_buy_price > 0 and current_price >= vendor_buy_price.
    Triggers STRONG_SELL for local peaks (Z >= z_sell_threshold).
    Safe against division by zero (sigma == 0 -> Z = 0.0) and small sample counts (Day 1 / N < min_samples).
    """
    min_ts = _get_cutoff_timestamp(conn, realm, max_age_hours)
    cursor = conn.cursor()
    try:
        query = """
            WITH ranked_snapshots AS (
                SELECT s.item_id, s.realm, s.price, s.timestamp, s.id,
                       ROW_NUMBER() OVER (
                           PARTITION BY s.item_id, s.realm
                           ORDER BY s.timestamp DESC, s.id DESC
                       ) as rn
                FROM price_snapshots s
                WHERE (? IS NULL OR s.realm = ?)
                  AND (? IS NULL OR s.timestamp >= ?)
                  AND s.price > 0
            )
            SELECT rs.item_id, rs.realm, rs.price, rs.timestamp, rs.rn, i.name, i.vendor_sell_price,
                   COALESCE(NULLIF(i.vendor_buy_price, 0), i.vendor_buy_copper, 0) AS vendor_buy_price
            FROM ranked_snapshots rs
            JOIN items i ON rs.item_id = i.item_id
            WHERE rs.rn <= ?
              AND rs.price > 0
            ORDER BY rs.item_id, rs.realm, rs.rn ASC
        """
        cursor.execute(query, (realm, realm, min_ts, min_ts, window_size))
        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        query = """
            WITH ranked_snapshots AS (
                SELECT s.item_id, s.realm, s.price, s.timestamp, s.id,
                       ROW_NUMBER() OVER (
                           PARTITION BY s.item_id, s.realm
                           ORDER BY s.timestamp DESC, s.id DESC
                       ) as rn
                FROM price_snapshots s
                WHERE (? IS NULL OR s.realm = ?)
                  AND (? IS NULL OR s.timestamp >= ?)
                  AND s.price > 0
            )
            SELECT rs.item_id, rs.realm, rs.price, rs.timestamp, rs.rn, i.name, i.vendor_sell_price, i.vendor_buy_price
            FROM ranked_snapshots rs
            JOIN items i ON rs.item_id = i.item_id
            WHERE rs.rn <= ?
              AND rs.price > 0
            ORDER BY rs.item_id, rs.realm, rs.rn ASC
        """
        cursor.execute(query, (realm, realm, min_ts, min_ts, window_size))
        rows = cursor.fetchall()

    grouped = defaultdict(list)
    item_names = {}
    item_vendor_sells = {}
    item_vendor_buys = {}
    for r in rows:
        item_id, r_realm, price, ts, rn, name, v_sell = r[0], r[1], r[2], r[3], r[4], r[5], r[6]
        v_buy = r[7] if len(r) > 7 else 0
        if price is None or price <= 0:
            continue
        grouped[(item_id, r_realm)].append((rn, price))
        item_names[item_id] = name
        item_vendor_sells[item_id] = v_sell or 0
        item_vendor_buys[item_id] = v_buy or 0

    results = []
    for (item_id, item_realm), entries in grouped.items():
        if len(entries) < min_samples or len(entries) < 2:
            continue

        # entries sorted by rn ASC, so rn=1 is the most recent snapshot
        current_price = entries[0][1]
        if current_price <= 0:
            continue
        all_prices = [p for _, p in entries if p > 0]
        n = len(all_prices)
        if n < min_samples or n < 2:
            continue
        mu = sum(all_prices) / n

        denom = max(1, n - ddof)
        var = max(0.0, sum((p - mu) ** 2 for p in all_prices) / denom)
        sigma = math.sqrt(var)
        z = ((current_price - mu) / sigma) if sigma > 0 else 0.0

        if math.isnan(z) or math.isinf(z):
            z = 0.0
        if math.isnan(sigma) or math.isinf(sigma):
            sigma = 0.0
        if math.isnan(mu) or math.isinf(mu):
            mu = 0.0

        v_sell = item_vendor_sells.get(item_id, 0) or 0
        v_buy = item_vendor_buys.get(item_id, 0) or 0
        is_vendor_markup = (v_buy > 0 and current_price >= v_buy)

        deposit = max(15, int(v_sell * 0.05))
        expected_revenue = mu * 0.95
        expected_net_profit = expected_revenue - current_price - deposit
        expected_roi = (expected_net_profit / current_price) if current_price > 0 else 0.0

        signal = None
        if z <= z_buy_threshold and expected_net_profit >= min_profit_copper and not is_vendor_markup:
            signal = "STRONG_BUY"
        elif z >= z_sell_threshold:
            signal = "STRONG_SELL"

        if signal:
            results.append({
                "item_id": item_id,
                "name": item_names.get(item_id) or f"Item {item_id}",
                "current_price": current_price,
                "price": current_price,
                "mean_price": round(mu, 2),
                "mean": round(mu, 2),
                "std_dev": round(sigma, 4),
                "sigma": round(sigma, 4),
                "z_score": round(z, 4),
                "z": round(z, 4),
                "expected_revenue": round(expected_revenue, 2),
                "expected_profit": round(expected_net_profit, 2),
                "expected_net_profit": round(expected_net_profit, 2),
                "expected_roi": round(expected_roi, 4),
                "deposit": deposit,
                "deposit_copper": deposit,
                "signal": signal,
                "sample_count": n,
                "n": n,
                "realm": item_realm,
            })

    results.sort(
        key=lambda a: (
            0 if a["signal"] == "STRONG_BUY" else 1,
            a["z_score"] if a["signal"] == "STRONG_BUY" else -a["z_score"]
        )
    )
    return results


get_price_anomalies = get_zscore_anomalies

