"""Analytics and arbitrage calculation engine for WoW: Forever."""

from collections import defaultdict
import math
import sqlite3
from typing import Optional


def get_vendor_arbitrage(
    conn: sqlite3.Connection,
    min_profit_copper: int = 50,
    realm: Optional[str] = None
) -> list[dict]:
    """Find auction lots where buyout price < vendor sell price with net profit >= min_profit_copper.

    Sorted by absolute profit in descending order.
    """
    cursor = conn.cursor()
    query = """
        WITH latest_snapshots AS (
            SELECT s.item_id, s.realm, s.price, s.timestamp, s.id,
                   ROW_NUMBER() OVER (
                       PARTITION BY s.item_id
                       ORDER BY s.timestamp DESC, s.id DESC
                   ) as rn
            FROM price_snapshots s
            WHERE (? IS NULL OR s.realm = ?)
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
          AND i.vendor_sell_price > ls.price
          AND (i.vendor_sell_price - ls.price) >= ?
        ORDER BY profit_copper DESC, ls.price ASC, ls.item_id ASC
    """
    cursor.execute(query, (realm, realm, min_profit_copper))
    results = []
    for row in cursor.fetchall():
        results.append({
            "item_id": int(row[0]),
            "name": str(row[1]),
            "quality": int(row[2] or 0),
            "buyout_price": int(row[3]),
            "price": int(row[3]),
            "vendor_sell_price": int(row[4]),
            "profit_copper": int(row[5]),
            "profit": int(row[5]),
            "realm": str(row[6]) if row[6] is not None else None,
            "timestamp": row[7],
        })
    return results


def get_crafting_margins(
    conn: sqlite3.Connection,
    has_bartering: bool = True,
    has_master_chef: bool = True,
    min_roi: float = 0.25,
    realm: Optional[str] = None
) -> list[dict]:
    """Calculate crafting margins accounting for Bartering (-5% NPC), Master Chef (1.1x Cooking), and AH cut (5%).

    Filters recipes where profit > 0 and ROI >= min_roi, sorted by ROI and profit descending.
    """
    cursor = conn.cursor()

    # 1. Latest snapshot price for each item
    query_prices = """
        WITH latest_snapshots AS (
            SELECT s.item_id, s.price,
                   ROW_NUMBER() OVER (
                       PARTITION BY s.item_id
                       ORDER BY s.timestamp DESC, s.id DESC
                   ) as rn
            FROM price_snapshots s
            WHERE (? IS NULL OR s.realm = ?)
        )
        SELECT item_id, price FROM latest_snapshots WHERE rn = 1
    """
    cursor.execute(query_prices, (realm, realm))
    latest_prices = {row[0]: row[1] for row in cursor.fetchall()}

    # 2. Items metadata
    cursor.execute("SELECT item_id, name, vendor_sell_price, vendor_buy_price, quality FROM items")
    items_by_id = {}
    for r in cursor.fetchall():
        items_by_id[r[0]] = {
            "item_id": r[0],
            "name": r[1],
            "vendor_sell_price": r[2] or 0,
            "vendor_buy_price": r[3] or 0,
            "quality": r[4] or 0,
        }

    # 3. Waylaid crates target mapping
    cursor.execute(
        "SELECT item_id, name, target_item_id, target_item_count, reward_copper FROM items WHERE category = 'waylaid_crate'"
    )
    waylaid_crates = {}
    for r in cursor.fetchall():
        target_id = r[2]
        if target_id is not None:
            waylaid_crates[target_id] = {
                "crate_item_id": r[0],
                "crate_name": r[1],
                "target_count": r[3],
                "reward_copper": r[4],
            }

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
    for recipe in recipes:
        recipe_id = recipe["recipe_id"]
        crafted_item_id = recipe["crafted_item_id"]

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
            reagent_meta = items_by_id.get(reagent_id)
            v_buy = reagent_meta["vendor_buy_price"] if reagent_meta else 0

            if v_buy > 0:
                unit_cost = (v_buy * 0.95) if has_bartering else float(v_buy)
                is_vendor = True
            else:
                unit_cost = latest_prices.get(reagent_id)
                is_vendor = False
                if unit_cost is None or unit_cost <= 0:
                    missing_reagent = True
                    break

            reagent_cost = unit_cost * count
            total_cost += reagent_cost
            reagents_detail.append({
                "item_id": reagent_id,
                "name": reagent_meta["name"] if reagent_meta else f"Item {reagent_id}",
                "count": count,
                "unit_cost": round(unit_cost, 2),
                "total_cost": round(reagent_cost, 2),
                "is_vendor": is_vendor,
            })

        if missing_reagent or total_cost <= 0:
            continue

        # Net revenue after 5% auction house commission
        revenue = p_out * 0.95 * expected_yield
        profit = revenue - total_cost
        roi = profit / total_cost

        if profit <= 0 or roi < min_roi:
            continue

        crate_info = waylaid_crates.get(crafted_item_id)
        crafted_meta = items_by_id.get(crafted_item_id, {})

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
            "profit": round(profit, 2),
            "profit_copper": int(round(profit)),
            "roi": round(roi, 4),
            "roi_percent": round(roi * 100, 2),
            "is_waylaid_target": crate_info is not None,
            "waylaid_crate": crate_info,
            "waylaid_crate_name": crate_info["crate_name"] if crate_info else None,
            "waylaid_crate_id": crate_info["crate_item_id"] if crate_info else None,
            "reagents": reagents_detail,
        })

    results.sort(key=lambda x: (x["roi"], x["profit"]), reverse=True)
    return results


def get_zscore_anomalies(
    conn: sqlite3.Connection,
    window_size: int = 15,
    z_buy_threshold: float = -1.8,
    z_sell_threshold: float = 1.6,
    realm: Optional[str] = None
) -> list[dict]:
    """Calculate rolling mean and standard deviation over window of <= window_size snapshots (min 2).

    Triggers STRONG_BUY for panic dumps (Z <= z_buy_threshold) and STRONG_SELL for local peaks (Z >= z_sell_threshold).
    Safe against division by zero (sigma == 0 -> Z = 0.0).
    """
    cursor = conn.cursor()
    query = """
        WITH ranked_snapshots AS (
            SELECT s.item_id, s.realm, s.price, s.timestamp, s.id,
                   ROW_NUMBER() OVER (
                       PARTITION BY s.item_id
                       ORDER BY s.timestamp DESC, s.id DESC
                   ) as rn
            FROM price_snapshots s
            WHERE (? IS NULL OR s.realm = ?)
        )
        SELECT rs.item_id, rs.realm, rs.price, rs.timestamp, rs.rn, i.name
        FROM ranked_snapshots rs
        JOIN items i ON rs.item_id = i.item_id
        WHERE rs.rn <= ?
        ORDER BY rs.item_id, rs.rn ASC
    """
    cursor.execute(query, (realm, realm, window_size))
    rows = cursor.fetchall()

    grouped = defaultdict(list)
    item_names = {}
    item_realms = {}
    for r in rows:
        item_id, r_realm, price, ts, rn, name = r[0], r[1], r[2], r[3], r[4], r[5]
        grouped[item_id].append((rn, price))
        item_names[item_id] = name
        item_realms[item_id] = r_realm

    results = []
    for item_id, entries in grouped.items():
        if len(entries) < 2:
            continue

        # entries sorted by rn ASC, so rn=1 is the most recent snapshot
        current_price = entries[0][1]
        all_prices = [p for _, p in entries]
        n = len(all_prices)
        mu = sum(all_prices) / n
        var = sum((p - mu) ** 2 for p in all_prices) / n
        sigma = math.sqrt(var)
        z = ((current_price - mu) / sigma) if sigma > 0 else 0.0

        signal = None
        if z <= z_buy_threshold:
            signal = "STRONG_BUY"
        elif z >= z_sell_threshold:
            signal = "STRONG_SELL"

        if signal:
            results.append({
                "item_id": item_id,
                "name": item_names[item_id],
                "current_price": current_price,
                "price": current_price,
                "mean_price": round(mu, 2),
                "mean": round(mu, 2),
                "std_dev": round(sigma, 4),
                "sigma": round(sigma, 4),
                "z_score": round(z, 4),
                "z": round(z, 4),
                "signal": signal,
                "sample_count": n,
                "n": n,
                "realm": item_realms[item_id],
            })

    results.sort(
        key=lambda a: (
            0 if a["signal"] == "STRONG_BUY" else 1,
            a["z_score"] if a["signal"] == "STRONG_BUY" else -a["z_score"]
        )
    )
    return results
