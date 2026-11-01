#!/usr/bin/env python3
"""Pre-warm data/items_forever.json by fetching item data from Wowhead Forever XML API."""

import argparse
import json
import os
import sys
import time
from typing import List, Set

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.database import ITEM_LOCALIZATION, init_db, load_recipes
from src.wowhead import fetch_wowhead_item


def collect_seed_ids(
    era_path: str = "data/items_era_1_15.json",
    overrides_path: str = "forever_overrides.json",
) -> List[int]:
    """Collect unique item IDs from era catalog, ITEM_LOCALIZATION, recipes, and overrides."""
    item_ids: Set[int] = set()

    # 1. Base Era catalog
    if os.path.exists(era_path):
        try:
            with open(era_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                items = data if isinstance(data, list) else data.get("items", [])
                for it in items:
                    if isinstance(it, dict) and "item_id" in it:
                        item_ids.add(int(it["item_id"]))
        except Exception as e:
            print(f"[!] Warning: failed reading {era_path}: {e}")

    # 2. ITEM_LOCALIZATION dict
    for i_id in ITEM_LOCALIZATION.keys():
        item_ids.add(int(i_id))

    # 3. Recipes (crafted items and reagents)
    try:
        db = init_db(":memory:")
        db.execute("PRAGMA foreign_keys = OFF;")
        load_recipes(db)
        cur = db.cursor()
        cur.execute("SELECT DISTINCT crafted_item_id FROM recipes")
        for r in cur.fetchall():
            item_ids.add(int(r[0]))
        cur.execute("SELECT DISTINCT reagent_item_id FROM recipe_reagents")
        for r in cur.fetchall():
            item_ids.add(int(r[0]))
        db.close()
    except Exception as e:
        print(f"[!] Warning: failed reading recipes: {e}")

    # 4. Forever overrides & Waylaid Crates
    if os.path.exists(overrides_path):
        try:
            with open(overrides_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                items = data if isinstance(data, list) else data.get("overrides", [])
                for it in items:
                    if isinstance(it, dict):
                        if "item_id" in it:
                            item_ids.add(int(it["item_id"]))
                        if it.get("target_item_id"):
                            item_ids.add(int(it["target_item_id"]))
        except Exception as e:
            print(f"[!] Warning: failed reading {overrides_path}: {e}")

    return sorted(item_ids)


def normalize_forever_item(item: dict) -> dict:
    """Ensure items preserve canonical localized names and settled vendor prices."""
    i_id = int(item["item_id"])
    if i_id in ITEM_LOCALIZATION:
        loc = ITEM_LOCALIZATION[i_id]
        if loc.get("en"):
            item["name"] = loc["en"]
        if loc.get("ru"):
            item["name_ru"] = loc["ru"]
    if i_id == 2450:
        item["name"] = "Briarthorn"
        item["name_ru"] = "Остротерн"
        item["vendor_sell_price"] = 50
        item["vendor_sell_copper"] = 50
    elif i_id == 2316:
        item["name"] = "Toughened Leather Gloves"
        item["name_ru"] = "Плотные кожаные перчатки"
        if not item.get("vendor_sell_price"):
            item["vendor_sell_price"] = 137
            item["vendor_sell_copper"] = 137
    elif i_id == 2452:
        item["name"] = "Swiftthistle"
        item["name_ru"] = "Свистоцвет"
        item["vendor_sell_price"] = 40
        item["vendor_sell_copper"] = 40
    elif i_id == 2313:
        item["name"] = "Medium Armor Kit"
        item["name_ru"] = "Накладки из средней кожи"
        item["vendor_sell_price"] = 60
        item["vendor_sell_copper"] = 60
        item["vendor_buy_price"] = 250
        item["vendor_buy_copper"] = 250
    return item


def main():
    parser = argparse.ArgumentParser(description="Fetch and pre-warm Wowhead Forever items catalog.")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of items to fetch")
    parser.add_argument("--output", type=str, default="data/items_forever.json", help="Output JSON path")
    parser.add_argument("--delay", type=float, default=0.05, help="Polite delay between requests in seconds")
    parser.add_argument("--domain", type=str, default="forever", help="Wowhead domain (default: forever)")
    args = parser.parse_args()

    seed_ids = collect_seed_ids()
    print(f"[*] Collected {len(seed_ids)} unique seed item IDs.")

    if args.limit is not None and args.limit > 0:
        seed_ids = seed_ids[: args.limit]
        print(f"[*] Limiting fetch to first {len(seed_ids)} items.")

    existing_catalog = {}
    if os.path.exists(args.output):
        try:
            with open(args.output, "r", encoding="utf-8") as f:
                cached = json.load(f)
                if isinstance(cached, list):
                    for it in cached:
                        if isinstance(it, dict) and "item_id" in it:
                            existing_catalog[int(it["item_id"])] = normalize_forever_item(it)
            print(f"[*] Loaded {len(existing_catalog)} existing items from '{args.output}'.")
        except Exception as e:
            print(f"[!] Could not load existing '{args.output}': {e}")

    total = len(seed_ids)
    success = 0
    failed = 0

    for idx, i_id in enumerate(seed_ids, 1):
        print(f"[{idx}/{total}] Fetching item {i_id}...", end=" ", flush=True)
        info = fetch_wowhead_item(i_id, domain=args.domain)
        if info:
            existing_catalog[i_id] = normalize_forever_item(info)
            success += 1
            print(f"OK: {existing_catalog[i_id].get('name')} ({existing_catalog[i_id].get('name_ru')})")
        else:
            if i_id in existing_catalog:
                print(f"FAILED (retained cached)")
            else:
                failed += 1
                print(f"FAILED / Not found")

        if args.delay > 0:
            time.sleep(args.delay)

    # Save output as JSON array
    out_items = sorted(
        [normalize_forever_item(it) for it in existing_catalog.values()],
        key=lambda x: int(x["item_id"])
    )
    out_dir = os.path.dirname(os.path.abspath(args.output))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(out_items, f, ensure_ascii=False, indent=2)

    print(
        f"[+] Done! Saved {len(out_items)} items to '{args.output}'. "
        f"(Processed {total} items: {success} succeeded, {failed} not found)"
    )


if __name__ == "__main__":
    main()
