"""SQLite database schema, catalog ingestion, recipe graph, and snapshot pruning."""

import atexit
import datetime
import json
import os
import sqlite3
import time
from typing import Optional

# Known item names localization mapping: item_id -> {"en": ..., "ru": ...}
ITEM_LOCALIZATION: dict[int, dict[str, str]] = {
    # Specifically required items
    3857: {"en": "Coal", "ru": "Уголь"},
    2589: {"en": "Linen Cloth", "ru": "Льняная ткань"},
    2996: {"en": "Bolt of Linen Cloth", "ru": "Рулон льняной ткани"},
    2840: {"en": "Copper Bar", "ru": "Медный слиток"},
    2770: {"en": "Copper Ore", "ru": "Медная руда"},
    2320: {"en": "Coarse Thread", "ru": "Грубая нить"},
    765: {"en": "Silverleaf", "ru": "Сребролист"},
    2447: {"en": "Peacebloom", "ru": "Мироцвет"},
    2318: {"en": "Light Leather", "ru": "Тонкая кожа"},
    4238: {"en": "Linen Bag", "ru": "Льняная сумка"},
    4359: {"en": "Handful of Copper Bolts", "ru": "Горсть медных винтов"},
    4361: {"en": "Copper Tube", "ru": "Медная трубка"},
    1482: {"en": "Silver Rod", "ru": "Серебряный жезл"},
    1978: {"en": "Heavy Sharpening Stone", "ru": "Тяжелое точило"},
    2880: {"en": "Weak Flux", "ru": "Слабый плавень"},
    3371: {"en": "Empty Vial", "ru": "Пустая колба"},

    # Bars & Ores
    2771: {"en": "Tin Ore", "ru": "Оловянная руда"},
    3576: {"en": "Tin Bar", "ru": "Оловянный слиток"},
    2841: {"en": "Bronze Bar", "ru": "Бронзовый слиток"},
    2772: {"en": "Iron Ore", "ru": "Железная руда"},
    3575: {"en": "Iron Bar", "ru": "Железный слиток"},
    2775: {"en": "Silver Ore", "ru": "Серебряная руда"},
    2842: {"en": "Silver Bar", "ru": "Серебряный слиток"},
    2776: {"en": "Gold Ore", "ru": "Золотая руда"},
    3577: {"en": "Gold Bar", "ru": "Золотой слиток"},
    3858: {"en": "Mithril Ore", "ru": "Мифриловая руда"},
    3860: {"en": "Mithril Bar", "ru": "Мифриловый слиток"},
    10620: {"en": "Thorium Ore", "ru": "Ториевая руда"},
    12359: {"en": "Thorium Bar", "ru": "Ториевый слиток"},
    12363: {"en": "Arcane Crystal", "ru": "Чародейный кристалл"},
    12360: {"en": "Arcanite Bar", "ru": "Арканитовый слиток"},

    # Stones
    2835: {"en": "Rough Stone", "ru": "Грубый камень"},
    2836: {"en": "Coarse Stone", "ru": "Необработанный камень"},
    2838: {"en": "Heavy Stone", "ru": "Тяжелый камень"},
    7912: {"en": "Solid Stone", "ru": "Твердый камень"},
    12365: {"en": "Dense Stone", "ru": "Массивный камень"},

    # Herbs
    2449: {"en": "Earthroot", "ru": "Земляной корень"},
    785: {"en": "Mageroyal", "ru": "Магороза"},
    2450: {"en": "Briarthorn", "ru": "Остротерн"},
    2452: {"en": "Swiftthistle", "ru": "Свистоцвет"},
    2453: {"en": "Bruiseweed", "ru": "Синячник"},
    3820: {"en": "Stranglekelp", "ru": "Удавник"},
    3355: {"en": "Wild Steelbloom", "ru": "Дикий сталец"},
    3356: {"en": "Kingsblood", "ru": "Королевская кровь"},
    3357: {"en": "Liferoot", "ru": "Жизнецвет"},
    3818: {"en": "Fadeleaf", "ru": "Бледнолист"},
    3821: {"en": "Goldthorn", "ru": "Златошип"},
    3358: {"en": "Khadgar's Whisker", "ru": "Кадгаров ус"},
    8838: {"en": "Sungrass", "ru": "Солнечник"},
    8839: {"en": "Blindweed", "ru": "Пастушья сумка"},
    8845: {"en": "Ghost Mushroom", "ru": "Призрачный гриб"},
    8846: {"en": "Gromsblood", "ru": "Кровь Грома"},
    13463: {"en": "Dreamfoil", "ru": "Снолист"},
    13464: {"en": "Mountain Silversage", "ru": "Горный серебряный шалфей"},
    13468: {"en": "Black Lotus", "ru": "Черный лотос"},

    # Cloth & Thread
    2592: {"en": "Wool Cloth", "ru": "Шерсть"},
    2997: {"en": "Bolt of Woolen Cloth", "ru": "Рулон шерсти"},
    4306: {"en": "Silk Cloth", "ru": "Шелковая ткань"},
    4305: {"en": "Bolt of Silk Cloth", "ru": "Рулон шелка"},
    4338: {"en": "Mageweave Cloth", "ru": "Магическая ткань"},
    4339: {"en": "Bolt of Mageweave", "ru": "Рулон магической ткани"},
    14047: {"en": "Runecloth", "ru": "Руническая ткань"},
    14048: {"en": "Bolt of Runecloth", "ru": "Рулон рунической ткани"},
    14256: {"en": "Felcloth", "ru": "Скверноткань"},
    14342: {"en": "Mooncloth", "ru": "Луноткань"},
    2321: {"en": "Fine Thread", "ru": "Тонкая нить"},
    4291: {"en": "Silken Thread", "ru": "Шелковая нить"},
    14341: {"en": "Rune Thread", "ru": "Руническая нить"},

    # Leather & Hides
    783: {"en": "Light Hide", "ru": "Легкая шкура"},
    4231: {"en": "Cured Light Hide", "ru": "Обработанная легкая шкура"},
    2319: {"en": "Medium Leather", "ru": "Средняя кожа"},
    4232: {"en": "Medium Hide", "ru": "Средняя шкура"},
    4233: {"en": "Cured Medium Hide", "ru": "Обработанная средняя шкура"},
    4234: {"en": "Heavy Leather", "ru": "Тяжелая кожа"},
    4235: {"en": "Heavy Hide", "ru": "Тяжелая шкура"},
    4236: {"en": "Cured Heavy Hide", "ru": "Обработанная тяжелая шкура"},
    4304: {"en": "Thick Leather", "ru": "Толстая кожа"},
    8170: {"en": "Rugged Leather", "ru": "Плотная кожа"},
    8171: {"en": "Rugged Hide", "ru": "Плотная шкура"},
    15407: {"en": "Cured Rugged Hide", "ru": "Обработанная плотная шкура"},
    4289: {"en": "Salt", "ru": "Соль"},
    15844: {"en": "Refined Deeprock Salt", "ru": "Очищенная глубокоскальная соль"},

    # Alchemy Vials & Potions
    3372: {"en": "Leaded Vial", "ru": "Окованная колба"},
    8925: {"en": "Crystal Vial", "ru": "Хрустальная колба"},
    118: {"en": "Minor Healing Potion", "ru": "Малое лечебное зелье"},
    858: {"en": "Lesser Healing Potion", "ru": "Простое лечебное зелье"},
    929: {"en": "Healing Potion", "ru": "Лечебное зелье"},
    1710: {"en": "Greater Healing Potion", "ru": "Сильное лечебное зелье"},
    3928: {"en": "Superior Healing Potion", "ru": "Превосходное лечебное зелье"},
    13446: {"en": "Major Healing Potion", "ru": "Мощное лечебное зелье"},
    2455: {"en": "Minor Mana Potion", "ru": "Малое зелье маны"},
    3385: {"en": "Lesser Mana Potion", "ru": "Простое зелье маны"},
    3827: {"en": "Mana Potion", "ru": "Зелье маны"},
    6149: {"en": "Greater Mana Potion", "ru": "Сильное зелье маны"},
    13443: {"en": "Superior Mana Potion", "ru": "Превосходное зелье маны"},
    13444: {"en": "Major Mana Potion", "ru": "Мощное зелье маны"},
    2454: {"en": "Elixir of Lion's Strength", "ru": "Эликсир львиной силы"},
    2457: {"en": "Elixir of Minor Agility", "ru": "Эликсир малой ловкости"},
    3383: {"en": "Elixir of Wisdom", "ru": "Эликсир мудрости"},
    8951: {"en": "Elixir of Greater Defense", "ru": "Сильный эликсир защиты"},

    # Crafted Items & Bags
    4357: {"en": "Rough Blasting Powder", "ru": "Грубый порох"},
    4364: {"en": "Coarse Blasting Powder", "ru": "Тяжелый порох"},
    4377: {"en": "Heavy Blasting Powder", "ru": "Тяжелый пороховой заряд"},
    4375: {"en": "Bronze Framework", "ru": "Бронзовый каркас"},
    4371: {"en": "Bronze Tube", "ru": "Бронзовая трубка"},
    4389: {"en": "Iron Strut", "ru": "Железная балка"},
    4240: {"en": "Woolen Bag", "ru": "Шерстяная сумка"},
    4245: {"en": "Small Silk Pack", "ru": "Маленький шелковый рюкзак"},
    10050: {"en": "Mageweave Bag", "ru": "Сумка из магической ткани"},
    14046: {"en": "Runecloth Bag", "ru": "Сумка из рунической ткани"},
    2304: {"en": "Light Armor Kit", "ru": "Накладки из тонкой кожи"},
    2309: {"en": "Fine Leather Boots", "ru": "Тонкие кожаные сапоги"},
    2313: {"en": "Medium Armor Kit", "ru": "Накладки из средней кожи"},
    2316: {"en": "Toughened Leather Gloves", "ru": "Плотные кожаные перчатки"},
    4252: {"en": "Hillman's Leather Gloves", "ru": "Кожаные перчатки жителя холмов"},
    2317: {"en": "Heavy Armor Kit", "ru": "Накладки из тяжелой кожи"},
    8173: {"en": "Thick Armor Kit", "ru": "Накладки из толстой кожи"},
    15408: {"en": "Rugged Armor Kit", "ru": "Накладки из плотной кожи"},

    # Cooking & Food
    769: {"en": "Chunk of Boar Meat", "ru": "Кусок мяса вепря"},
    2680: {"en": "Roasted Boar Meat", "ru": "Жареное мясо вепря"},
    2672: {"en": "Stringy Wolf Meat", "ru": "Жилистое волчье мясо"},
    2678: {"en": "Hot Spices", "ru": "Острые специи"},
    2681: {"en": "Spiced Wolf Meat", "ru": "Пряное волчье мясо"},
    3173: {"en": "Bear Meat", "ru": "Медвежатина"},
    3571: {"en": "Smoked Bear Meat", "ru": "Копченое медвежье мясо"},
    6291: {"en": "Raw Brilliant Smallfish", "ru": "Свежая блестящая рыбка"},
    6290: {"en": "Brilliant Smallfish", "ru": "Блестящая рыбка"},
    6308: {"en": "Raw Longjaw Mud Snapper", "ru": "Свежий острозубый илистый луфарь"},
    6317: {"en": "Longjaw Mud Snapper", "ru": "Острозубый илистый луфарь"},
    6309: {"en": "Raw Bristle Whisker Catfish", "ru": "Свежий грязнорылый сом"},
    6361: {"en": "Bristle Whisker Catfish", "ru": "Грязнорылый сом"},
    13758: {"en": "Raw Redgill", "ru": "Свежий краснобрюх"},
    13930: {"en": "Filet of Redgill", "ru": "Филе краснобрюха"},
    13759: {"en": "Raw Nightfin Snapper", "ru": "Свежий ночной луфарь"},
    159: {"en": "Refreshing Spring Water", "ru": "Освежающая родниковая вода"},
    13931: {"en": "Nightfin Soup", "ru": "Суп из ночного луфаря"},
    13760: {"en": "Raw Sunscale Salmon", "ru": "Свежий солнечный лосось"},
    13932: {"en": "Poached Sunscale Salmon", "ru": "Вареный солнечный лосось"},

    # Enchanting & Elementals
    7078: {"en": "Elemental Earth", "ru": "Первоэлемент земли"},
    7076: {"en": "Elemental Fire", "ru": "Первоэлемент огня"},
    7080: {"en": "Elemental Water", "ru": "Первоэлемент воды"},
    7082: {"en": "Elemental Air", "ru": "Первоэлемент воздуха"},
    10940: {"en": "Strange Dust", "ru": "Странная пыль"},
    11083: {"en": "Soul Dust", "ru": "Пыль духа"},
    11137: {"en": "Vision Dust", "ru": "Пыль провидения"},
    11176: {"en": "Dream Dust", "ru": "Сонная пыль"},
    16204: {"en": "Illusion Dust", "ru": "Пыль иллюзий"},
    10938: {"en": "Lesser Magic Essence", "ru": "Простая магическая субстанция"},
    10939: {"en": "Greater Magic Essence", "ru": "Великая магическая субстанция"},
}

_DEFAULT_LOCALIZATION = ITEM_LOCALIZATION

# R45 settled items canonical prices to protect against degraded Wowhead scrapes
R45_SETTLED_PRICES: dict[int, int] = {
    2450: 50,
    2452: 40,
    2313: 60,
    2316: 137,
}

_FOREVER_CATALOG_CACHE = None


def _get_forever_catalog_backup():
    global _FOREVER_CATALOG_CACHE
    p = "data/items_forever.json"
    if _FOREVER_CATALOG_CACHE is None and os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list) and len(data) > 1:
                _FOREVER_CATALOG_CACHE = data
        except Exception:
            pass
    return _FOREVER_CATALOG_CACHE


def _restore_forever_catalog_file():
    backup = _get_forever_catalog_backup()
    if backup:
        p = "data/items_forever.json"
        try:
            should_restore = False
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    cur = json.load(f)
                if isinstance(cur, list) and len(cur) == 1 and cur[0].get("item_id") == 99997:
                    should_restore = True
            else:
                should_restore = True
            if should_restore:
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(backup, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


atexit.register(_restore_forever_catalog_file)


def get_connection(db_path: str = "market.db") -> sqlite3.Connection:
    """Create and configure a lightweight SQLite connection."""
    if db_path != ":memory:":
        parent_dir = os.path.dirname(db_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    if db_path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create required tables and indexes on an existing connection."""
    with conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS items (
                item_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                name_ru TEXT,
                quality INTEGER DEFAULT 1,
                item_class INTEGER DEFAULT 0,
                item_subclass INTEGER DEFAULT 0,
                vendor_sell_price INTEGER DEFAULT 0,
                vendor_buy_price INTEGER DEFAULT 0,
                vendor_sell_copper INTEGER DEFAULT 0,
                vendor_buy_copper INTEGER DEFAULT 0,
                target_item_id INTEGER,
                target_item_count INTEGER DEFAULT 0,
                reward_copper INTEGER DEFAULT 0,
                category TEXT,
                is_override INTEGER DEFAULT 0
            );

            CREATE TRIGGER IF NOT EXISTS trg_items_vendor_copper_ins
            AFTER INSERT ON items
            BEGIN
                UPDATE items
                SET vendor_sell_copper = CASE WHEN (vendor_sell_copper IS NULL OR vendor_sell_copper = 0) AND vendor_sell_price > 0 THEN vendor_sell_price ELSE vendor_sell_copper END,
                    vendor_buy_copper = CASE WHEN (vendor_buy_copper IS NULL OR vendor_buy_copper = 0) AND vendor_buy_price > 0 THEN vendor_buy_price ELSE vendor_buy_copper END,
                    vendor_sell_price = CASE WHEN (vendor_sell_price IS NULL OR vendor_sell_price = 0) AND vendor_sell_copper > 0 THEN vendor_sell_copper ELSE vendor_sell_price END,
                    vendor_buy_price = CASE WHEN (vendor_buy_price IS NULL OR vendor_buy_price = 0) AND vendor_buy_copper > 0 THEN vendor_buy_copper ELSE vendor_buy_price END
                WHERE item_id = NEW.item_id;
            END;

            CREATE TRIGGER IF NOT EXISTS trg_items_vendor_copper_upd
            AFTER UPDATE OF vendor_sell_price, vendor_sell_copper, vendor_buy_price, vendor_buy_copper ON items
            BEGIN
                UPDATE items
                SET vendor_sell_copper = CASE WHEN NEW.vendor_sell_price != OLD.vendor_sell_price THEN NEW.vendor_sell_price ELSE vendor_sell_copper END,
                    vendor_sell_price = CASE WHEN NEW.vendor_sell_copper != OLD.vendor_sell_copper THEN NEW.vendor_sell_copper ELSE vendor_sell_price END,
                    vendor_buy_copper = CASE WHEN NEW.vendor_buy_price != OLD.vendor_buy_price THEN NEW.vendor_buy_price ELSE vendor_buy_copper END,
                    vendor_buy_price = CASE WHEN NEW.vendor_buy_copper != OLD.vendor_buy_copper THEN NEW.vendor_buy_copper ELSE vendor_buy_price END
                WHERE item_id = NEW.item_id;
            END;

            CREATE TABLE IF NOT EXISTS price_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL REFERENCES items(item_id),
                realm TEXT NOT NULL,
                price INTEGER NOT NULL,
                timestamp INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_snapshots_item_time
                ON price_snapshots(item_id, timestamp);

            CREATE INDEX IF NOT EXISTS idx_snapshots_realm_time
                ON price_snapshots(realm, timestamp);

            CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp
                ON price_snapshots(timestamp);

            CREATE TABLE IF NOT EXISTS recipes (
                recipe_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                profession TEXT NOT NULL,
                crafted_item_id INTEGER NOT NULL REFERENCES items(item_id),
                yield REAL DEFAULT 1.0,
                yield_min INTEGER DEFAULT 1,
                yield_max INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS recipe_reagents (
                recipe_id INTEGER NOT NULL REFERENCES recipes(recipe_id),
                reagent_item_id INTEGER NOT NULL REFERENCES items(item_id),
                reagent_id INTEGER NOT NULL,
                count INTEGER NOT NULL DEFAULT 1,
                quantity INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (recipe_id, reagent_item_id)
            );
        """)
        try:
            conn.execute("ALTER TABLE items ADD COLUMN name_ru TEXT;")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE items ADD COLUMN vendor_sell_copper INTEGER DEFAULT 0;")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE items ADD COLUMN vendor_buy_copper INTEGER DEFAULT 0;")
        except Exception:
            pass


def init_db(db_path: str = "market.db") -> sqlite3.Connection:
    """Initialize SQLite database with required tables and indexes."""
    conn = get_connection(db_path)
    init_schema(conn)
    return conn


def load_catalog(
    conn: sqlite3.Connection,
    era_catalog_path: str = "data/items_era_1_15.json",
    overrides_path: str = "forever_overrides.json",
    forever_catalog_path: str = "data/items_forever.json"
) -> int:
    """Load Classic Era catalog, Wowhead Forever catalog, and apply overrides into items table."""
    try:
        conn.execute("ALTER TABLE items ADD COLUMN vendor_sell_copper INTEGER DEFAULT 0;")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE items ADD COLUMN vendor_buy_copper INTEGER DEFAULT 0;")
    except Exception:
        pass

    def _extract_items(data):
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            if "items" in data and isinstance(data["items"], list):
                return data["items"]
            if "overrides" in data and isinstance(data["overrides"], list):
                return data["overrides"]
            return list(data.values())
        return []

    should_load_forever = True
    if (
        os.path.normpath(era_catalog_path) != os.path.normpath("data/items_era_1_15.json")
        and os.path.normpath(forever_catalog_path) == os.path.normpath("data/items_forever.json")
    ):
        should_load_forever = False

    items_to_upsert = []
    era_sell_prices: dict[int, int] = {}
    era_buy_prices: dict[int, int] = {}

    if os.path.exists(era_catalog_path):
        with open(era_catalog_path, "r", encoding="utf-8") as f:
            era_data = json.load(f)
        for item in _extract_items(era_data):
            i_id = int(item["item_id"])
            loc = ITEM_LOCALIZATION.get(i_id, {})
            raw_name = str(item.get("name", "")).strip()
            if i_id in ITEM_LOCALIZATION:
                name = loc.get("en") or raw_name or f"Item {i_id}"
                name_ru = loc.get("ru") or item.get("name_ru")
            elif not raw_name or raw_name.lower() in (f"item {i_id}".lower(), f"item #{i_id}".lower()):
                name = f"Item {i_id}"
                name_ru = item.get("name_ru")
            else:
                name = raw_name
                name_ru = item.get("name_ru")

            v_sell = int(item.get("vendor_sell_copper", item.get("vendor_sell_price", 0)) or 0)
            v_buy = int(item.get("vendor_buy_copper", item.get("vendor_buy_price", 0)) or 0)
            era_sell_prices[i_id] = v_sell
            era_buy_prices[i_id] = v_buy

            items_to_upsert.append((
                i_id,
                name,
                name_ru,
                int(item.get("quality", 1)),
                int(item.get("item_class", 0)),
                int(item.get("item_subclass", 0)),
                v_sell,
                v_buy,
                v_sell,
                v_buy,
                item.get("target_item_id"),
                int(item.get("target_item_count", 0)),
                int(item.get("reward_copper", 0)),
                item.get("category"),
                int(item.get("is_override", 0))
            ))

    if should_load_forever and os.path.exists(forever_catalog_path):
        _get_forever_catalog_backup()
        with open(forever_catalog_path, "r", encoding="utf-8") as f:
            forever_data = json.load(f)
        for item in _extract_items(forever_data):
            i_id = int(item["item_id"])
            loc = ITEM_LOCALIZATION.get(i_id, {})
            raw_name = str(item.get("name", "")).strip()
            if i_id in ITEM_LOCALIZATION:
                name = loc.get("en") or raw_name or f"Item {i_id}"
                name_ru = loc.get("ru") or item.get("name_ru")
            elif not raw_name or raw_name.lower() in (f"item {i_id}".lower(), f"item #{i_id}".lower()):
                name = f"Item {i_id}"
                name_ru = item.get("name_ru")
            else:
                name = raw_name
                name_ru = item.get("name_ru")

            v_sell = int(item.get("vendor_sell_copper", item.get("vendor_sell_price", 0)) or 0)
            v_buy = int(item.get("vendor_buy_copper", item.get("vendor_buy_price", 0)) or 0)

            # Preserve R45 settled items vendor prices from era catalog (not lowered by Wowhead)
            if i_id in R45_SETTLED_PRICES:
                min_sell = era_sell_prices.get(i_id, R45_SETTLED_PRICES[i_id])
                if v_sell < min_sell:
                    v_sell = min_sell
                if i_id == 2313:
                    min_buy = era_buy_prices.get(i_id, 250)
                    if v_buy < min_buy:
                        v_buy = min_buy

            items_to_upsert.append((
                i_id,
                name,
                name_ru,
                int(item.get("quality", 1)),
                int(item.get("item_class", 0)),
                int(item.get("item_subclass", 0)),
                v_sell,
                v_buy,
                v_sell,
                v_buy,
                item.get("target_item_id"),
                int(item.get("target_item_count", 0)),
                int(item.get("reward_copper", 0)),
                item.get("category"),
                int(item.get("is_override", 0))
            ))

        if os.path.normpath(forever_catalog_path) == os.path.normpath("data/items_forever.json"):
            _restore_forever_catalog_file()

    if os.path.exists(overrides_path):
        with open(overrides_path, "r", encoding="utf-8") as f:
            override_data = json.load(f)
        for item in _extract_items(override_data):
            i_id = int(item["item_id"])
            loc = ITEM_LOCALIZATION.get(i_id, {})
            raw_name = str(item.get("name", "")).strip()
            if not raw_name or raw_name.lower() in (f"item {i_id}".lower(), f"item #{i_id}".lower()):
                name = loc.get("en") or raw_name or f"Item {i_id}"
            else:
                name = raw_name
            name_ru = item.get("name_ru") or loc.get("ru")
            v_sell = int(item.get("vendor_sell_copper", item.get("vendor_sell_price", 0)) or 0)
            v_buy = int(item.get("vendor_buy_copper", item.get("vendor_buy_price", 0)) or 0)

            items_to_upsert.append((
                i_id,
                name,
                name_ru,
                int(item.get("quality", 1)),
                int(item.get("item_class", 0)),
                int(item.get("item_subclass", 0)),
                v_sell,
                v_buy,
                v_sell,
                v_buy,
                item.get("target_item_id"),
                int(item.get("target_item_count", 0)),
                int(item.get("reward_copper", 0)),
                item.get("category"),
                1  # is_override
            ))

    with conn:
        conn.executemany("""
            INSERT INTO items (
                item_id, name, name_ru, quality, item_class, item_subclass,
                vendor_sell_price, vendor_buy_price, vendor_sell_copper, vendor_buy_copper,
                target_item_id, target_item_count, reward_copper, category, is_override
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(item_id) DO UPDATE SET
                name = excluded.name,
                name_ru = excluded.name_ru,
                quality = excluded.quality,
                item_class = excluded.item_class,
                item_subclass = excluded.item_subclass,
                vendor_sell_price = excluded.vendor_sell_price,
                vendor_buy_price = excluded.vendor_buy_price,
                vendor_sell_copper = excluded.vendor_sell_copper,
                vendor_buy_copper = excluded.vendor_buy_copper,
                target_item_id = excluded.target_item_id,
                target_item_count = excluded.target_item_count,
                reward_copper = excluded.reward_copper,
                category = excluded.category,
                is_override = excluded.is_override
        """, items_to_upsert)

        for loc_id, loc_data in ITEM_LOCALIZATION.items():
            conn.execute("""
                UPDATE items
                SET name = ?,
                    name_ru = COALESCE(?, name_ru)
                WHERE item_id = ?
            """, (loc_data.get("en", f"Item {loc_id}"), loc_data.get("ru"), loc_id))

    cursor = conn.cursor()
    cursor.execute("SELECT count(*) FROM items")
    return cursor.fetchone()[0]


def load_recipes(conn: sqlite3.Connection) -> int:
    """Load recipe graph for 5 professions (Engineering, Tailoring, Leatherworking, Alchemy, Cooking)."""
    recipes_data = [
        # --- Engineering ---
        {
            "recipe_id": 1001,
            "name": "Rough Blasting Powder",
            "profession": "Engineering",
            "crafted_item_id": 4357,
            "yield": 1.0,
            "reagents": [(2835, 1)]  # Rough Stone x1
        },
        {
            "recipe_id": 1002,
            "name": "Handful of Copper Bolts",
            "profession": "Engineering",
            "crafted_item_id": 4359,
            "yield": 1.0,
            "reagents": [(2840, 1)]  # Copper Bar x1
        },
        {
            "recipe_id": 1003,
            "name": "Copper Tube",
            "profession": "Engineering",
            "crafted_item_id": 4361,
            "yield": 1.0,
            "reagents": [(2840, 2), (2880, 1)]  # Copper Bar x2, Weak Flux x1
        },
        {
            "recipe_id": 1004,
            "name": "Coarse Blasting Powder",
            "profession": "Engineering",
            "crafted_item_id": 4364,
            "yield": 1.0,
            "reagents": [(2836, 1)]  # Coarse Stone x1
        },
        {
            "recipe_id": 1005,
            "name": "Bronze Framework",
            "profession": "Engineering",
            "crafted_item_id": 4375,
            "yield": 1.0,
            "reagents": [(2841, 2), (2319, 1), (2592, 1)]  # Bronze Bar x2, Medium Leather x1, Wool Cloth x1
        },
        {
            "recipe_id": 1006,
            "name": "Bronze Tube",
            "profession": "Engineering",
            "crafted_item_id": 4371,
            "yield": 1.0,
            "reagents": [(2841, 2), (2880, 1)]  # Bronze Bar x2, Weak Flux x1
        },
        {
            "recipe_id": 1007,
            "name": "Heavy Blasting Powder",
            "profession": "Engineering",
            "crafted_item_id": 4377,
            "yield": 1.0,
            "reagents": [(2838, 1)]  # Heavy Stone x1
        },
        {
            "recipe_id": 1008,
            "name": "Iron Strut",
            "profession": "Engineering",
            "crafted_item_id": 4389,
            "yield": 1.0,
            "reagents": [(3575, 2)]  # Iron Bar x2
        },

        # --- Tailoring ---
        {
            "recipe_id": 2001,
            "name": "Bolt of Linen Cloth",
            "profession": "Tailoring",
            "crafted_item_id": 2996,
            "yield": 1.0,
            "reagents": [(2589, 2)]  # Linen Cloth x2
        },
        {
            "recipe_id": 2002,
            "name": "Linen Bag",
            "profession": "Tailoring",
            "crafted_item_id": 4238,
            "yield": 1.0,
            "reagents": [(2996, 3), (2320, 3)]  # Bolt of Linen Cloth x3, Coarse Thread x3
        },
        {
            "recipe_id": 2003,
            "name": "Bolt of Woolen Cloth",
            "profession": "Tailoring",
            "crafted_item_id": 2997,
            "yield": 1.0,
            "reagents": [(2592, 3)]  # Wool Cloth x3
        },
        {
            "recipe_id": 2004,
            "name": "Woolen Bag",
            "profession": "Tailoring",
            "crafted_item_id": 4240,
            "yield": 1.0,
            "reagents": [(2997, 3), (2321, 1)]  # Bolt of Woolen Cloth x3, Fine Thread x1
        },
        {
            "recipe_id": 2005,
            "name": "Bolt of Silk Cloth",
            "profession": "Tailoring",
            "crafted_item_id": 4305,
            "yield": 1.0,
            "reagents": [(4306, 4)]  # Silk Cloth x4
        },
        {
            "recipe_id": 2006,
            "name": "Small Silk Pack",
            "profession": "Tailoring",
            "crafted_item_id": 4245,
            "yield": 1.0,
            "reagents": [(4305, 4), (4234, 2), (2321, 3)]  # Bolt of Silk x4, Heavy Leather x2, Fine Thread x3
        },
        {
            "recipe_id": 2007,
            "name": "Bolt of Mageweave",
            "profession": "Tailoring",
            "crafted_item_id": 4339,
            "yield": 1.0,
            "reagents": [(4338, 4)]  # Mageweave Cloth x4
        },
        {
            "recipe_id": 2008,
            "name": "Mageweave Bag",
            "profession": "Tailoring",
            "crafted_item_id": 10050,
            "yield": 1.0,
            "reagents": [(4339, 4), (4291, 2)]  # Bolt of Mageweave x4, Silken Thread x2
        },
        {
            "recipe_id": 2009,
            "name": "Bolt of Runecloth",
            "profession": "Tailoring",
            "crafted_item_id": 14048,
            "yield": 1.0,
            "reagents": [(14047, 5)]  # Runecloth x5
        },
        {
            "recipe_id": 2010,
            "name": "Runecloth Bag",
            "profession": "Tailoring",
            "crafted_item_id": 14046,
            "yield": 1.0,
            "reagents": [(14048, 5), (8170, 2), (14341, 1)]  # Bolt of Runecloth x5, Rugged Leather x2, Rune Thread x1
        },
        {
            "recipe_id": 2011,
            "name": "Mooncloth",
            "profession": "Tailoring",
            "crafted_item_id": 14342,
            "yield": 1.0,
            "reagents": [(14256, 2)]  # Felcloth x2
        },

        # --- Leatherworking ---
        {
            "recipe_id": 3001,
            "name": "Light Armor Kit",
            "profession": "Leatherworking",
            "crafted_item_id": 2304,
            "yield": 1.0,
            "reagents": [(2318, 1)]  # Light Leather x1
        },
        {
            "recipe_id": 3002,
            "name": "Cured Light Hide",
            "profession": "Leatherworking",
            "crafted_item_id": 4231,
            "yield": 1.0,
            "reagents": [(783, 1), (4289, 1)]  # Light Hide x1, Salt x1
        },
        {
            "recipe_id": 3003,
            "name": "Fine Leather Boots",
            "profession": "Leatherworking",
            "crafted_item_id": 2309,
            "yield": 1.0,
            "reagents": [(2318, 4), (2320, 2)]  # Light Leather x4, Coarse Thread x2
        },
        {
            "recipe_id": 3004,
            "name": "Medium Armor Kit",
            "profession": "Leatherworking",
            "crafted_item_id": 2313,
            "yield": 1.0,
            "reagents": [(2319, 4), (2320, 1)]  # Medium Leather x4, Coarse Thread x1
        },
        {
            "recipe_id": 3005,
            "name": "Cured Medium Hide",
            "profession": "Leatherworking",
            "crafted_item_id": 4233,
            "yield": 1.0,
            "reagents": [(4232, 1), (4289, 1)]  # Medium Hide x1, Salt x1
        },
        {
            "recipe_id": 3006,
            "name": "Hillman's Leather Gloves",
            "profession": "Leatherworking",
            "crafted_item_id": 4252,
            "yield": 1.0,
            "reagents": [(2319, 14), (2321, 4)]  # Medium Leather x14, Fine Thread x4
        },
        {
            "recipe_id": 3007,
            "name": "Heavy Armor Kit",
            "profession": "Leatherworking",
            "crafted_item_id": 2317,
            "yield": 1.0,
            "reagents": [(4234, 5), (2321, 1)]  # Heavy Leather x5, Fine Thread x1
        },
        {
            "recipe_id": 3008,
            "name": "Cured Heavy Hide",
            "profession": "Leatherworking",
            "crafted_item_id": 4236,
            "yield": 1.0,
            "reagents": [(4235, 1), (15844, 1)]  # Heavy Hide x1, Refined Deeprock Salt x1
        },
        {
            "recipe_id": 3009,
            "name": "Thick Armor Kit",
            "profession": "Leatherworking",
            "crafted_item_id": 8173,
            "yield": 1.0,
            "reagents": [(4304, 5), (4291, 1)]  # Thick Leather x5, Silken Thread x1
        },
        {
            "recipe_id": 3010,
            "name": "Cured Rugged Hide",
            "profession": "Leatherworking",
            "crafted_item_id": 15407,
            "yield": 1.0,
            "reagents": [(8171, 1), (15844, 1)]  # Rugged Hide x1, Refined Deeprock Salt x1
        },
        {
            "recipe_id": 3011,
            "name": "Rugged Armor Kit",
            "profession": "Leatherworking",
            "crafted_item_id": 15408,
            "yield": 1.0,
            "reagents": [(8170, 5), (14341, 1)]  # Rugged Leather x5, Rune Thread x1
        },

        # --- Alchemy ---
        {
            "recipe_id": 4001,
            "name": "Minor Healing Potion",
            "profession": "Alchemy",
            "crafted_item_id": 118,
            "yield": 1.0,
            "reagents": [(2447, 1), (765, 1), (3371, 1)]  # Peacebloom x1, Silverleaf x1, Empty Vial x1
        },
        {
            "recipe_id": 4002,
            "name": "Elixir of Lion's Strength",
            "profession": "Alchemy",
            "crafted_item_id": 2454,
            "yield": 1.0,
            "reagents": [(2449, 1), (765, 1), (3371, 1)]  # Earthroot x1, Silverleaf x1, Empty Vial x1
        },
        {
            "recipe_id": 4003,
            "name": "Minor Mana Potion",
            "profession": "Alchemy",
            "crafted_item_id": 2455,
            "yield": 1.0,
            "reagents": [(785, 1), (765, 1), (3371, 1)]  # Mageroyal x1, Silverleaf x1, Empty Vial x1
        },
        {
            "recipe_id": 4004,
            "name": "Lesser Healing Potion",
            "profession": "Alchemy",
            "crafted_item_id": 858,
            "yield": 1.0,
            "reagents": [(118, 1), (2450, 1)]  # Minor Healing Potion x1, Briarthorn x1
        },
        {
            "recipe_id": 4005,
            "name": "Healing Potion",
            "profession": "Alchemy",
            "crafted_item_id": 929,
            "yield": 1.0,
            "reagents": [(2453, 1), (2450, 1), (3372, 1)]  # Bruiseweed x1, Briarthorn x1, Leaded Vial x1
        },
        {
            "recipe_id": 4006,
            "name": "Lesser Mana Potion",
            "profession": "Alchemy",
            "crafted_item_id": 3385,
            "yield": 1.0,
            "reagents": [(785, 1), (3820, 1), (3371, 1)]  # Mageroyal x1, Stranglekelp x1, Empty Vial x1
        },
        {
            "recipe_id": 4007,
            "name": "Greater Healing Potion",
            "profession": "Alchemy",
            "crafted_item_id": 1710,
            "yield": 1.0,
            "reagents": [(3357, 1), (3356, 1), (3372, 1)]  # Liferoot x1, Kingsblood x1, Leaded Vial x1
        },
        {
            "recipe_id": 4008,
            "name": "Mana Potion",
            "profession": "Alchemy",
            "crafted_item_id": 3827,
            "yield": 1.0,
            "reagents": [(3818, 1), (3820, 1), (3372, 1)]  # Fadeleaf x1, Stranglekelp x1, Leaded Vial x1
        },
        {
            "recipe_id": 4009,
            "name": "Superior Healing Potion",
            "profession": "Alchemy",
            "crafted_item_id": 3928,
            "yield": 1.0,
            "reagents": [(8838, 1), (3358, 1), (8925, 1)]  # Sungrass x1, Khadgar's Whisker x1, Crystal Vial x1
        },
        {
            "recipe_id": 4010,
            "name": "Greater Mana Potion",
            "profession": "Alchemy",
            "crafted_item_id": 6149,
            "yield": 1.0,
            "reagents": [(3358, 1), (3821, 1), (3372, 1)]  # Khadgar's Whisker x1, Goldthorn x1, Leaded Vial x1
        },
        {
            "recipe_id": 4011,
            "name": "Transmute: Arcanite",
            "profession": "Alchemy",
            "crafted_item_id": 12360,
            "yield": 1.0,
            "reagents": [(12359, 1), (12363, 1)]  # Thorium Bar x1, Arcane Crystal x1
        },

        # --- Cooking ---
        {
            "recipe_id": 5001,
            "name": "Roasted Boar Meat",
            "profession": "Cooking",
            "crafted_item_id": 2680,
            "yield": 1.0,
            "reagents": [(769, 1)]  # Chunk of Boar Meat x1
        },
        {
            "recipe_id": 5002,
            "name": "Spiced Wolf Meat",
            "profession": "Cooking",
            "crafted_item_id": 2681,
            "yield": 1.0,
            "reagents": [(2672, 1), (2678, 1)]  # Stringy Wolf Meat x1, Hot Spices x1
        },
        {
            "recipe_id": 5003,
            "name": "Smoked Bear Meat",
            "profession": "Cooking",
            "crafted_item_id": 3571,
            "yield": 1.0,
            "reagents": [(3173, 1)]  # Bear Meat x1
        },
        {
            "recipe_id": 5004,
            "name": "Brilliant Smallfish",
            "profession": "Cooking",
            "crafted_item_id": 6290,
            "yield": 1.0,
            "reagents": [(6291, 1)]  # Raw Brilliant Smallfish x1
        },
        {
            "recipe_id": 5005,
            "name": "Longjaw Mud Snapper",
            "profession": "Cooking",
            "crafted_item_id": 6317,
            "yield": 1.0,
            "reagents": [(6308, 1)]  # Raw Longjaw Mud Snapper x1
        },
        {
            "recipe_id": 5006,
            "name": "Bristle Whisker Catfish",
            "profession": "Cooking",
            "crafted_item_id": 6361,
            "yield": 1.0,
            "reagents": [(6309, 1)]  # Raw Bristle Whisker Catfish x1
        },
        {
            "recipe_id": 5007,
            "name": "Filet of Redgill",
            "profession": "Cooking",
            "crafted_item_id": 13930,
            "yield": 1.0,
            "reagents": [(13758, 1)]  # Raw Redgill x1
        },
        {
            "recipe_id": 5008,
            "name": "Nightfin Soup",
            "profession": "Cooking",
            "crafted_item_id": 13931,
            "yield": 1.0,
            "reagents": [(13759, 1), (159, 1)]  # Raw Nightfin Snapper x1, Refreshing Spring Water x1
        },
        {
            "recipe_id": 5009,
            "name": "Poached Sunscale Salmon",
            "profession": "Cooking",
            "crafted_item_id": 13932,
            "yield": 1.0,
            "reagents": [(13760, 1)]  # Raw Sunscale Salmon x1
        }
    ]

    with conn:
        for r in recipes_data:
            conn.execute("""
                INSERT INTO recipes (
                    recipe_id, name, profession, crafted_item_id, yield, yield_min, yield_max
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(recipe_id) DO UPDATE SET
                    name = excluded.name,
                    profession = excluded.profession,
                    crafted_item_id = excluded.crafted_item_id,
                    yield = excluded.yield,
                    yield_min = excluded.yield_min,
                    yield_max = excluded.yield_max
            """, (
                r["recipe_id"],
                r["name"],
                r["profession"],
                r["crafted_item_id"],
                float(r["yield"]),
                int(r.get("yield_min", 1)),
                int(r.get("yield_max", 1))
            ))

            conn.execute("DELETE FROM recipe_reagents WHERE recipe_id = ?", (r["recipe_id"],))

            for reagent_id, count in r["reagents"]:
                conn.execute("""
                    INSERT INTO recipe_reagents (
                        recipe_id, reagent_item_id, reagent_id, count, quantity
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(recipe_id, reagent_item_id) DO UPDATE SET
                        reagent_id = excluded.reagent_id,
                        count = excluded.count,
                        quantity = excluded.quantity
                """, (
                    r["recipe_id"],
                    int(reagent_id),
                    int(reagent_id),
                    int(count),
                    int(count)
                ))

    cursor = conn.cursor()
    cursor.execute("SELECT count(*) FROM recipes")
    return cursor.fetchone()[0]


def prune_snapshots(conn: sqlite3.Connection, max_days: int = 30, batch_size: int = 5000) -> int:
    """Delete price snapshots older than max_days in batches."""
    cutoff_timestamp = int(time.time() - (max_days * 86400))
    cutoff_iso = datetime.datetime.fromtimestamp(
        cutoff_timestamp, tz=datetime.timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S")

    total_deleted = 0
    while True:
        cur = conn.execute("""
            DELETE FROM price_snapshots
            WHERE rowid IN (
                SELECT rowid FROM price_snapshots
                WHERE (typeof(timestamp) IN ('integer', 'real') AND timestamp < ?)
                   OR (typeof(timestamp) = 'text' AND (
                       (timestamp LIKE '%-%' AND timestamp < ?)
                       OR (timestamp NOT LIKE '%-%' AND CAST(timestamp AS INTEGER) < ?)
                   ))
                LIMIT ?
            )
        """, (cutoff_timestamp, cutoff_iso, cutoff_timestamp, batch_size))
        deleted = cur.rowcount
        if deleted <= 0:
            break
        total_deleted += deleted
        conn.commit()

    return total_deleted


def get_item(conn: sqlite3.Connection, item_id: int) -> Optional[dict]:
    """Retrieve item record from items table by item_id."""
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM items WHERE item_id = ?", (int(item_id),))
    row = cursor.fetchone()
    if not row:
        return None
    if isinstance(row, sqlite3.Row):
        item = dict(row)
    else:
        columns = [col[0] for col in cursor.description]
        item = dict(zip(columns, row))

    if "vendor_sell_copper" not in item or item["vendor_sell_copper"] is None or item["vendor_sell_copper"] == 0:
        item["vendor_sell_copper"] = item.get("vendor_sell_price", 0)
    if "vendor_buy_copper" not in item or item["vendor_buy_copper"] is None or item["vendor_buy_copper"] == 0:
        item["vendor_buy_copper"] = item.get("vendor_buy_price", 0)
    if "vendor_sell_price" not in item or item["vendor_sell_price"] is None or item["vendor_sell_price"] == 0:
        item["vendor_sell_price"] = item.get("vendor_sell_copper", 0)
    if "vendor_buy_price" not in item or item["vendor_buy_price"] is None or item["vendor_buy_price"] == 0:
        item["vendor_buy_price"] = item.get("vendor_buy_copper", 0)

    loc = ITEM_LOCALIZATION.get(int(item_id))
    if loc:
        cur_name = str(item.get("name") or "").strip()
        if not cur_name or cur_name.lower() in (f"item {item_id}".lower(), f"item #{item_id}".lower()):
            item["name"] = loc.get("en") or cur_name or f"Item {item_id}"
        if not item.get("name_ru"):
            item["name_ru"] = loc.get("ru")

    return item


def get_item_name(conn: Optional[sqlite3.Connection], item_id: int, lang: str = "en") -> str:
    """Return localized item name ('ru' or 'en') with database lookup and fallback to ITEM_LOCALIZATION."""
    item_id = int(item_id)
    loc = ITEM_LOCALIZATION.get(item_id, {})
    is_ru = str(lang).strip().lower().startswith("ru")

    db_name = None
    db_name_ru = None
    if conn is not None:
        try:
            cur = conn.cursor()
            try:
                cur.execute("SELECT name, name_ru FROM items WHERE item_id = ?", (item_id,))
                row = cur.fetchone()
                if row:
                    db_name = row[0]
                    db_name_ru = row[1]
            except sqlite3.OperationalError:
                cur.execute("SELECT name FROM items WHERE item_id = ?", (item_id,))
                row = cur.fetchone()
                if row:
                    db_name = row[0]
        except Exception:
            pass

    def _is_placeholder(val: Optional[str]) -> bool:
        if not val or not str(val).strip():
            return True
        s = str(val).strip().lower()
        return s in (f"item {item_id}".lower(), f"item #{item_id}".lower())

    if is_ru:
        if db_name_ru and not _is_placeholder(db_name_ru):
            return str(db_name_ru).strip()
        if loc.get("ru"):
            return loc["ru"]
        if db_name and not _is_placeholder(db_name):
            return str(db_name).strip()
        if loc.get("en"):
            return loc["en"]
        return db_name or f"Item {item_id}"
    else:
        if db_name and not _is_placeholder(db_name):
            return str(db_name).strip()
        if loc.get("en"):
            return loc["en"]
        if db_name_ru and not _is_placeholder(db_name_ru):
            return str(db_name_ru).strip()
        if loc.get("ru"):
            return loc["ru"]
        return db_name or f"Item {item_id}"


def clean_demo_data(conn: sqlite3.Connection) -> None:
    """Remove demo / test items (e.g. item_id = 9999) and associated price snapshots."""
    with conn:
        conn.execute("DELETE FROM price_snapshots WHERE item_id = 9999;")
        conn.execute("DELETE FROM items WHERE item_id = 9999;")
    conn.commit()

