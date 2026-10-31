"""SQLite database schema, catalog ingestion, recipe graph, and snapshot pruning."""

import datetime
import json
import os
import sqlite3
import time
from typing import Optional


def init_db(db_path: str = "market.db") -> sqlite3.Connection:
    """Initialize SQLite database with required tables and indexes."""
    if db_path != ":memory:":
        parent_dir = os.path.dirname(db_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    if db_path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL;")

    with conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS items (
                item_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                quality INTEGER DEFAULT 1,
                item_class INTEGER DEFAULT 0,
                item_subclass INTEGER DEFAULT 0,
                vendor_sell_price INTEGER DEFAULT 0,
                vendor_buy_price INTEGER DEFAULT 0,
                target_item_id INTEGER,
                target_item_count INTEGER DEFAULT 0,
                reward_copper INTEGER DEFAULT 0,
                category TEXT,
                is_override INTEGER DEFAULT 0
            );

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

    return conn


def load_catalog(
    conn: sqlite3.Connection,
    era_catalog_path: str = "data/items_era_1_15.json",
    overrides_path: str = "forever_overrides.json"
) -> int:
    """Load Classic Era catalog and apply Forever overrides into items table."""
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

    items_to_upsert = []

    if os.path.exists(era_catalog_path):
        with open(era_catalog_path, "r", encoding="utf-8") as f:
            era_data = json.load(f)
        for item in _extract_items(era_data):
            items_to_upsert.append((
                int(item["item_id"]),
                str(item.get("name", "")),
                int(item.get("quality", 1)),
                int(item.get("item_class", 0)),
                int(item.get("item_subclass", 0)),
                int(item.get("vendor_sell_price", 0)),
                int(item.get("vendor_buy_price", 0)),
                item.get("target_item_id"),
                int(item.get("target_item_count", 0)),
                int(item.get("reward_copper", 0)),
                item.get("category"),
                int(item.get("is_override", 0))
            ))

    if os.path.exists(overrides_path):
        with open(overrides_path, "r", encoding="utf-8") as f:
            override_data = json.load(f)
        for item in _extract_items(override_data):
            items_to_upsert.append((
                int(item["item_id"]),
                str(item.get("name", "")),
                int(item.get("quality", 1)),
                int(item.get("item_class", 0)),
                int(item.get("item_subclass", 0)),
                int(item.get("vendor_sell_price", 0)),
                int(item.get("vendor_buy_price", 0)),
                item.get("target_item_id"),
                int(item.get("target_item_count", 0)),
                int(item.get("reward_copper", 0)),
                item.get("category"),
                1  # is_override
            ))

    with conn:
        conn.executemany("""
            INSERT OR REPLACE INTO items (
                item_id, name, quality, item_class, item_subclass,
                vendor_sell_price, vendor_buy_price, target_item_id,
                target_item_count, reward_copper, category, is_override
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, items_to_upsert)

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
            "crafted_item_id": 2316,
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
            "reagents": [(118, 1), (2452, 1)]  # Minor Healing Potion x1, Briarthorn x1
        },
        {
            "recipe_id": 4005,
            "name": "Healing Potion",
            "profession": "Alchemy",
            "crafted_item_id": 929,
            "yield": 1.0,
            "reagents": [(2453, 1), (2452, 1), (3372, 1)]  # Bruiseweed x1, Briarthorn x1, Leaded Vial x1
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
                INSERT OR REPLACE INTO recipes (
                    recipe_id, name, profession, crafted_item_id, yield, yield_min, yield_max
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                r["recipe_id"],
                r["name"],
                r["profession"],
                r["crafted_item_id"],
                float(r["yield"]),
                int(r.get("yield_min", 1)),
                int(r.get("yield_max", 1))
            ))

            for reagent_id, count in r["reagents"]:
                conn.execute("""
                    INSERT OR REPLACE INTO recipe_reagents (
                        recipe_id, reagent_item_id, reagent_id, count, quantity
                    ) VALUES (?, ?, ?, ?, ?)
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


def prune_snapshots(conn: sqlite3.Connection, max_days: int = 30) -> int:
    """Delete price snapshots older than max_days."""
    cutoff_epoch = int(time.time()) - int(max_days * 86400)
    cutoff_iso = datetime.datetime.fromtimestamp(
        cutoff_epoch, tz=datetime.timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S")

    cursor = conn.cursor()
    cursor.execute("""
        DELETE FROM price_snapshots
        WHERE (typeof(timestamp) IN ('integer', 'real') AND timestamp < ?)
           OR (typeof(timestamp) = 'text' AND timestamp < ?)
    """, (cutoff_epoch, cutoff_iso))
    pruned = cursor.rowcount
    conn.commit()
    return max(0, pruned)


def get_item(conn: sqlite3.Connection, item_id: int) -> Optional[dict]:
    """Retrieve item record from items table by item_id."""
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM items WHERE item_id = ?", (int(item_id),))
    row = cursor.fetchone()
    if not row:
        return None
    if isinstance(row, sqlite3.Row):
        return dict(row)
    columns = [col[0] for col in cursor.description]
    return dict(zip(columns, row))
