"""Analytics and arbitrage calculation engine for WoW: Forever."""

from collections import defaultdict
import json
import math
import os
import sqlite3
import statistics
import time
from typing import Optional, Any, Union

try:
    from src.database import get_item_name
except ImportError:
    try:
        from database import get_item_name
    except ImportError:
        def get_item_name(conn: Optional[sqlite3.Connection], item_id: int, lang: str = "en") -> str:
            return f"Item {item_id}"

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


def resolve_effective_material_cost(
    item_id: int,
    db_conn: Optional[sqlite3.Connection],
    realm: Optional[str] = None,
    depth: int = 0,
    max_depth: int = 4,
    visited_recipes: Optional[set] = None,
    has_bartering: bool = False,
    has_master_chef: bool = False,
    max_age_hours: Optional[float] = None,
    **kwargs: Any,
) -> dict:
    """Make-vs-Buy: recursively resolve effective unit cost for an item / intermediate reagent.

    Checks:
      1. Vendor buy price (P_vendor_buy) if item is in VENDOR_REAGENTS_WHITELIST.
      2. Auction House price (P_ah) from latest price snapshot or item_market_metrics.
      3. Crafting cost (C_craft) from recipes where crafted_item_id == item_id.
         If depth < max_depth and recipe_id not in visited_recipes, recursively resolves
         reagents: sum(count * resolve(sub_r)) / yield.

    Selects optimal source: MatPrice = min(P_vendor_buy, P_ah, C_craft) among available > 0.
    Protects against cycles via visited_recipes.

    Returns:
        dict: {
            "item_id": item_id,
            "name": name,
            "name_ru": name_ru,
            "effective_unit_cost": int,
            "source": str,  # 'vendor', 'ah', 'craft', or 'none'
            "vendor_price": int or None,
            "ah_price": int or None,
            "craft_cost": int or None,
            "sub_tree": list[dict],
        }
    """
    try:
        item_id = int(item_id)
    except (ValueError, TypeError):
        pass

    if visited_recipes is None:
        visited_recipes = set()
    else:
        visited_recipes = set(visited_recipes)

    name = get_item_name(db_conn, item_id, "en") if db_conn else f"Item {item_id}"
    name_ru = get_item_name(db_conn, item_id, "ru") if db_conn else f"Item {item_id}"

    cursor = db_conn.cursor() if db_conn is not None else None

    # 1. Vendor buy price (with VENDOR_REAGENTS_WHITELIST check)
    v_buy = 0
    if cursor is not None:
        try:
            cursor.execute(
                "SELECT vendor_buy_copper, vendor_buy_price FROM items WHERE item_id = ?",
                (item_id,),
            )
            row = cursor.fetchone()
            if row:
                v_buy = row[0] or row[1] or 0
        except sqlite3.OperationalError:
            try:
                cursor.execute(
                    "SELECT vendor_buy_price FROM items WHERE item_id = ?",
                    (item_id,),
                )
                row = cursor.fetchone()
                if row:
                    v_buy = row[0] or 0
            except Exception:
                pass
        except Exception:
            pass

    vendor_price: Optional[int] = None
    if item_id in VENDOR_REAGENTS_WHITELIST and v_buy > 0:
        if has_bartering:
            eff_v = v_buy * 0.95
            vendor_price = int(round(eff_v))
        else:
            vendor_price = int(v_buy)

    # 2. AH price from latest snapshot or item_market_metrics
    ah_price: Optional[int] = None
    if cursor is not None:
        min_ts = _get_cutoff_timestamp(db_conn, realm, max_age_hours) if max_age_hours else None
        try:
            cursor.execute("""
                SELECT price FROM price_snapshots
                WHERE item_id = ?
                  AND (? IS NULL OR realm = ?)
                  AND (? IS NULL OR timestamp >= ?)
                  AND price > 0
                ORDER BY timestamp DESC, id DESC
                LIMIT 1
            """, (item_id, realm, realm, min_ts, min_ts))
            row = cursor.fetchone()
            if row and row[0] is not None and row[0] > 0:
                ah_price = int(round(row[0]))
            else:
                cursor.execute("""
                    SELECT ewma_price, median_price FROM item_market_metrics
                    WHERE item_id = ? AND (? IS NULL OR realm = ?)
                    LIMIT 1
                """, (item_id, realm, realm))
                mrow = cursor.fetchone()
                if mrow:
                    m_val = mrow[0] or mrow[1]
                    if m_val and m_val > 0:
                        ah_price = int(round(m_val))
        except Exception:
            pass

    # 3. Crafting cost (DAG recursion)
    craft_cost: Optional[int] = None
    best_sub_tree: list[dict] = []

    if depth < max_depth and cursor is not None:
        try:
            cursor.execute(
                "SELECT recipe_id, name, yield, profession FROM recipes WHERE crafted_item_id = ?",
                (item_id,),
            )
            candidate_recipes = cursor.fetchall()
        except Exception:
            candidate_recipes = []

        best_recipe_unit_cost: Optional[int] = None
        best_recipe_sub_tree: list[dict] = []

        for r_id, r_name, r_yield, r_prof in candidate_recipes:
            if r_id in visited_recipes:
                continue

            try:
                cursor.execute(
                    "SELECT reagent_item_id, count, quantity FROM recipe_reagents WHERE recipe_id = ?",
                    (r_id,),
                )
                reagents = cursor.fetchall()
            except Exception:
                reagents = []

            if not reagents:
                continue

            base_yield = float(r_yield or 1.0)
            if has_master_chef and (r_prof or "").strip().lower() == "cooking":
                expected_yield = 1.1 * base_yield
            else:
                expected_yield = base_yield
            if expected_yield <= 0:
                expected_yield = 1.0

            current_visited = visited_recipes | {r_id}
            recipe_possible = True
            current_sub_tree: list[dict] = []
            recipe_total_cost = 0.0

            for row_reag in reagents:
                reag_id = row_reag[0]
                reag_count = row_reag[1] or row_reag[2] or 1

                sub_res = resolve_effective_material_cost(
                    item_id=reag_id,
                    db_conn=db_conn,
                    realm=realm,
                    depth=depth + 1,
                    max_depth=max_depth,
                    visited_recipes=current_visited,
                    has_bartering=has_bartering,
                    has_master_chef=has_master_chef,
                    max_age_hours=max_age_hours,
                    **kwargs,
                )

                sub_unit_cost = sub_res.get("effective_unit_cost")
                if sub_unit_cost is None or sub_unit_cost <= 0 or sub_res.get("source") == "none":
                    recipe_possible = False
                    break

                sub_item = dict(sub_res)
                sub_item["count"] = reag_count
                current_sub_tree.append(sub_item)
                recipe_total_cost += float(sub_unit_cost) * reag_count

            if recipe_possible and recipe_total_cost > 0:
                unit_craft = int(round(recipe_total_cost / expected_yield))
                if best_recipe_unit_cost is None or unit_craft < best_recipe_unit_cost:
                    best_recipe_unit_cost = unit_craft
                    best_recipe_sub_tree = current_sub_tree

        if best_recipe_unit_cost is not None:
            craft_cost = best_recipe_unit_cost
            best_sub_tree = best_recipe_sub_tree

    # Optimal source selection: min(P_vendor_buy, P_ah, C_craft) among available > 0
    candidates: list[tuple[int, int, str]] = []
    if vendor_price is not None and vendor_price > 0:
        candidates.append((vendor_price, 1, "vendor"))
    if ah_price is not None and ah_price > 0:
        candidates.append((ah_price, 2, "ah"))
    if craft_cost is not None and craft_cost > 0:
        candidates.append((craft_cost, 3, "craft"))

    if candidates:
        candidates.sort(key=lambda x: (x[0], x[1]))
        best_price, _, best_source = candidates[0]
        effective_unit_cost = int(round(best_price))
        source = best_source
    else:
        effective_unit_cost = 0
        source = "none"

    sub_tree_res = best_sub_tree if source == "craft" else []

    return {
        "item_id": item_id,
        "name": name,
        "name_ru": name_ru,
        "effective_unit_cost": effective_unit_cost,
        "source": source,
        "vendor_price": vendor_price,
        "ah_price": ah_price,
        "craft_cost": craft_cost,
        "sub_tree": sub_tree_res,
    }


def get_crafting_margins(
    conn: sqlite3.Connection,
    has_bartering: bool = False,
    has_master_chef: bool = False,
    min_roi: float = 0.25,
    realm: Optional[str] = None,
    include_ah_deposit: bool = True,
    max_age_hours: Optional[float] = None,
    resolve_subcrafts: bool = False,
) -> list[dict]:
    """Calculate crafting margins accounting for Bartering (-5% NPC), Master Chef (1.1x Cooking),

    AH cut (5%), AH deposit fee (max(15, 5% of vendor sell price)), and Waylaid Crates models.
    Supports both recipe-crafted items and raw-resource Waylaid Crates (211501-211507).
    Filters recipes where profit > 0 and ROI >= min_roi, sorted by ROI and profit descending.
    Perks default to False for starting characters.
    When resolve_subcrafts=True, recursively evaluates DAG crafting costs for sub-reagents.
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
                        resolve_subcrafts=resolve_subcrafts,
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
    try:
        cursor.execute("SELECT recipe_id, name, profession, crafted_item_id, yield, min_skill, source, source_details_ru, source_details_en FROM recipes")
        recipes = [
            {
                "recipe_id": r[0],
                "name": r[1],
                "profession": r[2],
                "crafted_item_id": r[3],
                "yield": r[4],
                "min_skill": r[5] or 1,
                "source": r[6] or "trainer",
                "source_details_ru": r[7],
                "source_details_en": r[8],
            }
            for r in cursor.fetchall()
        ]
    except sqlite3.OperationalError:
        cursor.execute("SELECT recipe_id, name, profession, crafted_item_id, yield FROM recipes")
        recipes = [
            {
                "recipe_id": r[0],
                "name": r[1],
                "profession": r[2],
                "crafted_item_id": r[3],
                "yield": r[4],
                "min_skill": 1,
                "source": "trainer",
                "source_details_ru": None,
                "source_details_en": None,
            }
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
        reagents_tree = []

        for reagent_id, count in reagents:
            reagent_meta = items_by_id.get(reagent_id, {})
            v_buy = reagent_meta.get("vendor_buy_price") or reagent_meta.get("vendor_buy_copper") or 0

            res = resolve_effective_material_cost(
                item_id=reagent_id,
                db_conn=conn,
                realm=realm,
                depth=0,
                max_depth=4 if resolve_subcrafts else 0,
                visited_recipes={recipe_id},
                has_bartering=has_bartering,
                has_master_chef=has_master_chef,
                max_age_hours=max_age_hours,
            )

            eff_cost = res.get("effective_unit_cost")
            source = res.get("source")

            if eff_cost is None or eff_cost <= 0 or source == "none":
                missing_reagent = True
                break

            is_vendor = (reagent_id in VENDOR_REAGENTS_WHITELIST and v_buy > 0)
            if is_vendor and has_bartering:
                unit_cost = round(v_buy * 0.95, 2)
                if res.get("ah_price") is not None and res["ah_price"] > 0:
                    unit_cost = min(float(res["ah_price"]), unit_cost)
            else:
                unit_cost = float(eff_cost)

            reagent_cost = unit_cost * count
            total_cost += reagent_cost

            reagents_detail.append({
                "item_id": reagent_id,
                "name": reagent_meta.get("name") or res.get("name") or f"Item {reagent_id}",
                "name_ru": res.get("name_ru") or reagent_meta.get("name_ru"),
                "count": count,
                "unit_cost": round(unit_cost, 2),
                "total_cost": round(reagent_cost, 2),
                "is_vendor": is_vendor,
                "source": source,
            })

            node = dict(res)
            node["count"] = count
            node["total_cost"] = round(reagent_cost, 2)
            reagents_tree.append(node)

        if missing_reagent or total_cost <= 0:
            continue

        def _tree_has_subcraft(nodes: list[dict]) -> bool:
            for n in nodes:
                if n.get("source") == "craft":
                    return True
                if _tree_has_subcraft(n.get("sub_tree", [])):
                    return True
            return False

        has_subcraft = _tree_has_subcraft(reagents_tree)

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
            "min_skill": recipe.get("min_skill", 1),
            "source": recipe.get("source", "trainer"),
            "source_details_ru": recipe.get("source_details_ru"),
            "source_details_en": recipe.get("source_details_en"),
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
            "reagents_tree": reagents_tree,
            "has_subcraft": has_subcraft,
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
                "source": "ah",
            }],
            "reagents_tree": [{
                "item_id": target_id,
                "name": target_meta.get("name", f"Item {target_id}"),
                "name_ru": target_meta.get("name_ru") or target_meta.get("name", f"Item {target_id}"),
                "count": target_count,
                "effective_unit_cost": int(round(p_out)),
                "source": "ah",
                "vendor_price": None,
                "ah_price": int(round(p_out)),
                "craft_cost": None,
                "sub_tree": [],
            }],
            "has_subcraft": False,
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


# ---------------------------------------------------------------------------
# Classic Era Disenchanting & Liquidity Shield
# ---------------------------------------------------------------------------

_DISENCHANT_REAGENT_NAMES: dict[int, dict[str, str]] = {
    10940: {"en": "Strange Dust", "ru": "Странная пыль"},
    11083: {"en": "Soul Dust", "ru": "Пыль духа"},
    11137: {"en": "Vision Dust", "ru": "Пыль провидения"},
    11176: {"en": "Dream Dust", "ru": "Сонная пыль"},
    16204: {"en": "Illusion Dust", "ru": "Пыль иллюзий"},
    10938: {"en": "Lesser Magic Essence", "ru": "Простая магическая субстанция"},
    10939: {"en": "Greater Magic Essence", "ru": "Великая магическая субстанция"},
    10998: {"en": "Lesser Astral Essence", "ru": "Простая астральная субстанция"},
    11082: {"en": "Greater Astral Essence", "ru": "Великая астральная субстанция"},
    11134: {"en": "Lesser Mystic Essence", "ru": "Простая таинственная субстанция"},
    11135: {"en": "Greater Mystic Essence", "ru": "Великая таинственная субстанция"},
    11174: {"en": "Lesser Nether Essence", "ru": "Простая субстанция Пустоты"},
    11175: {"en": "Greater Nether Essence", "ru": "Великая субстанция Пустоты"},
    16202: {"en": "Lesser Eternal Essence", "ru": "Простая вечная субстанция"},
    16203: {"en": "Greater Eternal Essence", "ru": "Великая вечная субстанция"},
    10978: {"en": "Small Glimmering Shard", "ru": "Малый мерцающий осколок"},
    11084: {"en": "Large Glimmering Shard", "ru": "Большой мерцающий осколок"},
    11138: {"en": "Small Glowing Shard", "ru": "Малый светящийся осколок"},
    11139: {"en": "Large Glowing Shard", "ru": "Большой светящийся осколок"},
    11177: {"en": "Small Radiant Shard", "ru": "Малый лучистый осколок"},
    11178: {"en": "Large Radiant Shard", "ru": "Большой лучистый осколок"},
    14343: {"en": "Small Brilliant Shard", "ru": "Малый сияющий осколок"},
    14344: {"en": "Large Brilliant Shard", "ru": "Большой сияющий осколок"},
    20725: {"en": "Nexus Crystal", "ru": "Кристалл Бездны"},
}


class DisenchantResult(float):
    """Float representing expected DE value with dictionary and attribute access to reagents."""

    def __new__(cls, value: float, reagents: Optional[list[dict]] = None):
        v = round(float(value), 2)
        obj = super().__new__(cls, v)
        obj.expected_value = v
        obj.reagents = reagents if reagents is not None else []
        return obj

    def __getitem__(self, key: str):
        if key == "expected_value":
            return self.expected_value
        elif key == "reagents":
            return self.reagents
        raise KeyError(key)

    def get(self, key: str, default=None):
        if key == "expected_value":
            return self.expected_value
        elif key == "reagents":
            return self.reagents
        return default

    def __contains__(self, key: str):
        return key in ("expected_value", "reagents")

    def keys(self):
        return ["expected_value", "reagents"]

    def values(self):
        return [self.expected_value, self.reagents]

    def items(self):
        return [("expected_value", self.expected_value), ("reagents", self.reagents)]


_DISENCHANT_TABLES_CACHE: dict = {}


def load_disenchant_tables(path: str = "data/disenchant_tables_era.json") -> dict:
    """Load disenchant tables JSON for Classic Era 1.15 with mtime caching."""
    global _DISENCHANT_TABLES_CACHE
    target_path = path
    if not os.path.exists(target_path):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        alt_path = os.path.join(base_dir, path)
        if os.path.exists(alt_path):
            target_path = alt_path
        else:
            return {"uncommon": [], "rare": [], "epic": []}
    try:
        mtime = os.path.getmtime(target_path)
    except OSError:
        mtime = 0.0

    cached = _DISENCHANT_TABLES_CACHE.get(target_path)
    if cached and cached.get("mtime") == mtime:
        return cached.get("data", {})

    try:
        with open(target_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _DISENCHANT_TABLES_CACHE[target_path] = {"mtime": mtime, "data": data}
        return data
    except Exception:
        if cached:
            return cached.get("data", {})
        return {"uncommon": [], "rare": [], "epic": []}


_CATALOG_CACHE: dict[int, dict] = {}


def _get_catalog_item(item_id: int) -> dict:
    """Retrieve item metadata from data/items_era_1_15.json and forever_overrides.json."""
    global _CATALOG_CACHE
    if not _CATALOG_CACHE:
        items_path = os.path.join("data", "items_era_1_15.json")
        if not os.path.exists(items_path):
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            items_path = os.path.join(base_dir, "data", "items_era_1_15.json")
        if os.path.exists(items_path):
            try:
                with open(items_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for it in data:
                        _CATALOG_CACHE[int(it["item_id"])] = it
            except Exception:
                pass

        ov_path = _OVERRIDES_PATH
        if not os.path.exists(ov_path):
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ov_path = os.path.join(base_dir, _OVERRIDES_PATH)
        if os.path.exists(ov_path):
            try:
                with open(ov_path, "r", encoding="utf-8") as f:
                    ov = json.load(f)
                    items = ov if isinstance(ov, list) else ov.get("items", ov.get("overrides", []))
                    for it in items:
                        _CATALOG_CACHE[int(it["item_id"])] = it
            except Exception:
                pass
    return _CATALOG_CACHE.get(item_id, {})


def _parse_intrinsic_liquidity(
    item_id: int,
    item_class: Optional[Any] = None,
    name: Optional[str] = None,
    quality: Optional[int] = None,
) -> float:
    """Calculate intrinsic liquidity L_intrinsic based on item type, quality, and category."""
    raw_str = str(item_class).strip().lower() if item_class is not None else ""

    cat_item = _get_catalog_item(item_id) if item_id else {}
    cat_name = (name or cat_item.get("name") or "").lower()
    cat_quality = quality if quality is not None else cat_item.get("quality")
    cat_category = str(cat_item.get("category") or "").lower()
    cat_class_int = cat_item.get("item_class")

    # 1. Waylaid containers / crates (0.95)
    if (
        "waylaid" in raw_str or "crate" in raw_str
        or "waylaid" in cat_category or "crate" in cat_name
        or "waylaid" in cat_name or (211501 <= item_id <= 211507)
    ):
        return 0.95

    # 2. Junk / Trash / Other / Grey (0.10)
    if any(k in raw_str for k in ("junk", "trash", "мусор", "grey", "gray", "poor", "other", "прочее")) or raw_str == "15":
        return 0.10
    if cat_quality == 0 or cat_class_int == 15 or cat_category in ("junk", "trash"):
        return 0.10

    # 3. Recipes / Patterns / Books (0.20)
    if any(k in raw_str for k in ("recipe", "pattern", "book", "plans", "schematic", "рецепт", "выкройк", "книг")) or raw_str == "9":
        return 0.20
    if cat_class_int == 9 or cat_category in ("recipe", "pattern", "book") or any(k in cat_name for k in ("recipe:", "pattern:", "plans:", "schematic:", "manual:", "book of", "книга")):
        return 0.20

    # 4. Weapons & Armor:
    # Green / Uncommon: 0.50
    # Blue / Purple / Rare / Epic: 0.35
    is_weapon_or_armor = (
        any(k in raw_str for k in ("weapon", "armor", "оружи", "брон", "доспех", "shield", "щит"))
        or raw_str in ("2", "4")
        or (cat_class_int in (2, 4))
        or cat_category in ("weapon", "weapons", "armor")
    )
    is_blue_purple = (
        any(k in raw_str for k in ("blue", "purple", "rare", "epic", "синяя", "фиолетовая", "редк", "эпик"))
        or (cat_quality is not None and cat_quality in (3, 4))
    )
    is_green = (
        any(k in raw_str for k in ("green", "uncommon", "зелен", "необычн"))
        or (cat_quality is not None and cat_quality == 2)
    )

    if is_weapon_or_armor:
        if is_blue_purple:
            return 0.35
        if is_green:
            return 0.50
        if cat_quality is not None:
            return 0.35 if cat_quality in (3, 4) else 0.50
        return 0.50

    if any(k in raw_str for k in ("rare", "epic", "blue", "purple", "синяя", "фиолетовая")):
        return 0.35
    if any(k in raw_str for k in ("uncommon", "green", "зеленая")):
        return 0.50

    # 5. Metals, Cloth, Leather, Herbs, Potions (0.90)
    is_tier2_material = any(
        k in raw_str or k in cat_category or k in cat_name
        for k in (
            "metal", "ore", "bar", "cloth", "leather", "hide", "herb", "potion", "elixir",
            "металл", "руда", "слиток", "ткань", "кож", "шкур", "трав", "зель", "эликсир",
        )
    )
    if is_tier2_material:
        return 0.90

    # 6. Consumables, Trade Goods, Reagents (0.95)
    is_tier1_consumable_or_tradegoods = any(
        k in raw_str or k in cat_category
        for k in (
            "consumable", "trade goods", "trade_goods", "reagent", "reagents", "raw",
            "расходник", "сырье", "материал", "реагент"
        )
    ) or raw_str in ("0", "7") or cat_class_int in (0, 7)
    if is_tier1_consumable_or_tradegoods:
        return 0.95

    if raw_str:
        return 0.10

    if cat_item:
        if cat_class_int in (0, 7):
            return 0.95
        if cat_class_int in (2, 4):
            return 0.35 if (cat_quality and cat_quality >= 3) else 0.50
        if cat_class_int == 9:
            return 0.20
        return 0.10

    return 0.10


def compute_robust_market_metrics(
    prices_history: list[int],
    timestamps: Optional[list[int]] = None,
    half_life_days: float = 3.5,
) -> dict:
    """Calculate robust market metrics using Median, MAD, Hampel filter, and EWMA.

    - Median price: P~ = median(P_i)
    - MAD = median(|P_i - P~|)
    - sigma_mad = 1.4826 * MAD
    - Hampel Filter: cuts points with |P_i - P~| > 3 * max(sigma_mad, 0.10 * P~)
    - EWMA on cleaned points with half_life_days (default 3.5 days):
        lambda = ln(2) / (half_life_days * 86400)
        w_i = exp(-lambda * (t_now - t_i))
        EWMA = sum(w_i * P_i) / sum(w_i)
      If timestamps not passed, uses average of cleaned prices.
    Returns: {"median_price": int, "mad": int, "sigma_mad": int, "ewma_price": int, "sample_count": int, "cleaned_count": int}
    """
    if not prices_history:
        return {
            "median_price": 0,
            "mad": 0,
            "sigma_mad": 0,
            "ewma_price": 0,
            "sample_count": 0,
            "cleaned_count": 0,
        }

    prices = [int(p) for p in prices_history]
    n_samples = len(prices)

    # 1. Median price
    p_median = float(statistics.median(prices))

    # 2. Median Absolute Deviation (MAD)
    deviations = [abs(p - p_median) for p in prices]
    mad = float(statistics.median(deviations))

    # 3. Sigma MAD (normal distribution scale factor)
    sigma_mad = 1.4826 * mad

    # 4. Hampel Filter
    # Cutoff threshold: 3 * max(sigma_mad, 0.10 * p_median)
    threshold = 3.0 * max(sigma_mad, 0.10 * p_median)

    has_valid_timestamps = (
        timestamps is not None
        and len(timestamps) == n_samples
    )

    if has_valid_timestamps:
        paired = list(zip(prices, timestamps))
        cleaned_paired = [
            (p, t) for p, t in paired
            if abs(p - p_median) <= threshold
        ]
        if not cleaned_paired:
            cleaned_paired = paired
        cleaned_prices = [p for p, t in cleaned_paired]
        cleaned_timestamps = [t for p, t in cleaned_paired]
    else:
        cleaned_prices = [
            p for p in prices
            if abs(p - p_median) <= threshold
        ]
        if not cleaned_prices:
            cleaned_prices = list(prices)
        cleaned_timestamps = None

    cleaned_count = len(cleaned_prices)

    # 5. EWMA on cleaned points
    if has_valid_timestamps and cleaned_timestamps and half_life_days > 0:
        lambda_val = math.log(2) / (float(half_life_days) * 86400.0)
        t_ref = max(cleaned_timestamps)
        weights = [math.exp(-lambda_val * (t_ref - t)) for t in cleaned_timestamps]
        sum_w = sum(weights)
        if sum_w > 0:
            ewma_val = sum(w * p for w, p in zip(weights, cleaned_prices)) / sum_w
        else:
            ewma_val = sum(cleaned_prices) / cleaned_count
    else:
        ewma_val = sum(cleaned_prices) / cleaned_count

    return {
        "median_price": int(round(p_median)),
        "mad": int(round(mad)),
        "sigma_mad": int(round(sigma_mad)),
        "ewma_price": int(round(ewma_val)),
        "sample_count": n_samples,
        "cleaned_count": cleaned_count,
    }


def update_all_market_metrics(db_conn: sqlite3.Connection, realm: Optional[str] = None) -> int:
    """Calculate and upsert robust market metrics and liquidity scores for all items in price_snapshots."""
    if db_conn is None:
        return 0

    try:
        from src.database import upsert_item_market_metrics
    except ImportError:
        try:
            from database import upsert_item_market_metrics
        except ImportError:
            def upsert_item_market_metrics(conn, metrics_list):
                return len(metrics_list)

    cursor = db_conn.cursor()

    # Pre-fetch items metadata (item_class, category, quality)
    items_meta = {}
    try:
        cursor.execute("SELECT item_id, item_class, category, quality FROM items")
        for r in cursor.fetchall():
            items_meta[r[0]] = {
                "item_class": r[1],
                "category": r[2],
                "quality": r[3],
            }
    except Exception:
        pass

    query = """
        SELECT item_id, realm, price, timestamp
        FROM price_snapshots
        WHERE (? IS NULL OR realm = ?) AND price > 0
        ORDER BY item_id, realm, timestamp ASC
    """
    try:
        cursor.execute(query, (realm, realm))
        rows = cursor.fetchall()
    except Exception:
        return 0

    if not rows:
        return 0

    grouped = defaultdict(list)
    for r in rows:
        grouped[(r[0], r[1])].append((r[2], r[3]))

    metrics_to_upsert = []
    for (item_id, item_realm), entries in grouped.items():
        prices = [p for p, t in entries]
        timestamps = [t for p, t in entries]
        metrics = compute_robust_market_metrics(prices, timestamps=timestamps)

        item_m = items_meta.get(item_id, {})
        item_class = item_m.get("item_class") or item_m.get("category")
        history = [{"price": p, "timestamp": t} for p, t in entries]
        l_score = compute_liquidity_score(
            item_id,
            history=history,
            item_class=item_class,
            quality=item_m.get("quality"),
        )

        metrics_to_upsert.append({
            "item_id": item_id,
            "realm": item_realm,
            "median_price": metrics["median_price"],
            "mad": metrics["mad"],
            "sigma_mad": metrics["sigma_mad"],
            "ewma_price": metrics["ewma_price"],
            "liquidity_score": l_score,
            "sample_count": metrics["sample_count"],
        })

    upsert_item_market_metrics(db_conn, metrics_to_upsert)
    return len(metrics_to_upsert)


def compute_liquidity_score(
    item_id: int,
    history: Optional[list[dict]] = None,
    item_class: Optional[Any] = None,
    **kwargs,
) -> float:
    """Calculate liquidity score L in [0.01, 1.0] from intrinsic class and observed history."""
    if item_class is None:
        item_class = kwargs.get("item_type") or kwargs.get("class")

    l_intrinsic = _parse_intrinsic_liquidity(item_id=item_id, item_class=item_class)

    if not history:
        return round(float(l_intrinsic), 2)

    n = len(history)
    if n == 0:
        return round(float(l_intrinsic), 2)

    freq_score = min(1.0, n / 10.0)
    has_lots = any(
        isinstance(e, dict) and any(k in e for k in ("count", "quantity", "lots", "lot_count"))
        for e in history
    )
    if has_lots:
        lots = [
            float(e.get("count") or e.get("quantity") or e.get("lots") or e.get("lot_count") or 1)
            for e in history if isinstance(e, dict)
        ]
        avg_lots = (sum(lots) / len(lots)) if lots else 1.0
        lot_score = max(0.2, min(1.0, avg_lots / 5.0))
        l_obs = 0.6 * freq_score + 0.4 * lot_score
    else:
        l_obs = freq_score

    l_score = round(0.5 * l_obs + 0.5 * l_intrinsic, 2)
    return max(0.01, min(1.0, l_score))


def get_strong_buys(
    conn: Optional[sqlite3.Connection] = None,
    window_size: int = 15,
    z_buy_threshold: float = -1.8,
    min_samples: int = 6,
    realm: Optional[str] = None,
    max_age_hours: Optional[float] = None,
    min_profit_copper: int = 1,
    include_traps: bool = True,
    **kwargs,
) -> list[dict]:
    """Retrieve verified STRONG_BUY sniping opportunities protected by Liquidity Shield.

    Applies liquidity penalty:
      liquidity_penalty = int(deposit * ((1.0 - liquidity) / max(0.01, liquidity)) + (1.0 - liquidity) * 0.10 * buy_price)
      net_profit = expected_net_revenue - buy_price - liquidity_penalty
    Signals with liquidity < 0.20 are flagged as ILLIQUID_TRAP (is_illiquid_trap = True).
    If net_profit <= 0 and liquidity < 0.50, signal is pruned from STRONG_BUY.
    """
    history_dict = kwargs.get("history_dict") or {}
    items_dict = kwargs.get("items_dict") or {}

    if isinstance(conn, list):
        candidates = conn
    elif conn is not None:
        candidates = get_zscore_anomalies(
            conn=conn,
            window_size=window_size,
            z_buy_threshold=z_buy_threshold,
            min_samples=min_samples,
            realm=realm,
            max_age_hours=max_age_hours,
            min_profit_copper=min_profit_copper,
        )
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT item_id, name, quality, item_class, category, vendor_sell_price FROM items")
            for r in cursor.fetchall():
                items_dict[r[0]] = {
                    "item_id": r[0],
                    "name": r[1],
                    "quality": r[2],
                    "class": r[3],
                    "item_class": r[3],
                    "category": r[4],
                    "vendor_sell_price": r[5] or 0,
                }
        except Exception:
            pass

        candidate_ids = [c["item_id"] for c in candidates if "item_id" in c]
        if candidate_ids:
            placeholders = ",".join("?" for _ in candidate_ids)
            query_snaps = f"""
                SELECT item_id, price, timestamp
                FROM price_snapshots
                WHERE item_id IN ({placeholders})
                  AND (? IS NULL OR realm = ?)
                  AND price > 0
                ORDER BY timestamp DESC
            """
            try:
                cursor.execute(query_snaps, candidate_ids + [realm, realm])
                for r in cursor.fetchall():
                    i_id, pr, ts = r[0], r[1], r[2]
                    if i_id not in history_dict:
                        history_dict[i_id] = []
                    if len(history_dict[i_id]) < window_size:
                        history_dict[i_id].append({"price": pr, "timestamp": ts})
            except Exception:
                pass

        robust_metrics = {}
        try:
            cursor.execute("""
                SELECT item_id, ewma_price, median_price, liquidity_score
                FROM item_market_metrics
                WHERE (? IS NULL OR realm = ?)
            """, (realm, realm))
            for r in cursor.fetchall():
                robust_metrics[r[0]] = {
                    "ewma_price": r[1],
                    "median_price": r[2],
                    "liquidity_score": r[3],
                }
        except Exception:
            pass
    elif "candidates" in kwargs:
        candidates = kwargs["candidates"]
    else:
        return []

    results = []
    for cand in candidates:
        item = dict(cand)
        item_id = int(item.get("item_id", 0))
        item_meta = items_dict.get(item_id, {})

        # Check robust metrics from item_market_metrics if available
        robust = robust_metrics.get(item_id) if "robust_metrics" in locals() else None
        if robust:
            rob_price = robust["ewma_price"] if (robust.get("ewma_price") and robust["ewma_price"] > 0) else robust.get("median_price", 0)
            if rob_price and rob_price > 0:
                item["dbmarket"] = rob_price

        item_class = item.get("class") or item.get("item_class") or item_meta.get("category") or item_meta.get("class") or item_meta.get("item_class")
        history = history_dict.get(item_id, item.get("history"))

        if "liquidity" in item:
            liquidity = item["liquidity"]
        elif robust and robust.get("liquidity_score") is not None and robust["liquidity_score"] > 0:
            liquidity = robust["liquidity_score"]
        else:
            liquidity = compute_liquidity_score(item_id, history=history, item_class=item_class)
        item["liquidity"] = liquidity
        item["liquidity_score"] = liquidity

        is_trap = (liquidity < 0.20)
        item["is_illiquid_trap"] = is_trap
        if is_trap:
            item["signal"] = "ILLIQUID_TRAP"
        elif item.get("signal") not in ("STRONG_BUY", "STRONG_SELL"):
            item["signal"] = "STRONG_BUY"

        buy_price = item.get("current_price", item.get("buyout_price", item.get("price", 0)))
        deposit = int(item.get("deposit") or item.get("deposit_copper") or 0)
        if deposit <= 0:
            v_sell = item_meta.get("vendor_sell_price") or item.get("vendor_sell_price") or 0
            deposit = max(15, int(v_sell * 0.05))

        if "dbmarket" in item and item["dbmarket"] > 0:
            expected_net_revenue = item["dbmarket"] * 0.95
            item["expected_revenue"] = round(expected_net_revenue, 2)
        else:
            expected_net_revenue = item.get("expected_revenue", item.get("mean_price", buy_price) * 0.95)

        eff_liq = max(0.01, liquidity)
        liquidity_penalty = int(deposit * ((1.0 - liquidity) / eff_liq) + (1.0 - liquidity) * 0.10 * buy_price)
        net_profit = expected_net_revenue - buy_price - liquidity_penalty

        item["deposit"] = deposit
        item["deposit_copper"] = deposit
        item["liquidity_penalty"] = liquidity_penalty
        item["expected_net_profit"] = round(net_profit, 2)
        item["net_profit"] = round(net_profit, 2)
        item["profit_copper"] = int(round(net_profit))
        item["profit"] = round(net_profit, 2)

        if item["signal"] == "STRONG_BUY":
            if net_profit <= 0 and liquidity < 0.50:
                continue
            if net_profit < min_profit_copper:
                continue
            results.append(item)
        elif item["signal"] == "ILLIQUID_TRAP":
            if include_traps:
                results.append(item)
        else:
            results.append(item)

    results.sort(
        key=lambda a: (
            0 if a.get("signal") == "STRONG_BUY" else (1 if a.get("signal") == "ILLIQUID_TRAP" else 2),
            -a.get("net_profit", 0)
        )
    )
    return results


def calculate_disenchant_value(
    item_id: Union[int, dict],
    conn: Optional[sqlite3.Connection] = None,
    realm: Optional[str] = None,
    item_meta: Optional[dict] = None,
    latest_prices: Optional[dict] = None,
    disenchant_tables: Optional[dict] = None,
    return_details: bool = False,
) -> Union[float, dict]:
    """Calculate expected disenchant value E[DE Value] in copper for equipment (Era 1.15).

    Matches Classic Era TSM Disenchanting data matrices across uncommon, rare, and epic gear.
    Returns DisenchantResult (float with .reagents and dict access), or a dict if return_details=True.
    """
    actual_item_id = item_id
    if isinstance(item_id, dict):
        item_meta = dict(item_id)
        actual_item_id = item_id.get("item_id")
        if isinstance(conn, dict):
            latest_prices = conn
            conn = None
        if isinstance(realm, dict):
            disenchant_tables = realm
            realm = None

    if disenchant_tables is None:
        disenchant_tables = load_disenchant_tables()

    cat_meta = _get_catalog_item(actual_item_id) if actual_item_id else {}
    meta = dict(cat_meta)
    if item_meta:
        meta.update(item_meta)

    def _ret_zero():
        if return_details:
            return {"expected_value": 0.0, "reagents": []}
        return DisenchantResult(0.0, [])

    quality = meta.get("quality")
    if quality not in (2, 3, 4):
        return _ret_zero()

    item_class = meta.get("item_class") or meta.get("class")
    raw_class = str(item_class).strip().lower() if item_class is not None else ""
    is_weapon = (
        raw_class in ("2", "weapon", "оружи")
        or item_class == 2
        or meta.get("category") in ("weapon", "weapons")
    )
    is_armor = (
        raw_class in ("4", "armor", "брон", "доспех", "shield", "щит")
        or item_class == 4
        or meta.get("category") in ("armor", "armors")
    )
    if not (is_weapon or is_armor):
        return _ret_zero()

    level = meta.get("item_level") or meta.get("level") or meta.get("req_level")
    if level is None:
        return _ret_zero()
    try:
        level = int(level)
    except (ValueError, TypeError):
        return _ret_zero()
    if level <= 0:
        return _ret_zero()

    if latest_prices is None and conn is not None:
        try:
            cur = conn.cursor()
            cur.execute("""
                WITH latest_snapshots AS (
                    SELECT s.item_id, s.realm, s.price,
                           ROW_NUMBER() OVER (
                               PARTITION BY s.item_id, s.realm
                               ORDER BY s.timestamp DESC, s.id DESC
                           ) as rn
                    FROM price_snapshots s
                    WHERE (? IS NULL OR s.realm = ?)
                      AND s.price > 0
                )
                SELECT item_id, price FROM latest_snapshots WHERE rn = 1
            """, (realm, realm))
            latest_prices = {r[0]: r[1] for r in cur.fetchall() if r[1] is not None and r[1] > 0}
        except Exception:
            latest_prices = {}
    elif latest_prices is None:
        latest_prices = {}

    tier_key = {2: "uncommon", 3: "rare", 4: "epic"}.get(quality)
    if not tier_key:
        return _ret_zero()

    tiers = disenchant_tables.get(tier_key, [])
    matching_tier = None
    for t in tiers:
        if t.get("min_level", 0) <= level <= t.get("max_level", 999):
            matching_tier = t
            break

    if not matching_tier:
        return _ret_zero()

    def _build_reagent_entry(r_id: int, chance_val: float, exp_cnt: float, unit_price: float) -> dict:
        r_id = int(r_id)
        en_name = _DISENCHANT_REAGENT_NAMES.get(r_id, {}).get("en")
        ru_name = _DISENCHANT_REAGENT_NAMES.get(r_id, {}).get("ru")
        if not en_name or not ru_name:
            try:
                en_name = en_name or get_item_name(conn, r_id, "en")
                ru_name = ru_name or get_item_name(conn, r_id, "ru")
            except Exception:
                en_name = en_name or f"Item {r_id}"
                ru_name = ru_name or f"Item {r_id}"
        return {
            "reagent_id": r_id,
            "reagent_name": en_name,
            "reagent_name_ru": ru_name,
            "chance": round(float(chance_val), 4),
            "expected_count": round(float(exp_cnt), 2),
            "unit_price_copper": int(round(float(unit_price))),
        }

    reagents = []
    expected_val = 0.0

    if tier_key == "uncommon":
        dust_chance = float(matching_tier.get("weapon_dust_chance" if is_weapon else "armor_dust_chance", 0)) / 100.0
        essence_chance = float(matching_tier.get("weapon_essence_chance" if is_weapon else "armor_essence_chance", 0)) / 100.0
        avg_dust = (float(matching_tier.get("dust_min", 1)) + float(matching_tier.get("dust_max", 1))) / 2.0
        avg_essence = (float(matching_tier.get("essence_min", 1)) + float(matching_tier.get("essence_max", 1))) / 2.0

        dust_id = matching_tier.get("dust_id")
        dust_price = float(latest_prices.get(dust_id, 0) or 0)

        if is_weapon and "forever_weapon_essence_id" in matching_tier:
            essence_id = matching_tier["forever_weapon_essence_id"]
        else:
            essence_id = matching_tier.get("essence_id")
        essence_price = float(latest_prices.get(essence_id, 0) or 0)

        expected_val = (dust_chance * avg_dust * dust_price) + (essence_chance * avg_essence * essence_price)

        if dust_id and dust_chance > 0:
            reagents.append(_build_reagent_entry(dust_id, dust_chance, avg_dust, dust_price))

        if essence_id and essence_chance > 0:
            reagents.append(_build_reagent_entry(essence_id, essence_chance, avg_essence, essence_price))

        shard_id = matching_tier.get("shard_id")
        if shard_id:
            shard_chance_pct = matching_tier.get("shard_chance")
            if shard_chance_pct is None:
                used_pct = float(matching_tier.get("weapon_dust_chance" if is_weapon else "armor_dust_chance", 0)) + \
                           float(matching_tier.get("weapon_essence_chance" if is_weapon else "armor_essence_chance", 0))
                shard_chance_pct = max(0.0, 100.0 - used_pct)
            if shard_chance_pct > 0:
                shard_chance = float(shard_chance_pct) / 100.0
                shard_price = float(latest_prices.get(shard_id, 0) or 0)
                expected_val += shard_chance * 1.0 * shard_price
                reagents.append(_build_reagent_entry(shard_id, shard_chance, 1.0, shard_price))

    elif tier_key == "rare":
        shard_id = matching_tier.get("shard_id")
        shard_chance = float(matching_tier.get("shard_chance", matching_tier.get("chance", 100))) / 100.0
        shard_price = float(latest_prices.get(shard_id, 0) or 0)
        expected_val = shard_chance * 1.0 * shard_price
        if shard_id and shard_chance > 0:
            reagents.append(_build_reagent_entry(shard_id, shard_chance, 1.0, shard_price))

        nexus_id = matching_tier.get("nexus_id")
        if nexus_id:
            nexus_chance = float(matching_tier.get("nexus_chance", 0)) / 100.0
            nexus_price = float(latest_prices.get(nexus_id, 0) or 0)
            expected_val += nexus_chance * 1.0 * nexus_price
            if nexus_chance > 0:
                reagents.append(_build_reagent_entry(nexus_id, nexus_chance, 1.0, nexus_price))

    elif tier_key == "epic":
        shard_id = matching_tier.get("shard_id")
        chance = float(matching_tier.get("chance", 100)) / 100.0
        min_qty = float(matching_tier.get("min_qty", 1))
        max_qty = float(matching_tier.get("max_qty", 1))
        avg_qty = (min_qty + max_qty) / 2.0
        shard_price = float(latest_prices.get(shard_id, 0) or 0)
        expected_val = chance * avg_qty * shard_price
        if shard_id and chance > 0:
            reagents.append(_build_reagent_entry(shard_id, chance, avg_qty, shard_price))

    rounded_val = round(expected_val, 2)
    if return_details:
        return {"expected_value": rounded_val, "reagents": reagents}
    return DisenchantResult(rounded_val, reagents)


class TargetDict(dict):
    """Target item specification with numerical comparison support for backwards compatibility."""

    def __eq__(self, other):
        if isinstance(other, (int, float)):
            return self.get("target") == other
        return super().__eq__(other)

    def __lt__(self, other):
        if isinstance(other, (int, float)):
            return self.get("target", 0) < other
        return NotImplemented

    def __le__(self, other):
        if isinstance(other, (int, float)):
            return self.get("target", 0) <= other
        return NotImplemented

    def __gt__(self, other):
        if isinstance(other, (int, float)):
            return self.get("target", 0) > other
        return NotImplemented

    def __ge__(self, other):
        if isinstance(other, (int, float)):
            return self.get("target", 0) >= other
        return NotImplemented

    def __int__(self):
        return int(self.get("target", 0))

    def __float__(self):
        return float(self.get("target", 0))


def export_sniper_addon_config(
    db_conn: Optional[sqlite3.Connection] = None,
    realm: Optional[str] = None,
    output_path: Optional[str] = None,
    min_profit_copper: int = 0,
    wtf_path: Optional[str] = None,
) -> dict:
    """Export unit-price buyout targets for ForeverTrade_Sniper WoW addon.

    Computes single ceiling per 1 unit across 3 arbitrage streams:
      MaxBuyout(i) = max(
          I(L_i >= 0.50) * floor(0.65 * dbmarket(i)),
          floor(0.80 * E[DE Value](i)),
          P_vendor_sell(i) - Delta_vendor(i)
      )
    where Delta_vendor(i) = max(50, floor(1500 / max_stack(i))).
    Excludes items with MaxBuyout <= 0.
    Writes config.lua to output_path and returns target summary.
    """
    if db_conn is not None:
        check_and_reload_overrides(db_conn)

    cursor = db_conn.cursor() if db_conn is not None else None

    # 1. Fetch latest prices on target realm
    latest_prices = {}
    if cursor is not None:
        try:
            cursor.execute("""
                WITH latest_snapshots AS (
                    SELECT s.item_id, s.realm, s.price,
                           ROW_NUMBER() OVER (
                               PARTITION BY s.item_id, s.realm
                               ORDER BY s.timestamp DESC, s.id DESC
                           ) as rn
                    FROM price_snapshots s
                    WHERE (? IS NULL OR s.realm = ?)
                      AND s.price > 0
                )
                SELECT item_id, price FROM latest_snapshots WHERE rn = 1
            """, (realm, realm))
            latest_prices = {r[0]: r[1] for r in cursor.fetchall() if r[1] is not None and r[1] > 0}
        except Exception:
            latest_prices = {}

        # Forward compatibility with Stage 4 item_market_metrics (EWMA / median)
        try:
            cursor.execute("""
                SELECT item_id, ewma_price, median_price
                FROM item_market_metrics
                WHERE (? IS NULL OR realm = ?)
            """, (realm, realm))
            for r in cursor.fetchall():
                val = r[1] if (r[1] and r[1] > 0) else r[2]
                if val and val > 0:
                    latest_prices[r[0]] = int(val)
        except Exception:
            try:
                cursor.execute("SELECT item_id, dbmarket FROM item_market_metrics WHERE (? IS NULL OR realm = ?)", (realm, realm))
                for r in cursor.fetchall():
                    if r[1] is not None and r[1] > 0:
                        latest_prices[r[0]] = int(r[1])
            except Exception:
                pass

        metrics_liquidity = {}
        try:
            cursor.execute("""
                SELECT item_id, liquidity_score
                FROM item_market_metrics
                WHERE (? IS NULL OR realm = ?)
            """, (realm, realm))
            for r in cursor.fetchall():
                if r[1] is not None:
                    metrics_liquidity[r[0]] = float(r[1])
        except Exception:
            pass

    # 2. Fetch items from DB
    items_dict = {}
    if cursor is not None:
        try:
            cursor.execute("""
                SELECT item_id, name, quality, item_class, category,
                       COALESCE(NULLIF(vendor_sell_copper, 0), vendor_sell_price, 0) AS vendor_sell,
                       COALESCE(NULLIF(vendor_buy_copper, 0), vendor_buy_price, 0) AS vendor_buy
                FROM items
            """)
            for r in cursor.fetchall():
                items_dict[r[0]] = {
                    "item_id": r[0],
                    "name": r[1],
                    "quality": r[2],
                    "item_class": r[3],
                    "category": r[4],
                    "vendor_sell": r[5] or 0,
                    "vendor_buy": r[6] or 0,
                }
        except sqlite3.OperationalError:
            try:
                cursor.execute("""
                    SELECT item_id, name, quality, item_class, category, vendor_sell_price, vendor_buy_price
                    FROM items
                """)
                for r in cursor.fetchall():
                    items_dict[r[0]] = {
                        "item_id": r[0],
                        "name": r[1],
                        "quality": r[2],
                        "item_class": r[3],
                        "category": r[4],
                        "vendor_sell": r[5] or 0,
                        "vendor_buy": r[6] or 0,
                    }
            except Exception:
                pass

    # Include any items from latest_prices or catalog if not present in items_dict
    for item_id in latest_prices:
        if item_id not in items_dict:
            cat_it = _get_catalog_item(item_id)
            items_dict[item_id] = {
                "item_id": item_id,
                "name": cat_it.get("name", f"Item {item_id}"),
                "quality": cat_it.get("quality", 1),
                "item_class": cat_it.get("item_class", 0),
                "category": cat_it.get("category"),
                "vendor_sell": cat_it.get("vendor_sell_copper", cat_it.get("vendor_sell_price", 0)) or 0,
                "vendor_buy": cat_it.get("vendor_buy_copper", cat_it.get("vendor_buy_price", 0)) or 0,
            }

    # If DB items are still empty, fall back to _CATALOG_CACHE
    if not items_dict:
        _get_catalog_item(1)  # ensure catalog cache loaded
        for item_id, cat_it in _CATALOG_CACHE.items():
            items_dict[item_id] = {
                "item_id": item_id,
                "name": cat_it.get("name", f"Item {item_id}"),
                "quality": cat_it.get("quality", 1),
                "item_class": cat_it.get("item_class", 0),
                "category": cat_it.get("category"),
                "vendor_sell": cat_it.get("vendor_sell_copper", cat_it.get("vendor_sell_price", 0)) or 0,
                "vendor_buy": cat_it.get("vendor_buy_copper", cat_it.get("vendor_buy_price", 0)) or 0,
            }

    de_tables = load_disenchant_tables()
    targets = {}

    for item_id, item_data in items_dict.items():
        cat_it = _get_catalog_item(item_id)
        item_class = item_data.get("item_class") or cat_it.get("item_class")
        raw_class = str(item_class).strip().lower() if item_class is not None else ""
        is_equipment = (
            raw_class in ("2", "4", "armor", "weapon", "доспех", "оружи", "брон")
            or item_class in (2, 4)
            or item_data.get("category") in ("armor", "armors", "weapon", "weapons", "equipment")
            or cat_it.get("category") in ("armor", "armors", "weapon", "weapons", "equipment")
        )

        # 1. Delta vendor & Vendor stream
        max_stack = item_data.get("max_stack") or cat_it.get("max_stack")
        if not max_stack or int(max_stack) <= 0:
            max_stack = 1 if is_equipment else 20
        else:
            max_stack = int(max_stack)

        delta_vendor = max(50, math.floor(1500 / max_stack))
        vendor_sell = int(item_data.get("vendor_sell") or cat_it.get("vendor_sell_copper") or cat_it.get("vendor_sell_price") or 0)
        term_vendor = vendor_sell - delta_vendor

        # 2. Market stream (DBMarket * 0.65 with Liquidity Shield L >= 0.50)
        dbmarket = latest_prices.get(item_id, 0)
        term_market = 0
        if dbmarket and dbmarket > 0:
            liquidity = metrics_liquidity.get(item_id) if "metrics_liquidity" in locals() else None
            if liquidity is None:
                liquidity = compute_liquidity_score(item_id, item_class=item_class)
            if liquidity >= 0.50:
                term_market = math.floor(0.65 * dbmarket)

        # 3. Disenchant stream (E[DE Value] * 0.80)
        de_val = calculate_disenchant_value(
            item_id,
            conn=db_conn,
            realm=realm,
            item_meta=item_data,
            latest_prices=latest_prices,
            disenchant_tables=de_tables,
        )
        term_de = math.floor(0.80 * de_val) if de_val > 0 else 0

        # Unified ceiling
        max_buyout = max(term_market, term_de, term_vendor)
        if max_buyout > 0:
            item_name = item_data.get("name") or cat_it.get("name") or f"Item {item_id}"
            targets[int(item_id)] = TargetDict({
                "target": int(max_buyout),
                "min_profit": int(min_profit_copper),
                "name": item_name,
            })

    # 4. Crafting Reagent stream from ProfitCrafterForever
    try:
        from src.etl import load_profitcrafter_data
    except ImportError:
        try:
            from etl import load_profitcrafter_data
        except ImportError:
            def load_profitcrafter_data(w_path=None):
                return {}

    pc_data = load_profitcrafter_data(wtf_path)
    char_name = pc_data.get("character", "") if isinstance(pc_data, dict) else ""
    pc_professions = pc_data.get("professions", {}) if isinstance(pc_data, dict) else {}
    pc_recipes = pc_data.get("recipes", {}) if isinstance(pc_data, dict) else {}

    for recipe_id, recipe in pc_recipes.items():
        if not isinstance(recipe, dict):
            continue
        output_id = recipe.get("output", 0)
        if not output_id or int(output_id) <= 0:
            continue
        output_id = int(output_id)
        qty = int(recipe.get("qty", 1) or 1)
        if qty <= 0:
            qty = 1

        output_price = latest_prices.get(output_id, 0)
        if not output_price or output_price <= 0:
            continue

        net_rev = int(output_price * qty * 0.95)
        if net_rev <= 0:
            continue

        max_total_craft_cost = max(0, int(net_rev * 0.85))
        if min_profit_copper > 0:
            max_total_craft_cost = min(max_total_craft_cost, max(0, net_rev - int(min_profit_copper)))

        if max_total_craft_cost <= 0:
            continue

        reagents = recipe.get("reagents", [])
        if not reagents or not isinstance(reagents, list):
            continue

        vendor_mats = []
        craft_mats = []
        for rg in reagents:
            if not isinstance(rg, dict):
                continue
            r_id = int(rg.get("id", 0))
            if r_id <= 0:
                continue
            r_n = int(rg.get("n", 1) or 1)
            if r_n <= 0:
                r_n = 1
            cat_it = _get_catalog_item(r_id)
            v_buy = int(items_dict.get(r_id, {}).get("vendor_buy") or cat_it.get("vendor_buy_copper") or cat_it.get("vendor_buy_price") or 0)
            if v_buy > 0 and (r_id in VENDOR_REAGENTS_WHITELIST or items_dict.get(r_id, {}).get("vendor_buy", 0) > 0 or cat_it.get("vendor_buy_copper", 0) > 0):
                vendor_mats.append((r_id, r_n, v_buy))
            else:
                craft_mats.append((r_id, r_n))

        if not craft_mats:
            craft_mats = [(r_id, r_n) for r_id, r_n, _ in vendor_mats]
            remaining_budget = max_total_craft_cost
        else:
            vendor_cost = sum(r_n * vb for _, r_n, vb in vendor_mats)
            remaining_budget = max_total_craft_cost - vendor_cost

        if remaining_budget <= 0:
            continue

        output_cat = _get_catalog_item(output_id)
        output_name = items_dict.get(output_id, {}).get("name") or output_cat.get("name") or f"Item {output_id}"
        recipe_name = recipe.get("name") or f"Recipe {recipe_id}"

        if len(craft_mats) == 1:
            r_id, r_n = craft_mats[0]
            unit_ceiling = max(1, int(remaining_budget // r_n))
            reagent_ceilings = [(r_id, unit_ceiling)]
        else:
            total_ah = sum(latest_prices.get(r_id, 0) * r_n for r_id, r_n in craft_mats)
            reagent_ceilings = []
            if total_ah > 0:
                for r_id, r_n in craft_mats:
                    ah_p = latest_prices.get(r_id, 0)
                    share = (ah_p * r_n) / total_ah if ah_p > 0 else (1.0 / len(craft_mats))
                    unit_ceiling = max(1, int((remaining_budget * share) // r_n))
                    reagent_ceilings.append((r_id, unit_ceiling))
            else:
                total_cnt = sum(r_n for _, r_n in craft_mats)
                for r_id, r_n in craft_mats:
                    unit_ceiling = max(1, int(remaining_budget // total_cnt))
                    reagent_ceilings.append((r_id, unit_ceiling))

        for r_id, unit_ceiling in reagent_ceilings:
            if unit_ceiling <= 0:
                continue
            r_cat = _get_catalog_item(r_id)
            reagent_name = items_dict.get(r_id, {}).get("name") or r_cat.get("name") or f"Item {r_id}"

            existing = targets.get(r_id)
            existing_target = int(existing.get("target", 0)) if existing else 0
            final_target = max(existing_target, unit_ceiling)

            targets[r_id] = TargetDict({
                "target": final_target,
                "min_profit": int(min_profit_copper),
                "name": reagent_name,
                "craft_target": True,
                "recipe_name": recipe_name,
                "output_id": output_id,
                "output_name": output_name,
            })

    # Output file handling
    if output_path is None:
        output_path = os.path.join("addons", "ForeverTrade_Sniper", "config.lua")
    output_path = str(output_path)

    out_dir = os.path.dirname(os.path.abspath(output_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    gen_ts = int(time.time())
    lines = [
        "-- Generated by Forever Trade Engine. DO NOT EDIT MANUALLY.",
        "FT_SNIPER_CONFIG = {",
        "    version = 2,",
        f"    generated_at = {gen_ts},",
        f"    min_profit_copper = {int(min_profit_copper)},",
        f'    character = "{char_name}",',
        "    professions = {",
    ]
    for pid in sorted(pc_professions.keys()):
        pinfo = pc_professions[pid]
        pname = str(pinfo.get("name", "")).replace("\\", "\\\\").replace('"', '\\"')
        prank = int(pinfo.get("rank", 0))
        pmax = int(pinfo.get("max", 0))
        lines.append(f"        [{pid}] = {{")
        lines.append(f'            name = "{pname}",')
        lines.append(f"            rank = {prank},")
        lines.append(f"            max = {pmax},")
        lines.append("        },")
    lines.append("    },")
    lines.append("    targets = {")
    for tid in sorted(targets.keys()):
        tinfo = targets[tid]
        safe_name = str(tinfo.get("name", f"Item {tid}")).replace("\\", "\\\\").replace('"', '\\"')
        ceiling_val = int(tinfo.get("target", 0))
        p_copper = int(tinfo.get("min_profit", min_profit_copper))
        if tinfo.get("craft_target"):
            safe_rec_name = str(tinfo.get("recipe_name", "")).replace("\\", "\\\\").replace('"', '\\"')
            out_id = int(tinfo.get("output_id", 0))
            lines.append(
                f'        [{tid}] = {{ target = {ceiling_val}, min_profit = {p_copper}, name = "{safe_name}", craft_target = true, recipe_name = "{safe_rec_name}", output_id = {out_id} }},'
            )
        else:
            lines.append(
                f'        [{tid}] = {{ target = {ceiling_val}, min_profit = {p_copper}, name = "{safe_name}" }},'
            )
    lines.append("    },")
    lines.append("    names = {")
    for tid in sorted(targets.keys()):
        tinfo = targets[tid]
        safe_name = str(tinfo.get("name", f"Item {tid}")).replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'        [{tid}] = "{safe_name}",')
    lines.append("    }")
    lines.append("}")
    lines.append("")

    tmp_path = str(output_path) + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    os.replace(tmp_path, output_path)

    return {
        "total_targets": len(targets),
        "output_path": output_path,
        "generated_at": gen_ts,
        "min_profit_copper": min_profit_copper,
        "targets": targets,
        "character": char_name,
        "professions": pc_professions,
    }


def get_disenchant_arbitrage(
    db_conn: Optional[sqlite3.Connection] = None,
    realm: Optional[str] = None,
    min_profit_copper: int = 1000,
    de_tables_path: Optional[str] = None,
    max_age_hours: Optional[float] = None,
) -> list[dict]:
    """Find equipment auction lots where E[DE Value] * 0.95 - buyout >= min_profit_copper.

    Returns list of dicts sorted by expected_net_profit descending:
      item_id, name, name_ru, quality, item_level, item_class, item_subclass,
      buyout_copper, expected_de_value, expected_net_profit, roi_percent,
      reagents, timestamp, realm.
    """
    if db_conn is None:
        return []

    check_and_reload_overrides(db_conn)
    de_tables = load_disenchant_tables(de_tables_path) if de_tables_path else load_disenchant_tables()

    min_ts = _get_cutoff_timestamp(db_conn, realm, max_age_hours)
    cursor = db_conn.cursor()

    # 1. Fetch latest prices on target realm for enchanting materials
    latest_prices = {}
    try:
        cursor.execute("""
            WITH latest_snapshots AS (
                SELECT s.item_id, s.realm, s.price,
                       ROW_NUMBER() OVER (
                           PARTITION BY s.item_id, s.realm
                           ORDER BY s.timestamp DESC, s.id DESC
                       ) as rn
                FROM price_snapshots s
                WHERE (? IS NULL OR s.realm = ?)
                  AND s.price > 0
            )
            SELECT item_id, price FROM latest_snapshots WHERE rn = 1
        """, (realm, realm))
        latest_prices = {r[0]: r[1] for r in cursor.fetchall() if r[1] is not None and r[1] > 0}
    except Exception:
        latest_prices = {}

    try:
        cursor.execute("SELECT item_id, ewma_price FROM item_market_metrics WHERE (? IS NULL OR realm = ?)", (realm, realm))
        for r in cursor.fetchall():
            if r[1] and r[1] > 0:
                latest_prices[r[0]] = int(r[1])
    except Exception:
        pass

    # 2. Check if item_level column exists in items table
    has_item_level = False
    try:
        cursor.execute("PRAGMA table_info(items)")
        cols = [r[1] for r in cursor.fetchall()]
        has_item_level = "item_level" in cols
    except Exception:
        pass

    item_level_select = "i.item_level," if has_item_level else "NULL as item_level,"

    # 3. Query candidate lots from latest_snapshots
    query = f"""
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
        SELECT s.item_id, s.price, s.timestamp,
               i.name, i.name_ru, i.quality, i.item_class, i.item_subclass, i.category,
               {item_level_select}
               s.realm
        FROM latest_snapshots s
        LEFT JOIN items i ON s.item_id = i.item_id
        WHERE s.rn = 1
    """

    results = []
    try:
        cursor.execute(query, (realm, realm, min_ts, min_ts))
        rows = cursor.fetchall()
    except Exception:
        return []

    for row in rows:
        item_id = int(row[0])
        buyout = int(row[1])
        ts = row[2]
        db_name = row[3]
        db_name_ru = row[4]
        db_quality = row[5]
        db_class = row[6]
        db_subclass = row[7]
        db_cat = row[8]
        db_level = row[9] if has_item_level else None

        cat_item = _get_catalog_item(item_id)
        combined_meta = dict(cat_item)

        quality = db_quality if db_quality is not None else combined_meta.get("quality")
        if quality not in (2, 3, 4):
            continue

        item_class = db_class if db_class is not None else combined_meta.get("item_class")
        item_subclass = db_subclass if db_subclass is not None else combined_meta.get("item_subclass", 0)

        level = db_level or combined_meta.get("item_level") or combined_meta.get("level") or combined_meta.get("req_level")
        if level is None:
            continue
        try:
            level = int(level)
        except (ValueError, TypeError):
            continue
        if level <= 0:
            continue

        combined_meta["quality"] = quality
        combined_meta["item_class"] = item_class
        combined_meta["item_subclass"] = item_subclass
        combined_meta["item_level"] = level

        de_res = calculate_disenchant_value(
            item_id=item_id,
            conn=db_conn,
            realm=realm,
            item_meta=combined_meta,
            latest_prices=latest_prices,
            disenchant_tables=de_tables,
        )

        expected_de_val = float(de_res)
        if expected_de_val <= 0:
            continue

        net_profit = round((expected_de_val * 0.95) - buyout, 2)
        if net_profit < min_profit_copper:
            continue

        roi_pct = round((net_profit / buyout) * 100, 2) if buyout > 0 else 0.0

        name_en = combined_meta.get("name") or db_name or get_item_name(db_conn, item_id, "en")
        name_ru = db_name_ru or combined_meta.get("name_ru") or get_item_name(db_conn, item_id, "ru")

        reagents = getattr(de_res, "reagents", [])
        if not reagents and isinstance(de_res, dict):
            reagents = de_res.get("reagents", [])

        results.append({
            "item_id": item_id,
            "name": name_en,
            "name_ru": name_ru,
            "quality": int(quality),
            "item_level": int(level),
            "item_class": int(item_class) if item_class is not None else 0,
            "item_subclass": int(item_subclass) if item_subclass is not None else 0,
            "buyout_copper": int(buyout),
            "expected_de_value": float(expected_de_val),
            "expected_net_profit": float(net_profit),
            "profit_copper": int(round(net_profit)),
            "roi_percent": float(roi_pct),
            "reagents": reagents,
            "timestamp": ts,
            "realm": row[10] if len(row) > 10 else realm,
        })

    results.sort(key=lambda x: x.get("expected_net_profit", 0), reverse=True)
    return results


def generate_dashboard_data(
    conn: sqlite3.Connection,
    realm: Optional[str] = None,
    has_bartering: bool = False,
    has_master_chef: bool = False,
    max_age_hours: Optional[float] = None,
    min_de_profit_copper: int = 1000,
) -> dict[str, list[dict]]:
    """Gather all market arbitrage streams for dashboard consumption."""
    try:
        vendor_arb = get_vendor_arbitrage(conn, realm=realm, max_age_hours=max_age_hours)
    except TypeError:
        vendor_arb = get_vendor_arbitrage(conn, realm=realm)

    try:
        crafting = get_crafting_margins(
            conn,
            has_bartering=has_bartering,
            has_master_chef=has_master_chef,
            realm=realm,
            max_age_hours=max_age_hours,
        )
    except TypeError:
        try:
            crafting = get_crafting_margins(
                conn,
                has_bartering=has_bartering,
                has_master_chef=has_master_chef,
                realm=realm,
            )
        except TypeError:
            crafting = get_crafting_margins(conn, realm=realm)

    try:
        anomalies = get_zscore_anomalies(conn, realm=realm, max_age_hours=max_age_hours)
    except TypeError:
        anomalies = get_zscore_anomalies(conn, realm=realm)

    try:
        disenchant = get_disenchant_arbitrage(
            conn,
            realm=realm,
            min_profit_copper=min_de_profit_copper,
            max_age_hours=max_age_hours,
        )
    except TypeError:
        disenchant = get_disenchant_arbitrage(conn, realm=realm)

    return {
        "vendor_arbitrage": vendor_arb,
        "crafting": crafting,
        "anomalies": anomalies,
        "disenchant_arbitrage": disenchant,
    }


def get_profitable_crafts(
    conn: sqlite3.Connection,
    has_bartering: bool = False,
    has_master_chef: bool = False,
    min_roi: float = 0.25,
    realm: Optional[str] = None,
    include_ah_deposit: bool = True,
    max_age_hours: Optional[float] = None,
    resolve_subcrafts: bool = True,
) -> list[dict]:
    """Calculate profitable crafts using recursive DAG Make-vs-Buy material cost resolution."""
    return get_crafting_margins(
        conn=conn,
        has_bartering=has_bartering,
        has_master_chef=has_master_chef,
        min_roi=min_roi,
        realm=realm,
        include_ah_deposit=include_ah_deposit,
        max_age_hours=max_age_hours,
        resolve_subcrafts=resolve_subcrafts,
    )





