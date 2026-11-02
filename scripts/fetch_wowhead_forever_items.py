#!/usr/bin/env python3
"""Pre-warm data/items_forever.json by fetching item data from Wowhead Forever XML API."""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from typing import List, Optional, Set

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


def is_clean_market_item(slug: str) -> bool:
    """Filter Wowhead slug to include clean market items and exclude gear/junk."""
    s = slug.lower()

    # 1. Exclude deprecated, test, old, monster, qa, unused, debug, dummy, gm
    if any(s.startswith(p) for p in ("deprecated", "test", "old", "monster", "qa", "unused", "gm-", "debug", "dummy")):
        return False
    if any(p in s for p in ("-deprecated", "deprecated-", "-test", "test-", "-monster", "monster-", "-unused", "unused-", "-dummy", "dummy-")):
        return False
    if s.endswith(("-old", "-deprecated", "-test", "-unused")):
        return False

    # 2. Recipes / Patterns / Schematics / Plans / Formulas / Manuals / Books / Tomes
    recipe_prefixes = ("recipe-", "pattern-", "schematic-", "plans-", "formula-", "manual-", "book-", "tome-")
    if any(s.startswith(p) or f"-{p}" in s for p in recipe_prefixes):
        return True

    # 3. Waylaid crates / supplies / shipments
    if "waylaid-" in s:
        return True

    # 4. Bags, pouches, packs, satchels, quivers, boxes, crates
    container_words = {"bag", "pouch", "sack", "satchel", "pack", "quiver", "ammo-pouch", "bandolier", "crate", "box", "case", "coffer"}
    parts = set(s.split("-"))
    if any(w in parts for w in container_words):
        gear_exclusions = {"chest", "armor", "vest", "robe", "tunic", "hauberk", "breastplate"}
        if not any(g in parts for g in gear_exclusions):
            return True

    # 5. Item enhancements / trade consumable items that might have gear words
    enhancements = ("armor-kit", "weightstone", "sharpening-stone", "shield-spike", "wizard-oil", "mana-oil")
    if any(k in s for k in enhancements):
        return True

    # 6. Exclude Armor / Weapon Gear
    gear_tokens = {
        "sword", "blade", "axe", "mace", "hammer", "dagger", "knife", "stiletto", "dirk", "staff", "polearm", "spear", "halberd", "scythe", "glaive",
        "bow", "crossbow", "gun", "rifle", "shotgun", "blunderbuss", "musket", "pistol", "wand", "rod", "scepter",
        "shield", "buckler",
        "helm", "helmet", "hat", "cap", "headband", "crown", "circlet", "cowl", "mask", "bandana", "hood", "headdress", "coif", "monocle", "eyepatch", "goggles", "glasses",
        "spaulders", "shoulderpads", "shoulderpad", "mantle", "epaulets", "pauldrons", "amice", "shoulders", "shoulder",
        "chestpiece", "breastplate", "tunic", "vest", "robe", "robes", "raiment", "jerkin", "armor", "hauberk", "chainmail", "scalemail", "cuirass", "vestment", "vestments", "doublet", "harness",
        "bracers", "bindings", "armbands", "cuffs", "wristguards", "bracer", "wristbands",
        "gloves", "gauntlets", "mitts", "handwraps", "grasps", "touch",
        "belt", "girdle", "sash", "cord", "cinch", "clasp", "waistband",
        "pants", "leggings", "britches", "breeches", "trousers", "kilt", "legplates", "chausses", "skirt", "pantaloons", "slacks",
        "boots", "shoes", "sandals", "footpads", "footwraps", "greaves", "sabatons", "slippers", "moccasins", "striders", "treads",
        "ring", "band", "signet", "circle",
        "necklace", "amulet", "choker", "pendant", "medallion", "collar", "talisman",
        "cloak", "cape", "drape",
        "trinket", "badge",
        "shirt", "tabard",
    }
    if any(p in gear_tokens for p in parts):
        return False

    # 7. Market Categories: Trade Goods & Consumables
    market_tokens = {
        # Consumables
        "potion", "potions", "elixir", "elixirs", "flask", "flasks", "draught", "brew", "bandage", "bandages", "scroll", "scrolls",
        "water", "juice", "milk", "tea", "ale", "wine", "beer", "cider", "whiskey", "ration", "rations",
        "meat", "fish", "ribs", "steak", "pie", "stew", "roast", "soup", "bread", "sausage", "egg", "eggs", "cheese",
        "clam", "crab", "apple", "mushroom", "mushrooms", "berry", "berries", "fruit",
        "oil", "poison", "explosive", "explosives", "dynamite", "bomb", "bombs", "grenade", "grenades", "charge",
        "arrow", "arrows", "shot", "bullet", "bullets", "shell", "shells",
        # Trade goods: Metal & Stone
        "ore", "bar", "bars", "ingot", "ingots", "nugget", "nuggets",
        "stone", "stones", "flux", "coal",
        # Cloth & Tailoring
        "cloth", "silk", "mageweave", "runecloth", "felcloth", "mooncloth", "wool", "linen", "felt", "thread", "dye", "yarn",
        # Leather & Skins
        "leather", "hide", "hides", "pelt", "pelts", "scale", "scales", "fur", "skin", "skins",
        # Gems & Jewels
        "gem", "gems", "malachite", "tigerseye", "shadowgem", "agate", "ruby", "sapphire", "diamond", "emerald", "opal", "crystal", "crystals", "citrine", "moonstone", "pearl", "pearls",
        # Enchanting
        "dust", "shard", "shards", "essence", "essences",
        # Elemental
        "elemental", "ichor",
        # Herbs & Alchemy
        "herb", "herbs", "leaf", "leaves", "root", "roots", "weed", "blossom", "lotus", "thistle", "clover", "grass", "kelp", "spores", "moss",
        "peacebloom", "silverleaf", "earthroot", "mageroyal", "briarthorn", "stranglekelp", "bruiseweed", "wild-steelbloom",
        "grave-moss", "kingsblood", "liferoot", "fadeleaf", "goldthorn", "khadgars-whisker", "wintersbite", "firebloom",
        "purple-lotus", "arthas-tears", "sungrass", "blindweed", "ghost-mushroom", "gromsblood", "golden-sansam",
        "dreamfoil", "mountain-silversage", "plaguebloom", "icecap", "black-lotus",
        # Monster parts & Reagents
        "fang", "fangs", "claw", "claws", "tooth", "teeth", "tusk", "tusks", "paw", "paws", "eye", "eyes", "feather", "feathers",
        "bone", "bones", "horn", "horns", "tail", "tails", "snout", "snouts", "liver", "livers", "heart", "hearts", "venom", "blood",
        "gland", "glands", "sac", "sacs",
        # Engineering parts & Reagents
        "fuse", "tube", "tubes", "casing", "casings", "trigger", "wire", "cog", "cogs", "gizmo", "actuator", "reagent", "reagents",
    }
    if any(p in market_tokens for p in parts):
        return True

    return False


def fetch_sitemap_market_ids(
    domain: str = "forever",
    limit: Optional[int] = 2000,
    max_retries: int = 3,
    backoff: float = 1.0,
) -> List[int]:
    """Fetch sitemap XML from Wowhead Forever and extract clean market item IDs."""
    url = (
        f"https://www.wowhead.com/{domain}/sitemap/item?page=1"
        if domain
        else "https://www.wowhead.com/sitemap/item?page=1"
    )
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ForeverTrade/1.0"},
    )
    content = ""
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=15.0) as resp:
                if resp.status == 429:
                    retry_after = resp.headers.get("Retry-After")
                    wait_time = float(retry_after) if retry_after else (backoff * (2 ** attempt))
                    print(f"\n[!] Sitemap fetch hit HTTP 429. Backing off for {wait_time:.1f}s...")
                    time.sleep(wait_time)
                    continue
                content = resp.read().decode("utf-8", errors="replace")
                break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries - 1:
                retry_after = e.headers.get("Retry-After")
                wait_time = float(retry_after) if retry_after else (backoff * (2 ** attempt))
                print(f"\n[!] Sitemap fetch hit HTTP 429. Backing off for {wait_time:.1f}s...")
                time.sleep(wait_time)
                continue
            print(f"\n[!] HTTP error fetching sitemap ({url}): {e}")
            break
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = backoff * (2 ** attempt)
                print(f"\n[!] Network error fetching sitemap ({url}): {e}. Retrying in {wait_time:.1f}s...")
                time.sleep(wait_time)
                continue
            print(f"\n[!] Failed fetching sitemap ({url}): {e}")
            break

    if not content:
        return []

    entries = re.findall(r"item=(\d+)/([^<]+)", content)
    matched_ids: List[int] = []
    seen: Set[int] = set()

    for item_id_str, slug in entries:
        i_id = int(item_id_str)
        if i_id not in seen and is_clean_market_item(slug):
            seen.add(i_id)
            matched_ids.append(i_id)

    if limit is not None and limit > 0:
        matched_ids = matched_ids[:limit]

    return matched_ids


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


def save_catalog(catalog: dict, output_path: str) -> None:
    """Flush normalized catalog to disk atomically."""
    out_items = sorted(
        [normalize_forever_item(it) for it in catalog.values()],
        key=lambda x: int(x["item_id"]),
    )
    out_dir = os.path.dirname(os.path.abspath(output_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    temp_path = f"{output_path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(out_items, f, ensure_ascii=False, indent=2)
    os.replace(temp_path, output_path)


def main():
    parser = argparse.ArgumentParser(description="Fetch and pre-warm Wowhead Forever items catalog.")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of items to fetch")
    parser.add_argument("--output", type=str, default="data/items_forever.json", help="Output JSON path")
    parser.add_argument("--delay", type=float, default=0.1, help="Polite delay between requests in seconds (default: 0.1)")
    parser.add_argument("--domain", type=str, default="forever", help="Wowhead domain (default: forever)")
    parser.add_argument("--sitemap-market", action="store_true", help="Fetch expanded market items from Wowhead Forever sitemap")
    parser.add_argument("--sitemap-limit", type=int, default=2000, help="Maximum number of items to extract from sitemap (default: 2000)")
    args = parser.parse_args()

    seed_ids = collect_seed_ids()
    print(f"[*] Collected {len(seed_ids)} unique seed item IDs from era catalog and recipes.")

    if args.sitemap_market:
        print(f"[*] Fetching market items from Wowhead sitemap (limit={args.sitemap_limit})...")
        sitemap_ids = fetch_sitemap_market_ids(domain=args.domain, limit=args.sitemap_limit)
        print(f"[*] Found {len(sitemap_ids)} clean market items from sitemap.")
        target_ids = sorted(set(seed_ids) | set(sitemap_ids))
        print(f"[*] Total target items after combining seeds and sitemap: {len(target_ids)}.")
    else:
        target_ids = seed_ids

    if args.limit is not None and args.limit > 0:
        target_ids = target_ids[: args.limit]
        print(f"[*] Limiting target to first {len(target_ids)} items.")

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

    # Skip items already cached
    to_fetch = [i_id for i_id in target_ids if i_id not in existing_catalog]
    skipped = len(target_ids) - len(to_fetch)
    print(f"[*] Target items: {len(target_ids)} ({skipped} already cached, {len(to_fetch)} to fetch).")

    if not to_fetch:
        print("[*] All target items are already cached in output JSON. Nothing to fetch.")
        save_catalog(existing_catalog, args.output)
        return

    total = len(to_fetch)
    success = 0
    failed = 0
    processed_since_flush = 0
    start_time = time.time()

    for idx, i_id in enumerate(to_fetch, 1):
        elapsed = time.time() - start_time
        speed = (idx - 1) / elapsed if elapsed > 0.05 and (idx - 1) > 0 else 0.0
        remaining = total - (idx - 1)
        eta_sec = remaining / speed if speed > 0 else 0
        eta_m = int(eta_sec // 60)
        eta_s = int(eta_sec % 60)
        eta_str = f"{eta_m}m {eta_s:02d}s" if eta_sec >= 60 else f"{int(eta_sec)}s"
        speed_str = f"{speed:.1f} it/s" if speed > 0 else "-- it/s"

        print(f"[{idx}/{total}] ({speed_str}, ETA: {eta_str}) Fetching item {i_id}...", end=" ", flush=True)

        info = fetch_wowhead_item(i_id, domain=args.domain)
        if info:
            existing_catalog[i_id] = normalize_forever_item(info)
            success += 1
            print(f"OK: {existing_catalog[i_id].get('name')} ({existing_catalog[i_id].get('name_ru')})")
        else:
            failed += 1
            print("FAILED / Not found")

        processed_since_flush += 1
        if processed_since_flush >= 25:
            save_catalog(existing_catalog, args.output)
            processed_since_flush = 0
            print(f"    [+] Live flushed catalog to '{args.output}' (Total items: {len(existing_catalog)})")

        if args.delay > 0:
            time.sleep(args.delay)

    # Final save
    save_catalog(existing_catalog, args.output)
    print(
        f"[+] Done! Saved {len(existing_catalog)} items to '{args.output}'. "
        f"(Processed {total} items: {success} succeeded, {failed} not found, {skipped} cached)"
    )


if __name__ == "__main__":
    main()
