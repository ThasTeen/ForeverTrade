#!/usr/bin/env python3
"""Build Classic Era 1.15 crafting recipes and enrich items_forever.json.

Combines MTSL (MissingTradeSkillsList) and TSI (TradeSkillInfo) to produce
980 crafting recipes across Blacksmithing, Leatherworking, Tailoring,
Engineering, Alchemy, Cooking, and Smelting. Fetches metadata for new items
from Wowhead Nether API and updates data/items_forever.json while preserving
settled prices and existing entries.
"""

import concurrent.futures
import json
import os
import re
import sys
import time
import urllib.request
from typing import Dict, List, Optional, Set, Tuple

# Ensure project root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.database import ITEM_LOCALIZATION, R45_SETTLED_PRICES
from src.wowhead import _fetch_via_nether

MTSL_URL = "https://raw.githubusercontent.com/Thumbkin/MissingTradeSkillsList_Classic/master/data/skills.lua"
TSI_URL = "https://raw.githubusercontent.com/Ravendwyr/TradeSkillInfo/master/Data.lua"
CACHE_DIR = os.path.join("data", "cache")
RECIPES_OUTPUT_PATH = os.path.join("data", "recipes_era_1_15.json")
ITEMS_FOREVER_PATH = os.path.join("data", "items_forever.json")
ITEMS_ERA_PATH = os.path.join("data", "items_era_1_15.json")


def download_cached(url: str, filename: str) -> str:
    """Download text file or return cached version if already present."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, filename)
    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 1000:
        with open(cache_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    print(f"[*] Downloading {url} -> {cache_path}...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (ForeverTrade/1.0)"})
    with urllib.request.urlopen(req, timeout=30.0) as resp:
        text = resp.read().decode("utf-8", errors="replace")

    with open(cache_path, "w", encoding="utf-8") as f:
        f.write(text)
    return text


def parse_tsi_combines(text_tsi: str) -> Dict[int, str]:
    """Parse TradeskillInfo.vars.combines into spell_id -> combine_str mapping."""
    combines: Dict[int, str] = {}
    for m in re.finditer(r'\[(\d+)\]\s*=\s*"([^"]+)"', text_tsi):
        spell_id = int(m.group(1))
        combines[spell_id] = m.group(2)
    return combines


def extract_recipes(text_mtsl: str, tsi_combines: Dict[int, str]) -> Tuple[List[dict], Set[int]]:
    """Extract and validate 980 crafting recipes from MTSL and TSI combines."""
    professions = [
        "Blacksmithing",
        "Leatherworking",
        "Tailoring",
        "Engineering",
        "Alchemy",
        "Cooking",
        "Mining",
    ]
    all_prof_names = [
        "Alchemy",
        "Blacksmithing",
        "Cooking",
        "Enchanting",
        "Engineering",
        "First Aid",
        "Leatherworking",
        "Mining",
        "Poisons",
        "Tailoring",
    ]

    recipes_list: List[dict] = []
    all_item_ids: Set[int] = set()

    for p in professions:
        m = re.search(r'\["' + p + r'"\]\s*=\s*\{', text_mtsl)
        if not m:
            continue
        start_idx = m.end()
        next_pos = len(text_mtsl)
        for other_p in all_prof_names:
            if other_p != p:
                m_other = re.search(r'\["' + other_p + r'"\]\s*=\s*\{', text_mtsl[start_idx:])
                if m_other:
                    pos = start_idx + m_other.start()
                    if pos < next_pos:
                        next_pos = pos

        block = text_mtsl[start_idx:next_pos]
        skills = re.findall(
            r'\["id"\]\s*=\s*(\d+).*?\["min_skill"\]\s*=\s*(\d+).*?\["English"\]\s*=\s*"([^"]+)".*?\["Russian"\]\s*=\s*"([^"]+)"',
            block,
            re.DOTALL,
        )

        for s_id_str, min_skill_str, name_en, name_ru in skills:
            s_id = int(s_id_str)
            if s_id not in tsi_combines:
                continue

            combine_data = tsi_combines[s_id]
            parts = combine_data.split("|")
            if len(parts) < 3 or not parts[0].isdigit():
                continue

            crafted_item_id = int(parts[0])
            reagents_str = parts[2].strip()
            raw_yield = float(parts[4]) if len(parts) > 4 and parts[4] else 1.0
            yield_val = raw_yield if raw_yield > 0 else 1.0

            reagents = []
            if reagents_str:
                for r in reagents_str.split(" "):
                    if ":" in r:
                        r_id, r_count = r.split(":")
                        if r_id.isdigit() and r_count.isdigit():
                            r_id_int = int(r_id)
                            r_count_int = int(r_count)
                            if r_count_int > 0:
                                reagents.append({"item_id": r_id_int, "count": r_count_int})
                                all_item_ids.add(r_id_int)

            if not reagents:
                continue

            all_item_ids.add(crafted_item_id)
            prof_name = "Smelting" if p == "Mining" else p

            recipes_list.append(
                {
                    "recipe_id": s_id,
                    "name": name_en,
                    "name_ru": name_ru,
                    "profession": prof_name,
                    "min_skill": int(min_skill_str),
                    "crafted_item_id": crafted_item_id,
                    "yield": yield_val,
                    "reagents": reagents,
                }
            )

    return recipes_list, all_item_ids


def fetch_ru_name(item_id: int) -> Optional[str]:
    """Fetch Russian localized name from Wowhead Nether Russian tooltip."""
    url = f"https://nether.wowhead.com/ru/tooltip/item/{item_id}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ForeverTrade/1.0"},
    )
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                if resp.status == 200:
                    d = json.loads(resp.read().decode("utf-8"))
                    name = d.get("name")
                    if name and not name.lower().startswith("item #"):
                        return name
            break
        except Exception:
            time.sleep(0.5)
    return None


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
    print("[*] Step 1: Downloading / reading cached MTSL and TSI data...")
    text_mtsl = download_cached(MTSL_URL, "skills.lua")
    text_tsi = download_cached(TSI_URL, "Data.lua")

    print("[*] Step 2: Extracting combine recipes...")
    tsi_combines = parse_tsi_combines(text_tsi)
    recipes_list, all_item_ids = extract_recipes(text_mtsl, tsi_combines)

    print(f"[*] Total compiled valid craft recipes: {len(recipes_list)}")
    print(f"[*] Total unique item IDs referenced: {len(all_item_ids)}")

    by_prof: Dict[str, int] = {}
    for r in recipes_list:
        by_prof[r["profession"]] = by_prof.get(r["profession"], 0) + 1
    for prof, cnt in sorted(by_prof.items()):
        print(f"    {prof}: {cnt} recipes")

    # Save recipes to data/recipes_era_1_15.json
    os.makedirs(os.path.dirname(RECIPES_OUTPUT_PATH), exist_ok=True)
    with open(RECIPES_OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(recipes_list, f, ensure_ascii=False, indent=2)
    print(f"[+] Saved {len(recipes_list)} recipes to {RECIPES_OUTPUT_PATH}")

    # Load existing catalogs
    era_items: Dict[int, dict] = {}
    if os.path.exists(ITEMS_ERA_PATH):
        with open(ITEMS_ERA_PATH, "r", encoding="utf-8") as f:
            for it in json.load(f):
                era_items[int(it["item_id"])] = it

    forever_items: Dict[int, dict] = {}
    if os.path.exists(ITEMS_FOREVER_PATH):
        with open(ITEMS_FOREVER_PATH, "r", encoding="utf-8") as f:
            for it in json.load(f):
                forever_items[int(it["item_id"])] = it
    orig_forever_count = len(forever_items)
    print(f"[*] Existing items_forever.json has {orig_forever_count} items")

    # Find missing item IDs
    missing_ids = sorted([iid for iid in all_item_ids if iid not in era_items and iid not in forever_items])
    print(f"[*] Items missing from catalogs: {len(missing_ids)}")

    # Mapping of crafted items to MTSL names
    mtsl_crafted_names: Dict[int, Tuple[str, str]] = {}
    for r in recipes_list:
        mtsl_crafted_names[r["crafted_item_id"]] = (r["name"], r.get("name_ru", ""))

    if missing_ids:
        print(f"[*] Fetching metadata for {len(missing_ids)} missing items via Wowhead Nether (12 threads)...")
        t0 = time.time()
        fetched_items: Dict[int, dict] = {}

        def _fetch_worker(item_id: int) -> Tuple[int, Optional[dict]]:
            res = _fetch_via_nether(item_id, domain="forever")
            return item_id, res

        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
            future_to_id = {executor.submit(_fetch_worker, iid): iid for iid in missing_ids}
            done_cnt = 0
            for fut in concurrent.futures.as_completed(future_to_id):
                iid, res = fut.result()
                done_cnt += 1
                if done_cnt % 100 == 0 or done_cnt == len(missing_ids):
                    print(f"    Fetched {done_cnt}/{len(missing_ids)} items ({done_cnt/len(missing_ids)*100:.1f}%)...")

                if res and isinstance(res, dict):
                    fetched_items[iid] = res
                else:
                    # Safe fallback from MTSL or generic
                    en_name, ru_name = mtsl_crafted_names.get(iid, (f"Item {iid}", None))
                    fetched_items[iid] = {
                        "item_id": iid,
                        "name": en_name,
                        "name_ru": ru_name,
                        "quality": 1,
                        "item_class": 0,
                        "item_subclass": 0,
                        "vendor_sell_price": 0,
                        "vendor_sell_copper": 0,
                        "vendor_buy_price": 0,
                        "vendor_buy_copper": 0,
                        "icon": "",
                    }

        elapsed = time.time() - t0
        print(f"[+] Nether fetch complete in {elapsed:.1f}s")

        # Assign Russian names for fetched items
        need_ru_fetch: List[int] = []
        for iid, item in fetched_items.items():
            if not item.get("name_ru"):
                if iid in ITEM_LOCALIZATION and ITEM_LOCALIZATION[iid].get("ru"):
                    item["name_ru"] = ITEM_LOCALIZATION[iid]["ru"]
                elif iid in mtsl_crafted_names and mtsl_crafted_names[iid][1]:
                    item["name_ru"] = mtsl_crafted_names[iid][1]
                else:
                    need_ru_fetch.append(iid)

        if need_ru_fetch:
            print(f"[*] Fetching Russian localization for {len(need_ru_fetch)} remaining items...")
            with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
                ru_results = list(executor.map(fetch_ru_name, need_ru_fetch))
                for iid, ru_name in zip(need_ru_fetch, ru_results):
                    if ru_name:
                        fetched_items[iid]["name_ru"] = ru_name

        # Merge into forever_items
        for iid, item in fetched_items.items():
            forever_items[iid] = item

    print(f"[*] Normalizing all {len(forever_items)} items...")
    normalized_items = []
    for it in forever_items.values():
        normalized_items.append(normalize_forever_item(it))

    normalized_items.sort(key=lambda x: int(x["item_id"]))

    with open(ITEMS_FOREVER_PATH, "w", encoding="utf-8") as f:
        json.dump(normalized_items, f, ensure_ascii=False, indent=2)

    print(f"[+] Saved updated {ITEMS_FOREVER_PATH} with {len(normalized_items)} items (was {orig_forever_count})")


if __name__ == "__main__":
    main()
