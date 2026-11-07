import json
import os

herbs = [
    {"item_id": 2447, "name_ru": "\u041c\u0438\u0440\u043e\u0446\u0432\u0435\u0442", "name_en": "Peacebloom", "min_skill": 1, "quality": 1},
    {"item_id": 765, "name_ru": "\u0421\u0440\u0435\u0431\u0440\u043e\u043b\u0438\u0441\u0442", "name_en": "Silverleaf", "min_skill": 1, "quality": 1},
    {"item_id": 2449, "name_ru": "\u0417\u0435\u043c\u043b\u044f\u043d\u043e\u0439 \u043a\u043e\u0440\u0435\u043d\u044c", "name_en": "Earthroot", "min_skill": 15, "quality": 1},
    {"item_id": 785, "name_ru": "\u041c\u0430\u0433\u043e\u0440\u043e\u0437\u0430", "name_en": "Mageroyal", "min_skill": 50, "quality": 1},
    {"item_id": 2450, "name_ru": "\u041e\u0441\u0442\u0440\u043e\u0442\u0435\u0440\u043d", "name_en": "Briarthorn", "min_skill": 70, "quality": 1},
    {"item_id": 2452, "name_ru": "\u0421\u043a\u043e\u0440\u043e\u043f\u043e\u043b\u043e\u0445", "name_en": "Swiftthistle", "min_skill": 0, "quality": 1},
    {"item_id": 3820, "name_ru": "\u0423\u0434\u0430\u0432\u043d\u0438\u043a", "name_en": "Stranglekelp", "min_skill": 85, "quality": 1},
    {"item_id": 2453, "name_ru": "\u0421\u0438\u043d\u044f\u0447\u043d\u0438\u043a", "name_en": "Bruiseweed", "min_skill": 100, "quality": 1},
    {"item_id": 3355, "name_ru": "\u0414\u0438\u043a\u0438\u0439 \u0441\u0442\u0430\u043b\u0435\u0446", "name_en": "Wild Steelbloom", "min_skill": 115, "quality": 1},
    {"item_id": 3369, "name_ru": "\u041c\u043e\u0433\u0438\u043b\u044c\u043d\u044b\u0439 \u043c\u043e\u0445", "name_en": "Grave Moss", "min_skill": 120, "quality": 1},
    {"item_id": 3356, "name_ru": "\u041a\u043e\u0440\u043e\u043b\u0435\u0432\u0441\u043a\u0430\u044f \u043a\u0440\u043e\u0432\u044c", "name_en": "Kingsblood", "min_skill": 125, "quality": 1},
    {"item_id": 3357, "name_ru": "\u041a\u043e\u0440\u0435\u043d\u044c \u0436\u0438\u0437\u043d\u0438", "name_en": "Liferoot", "min_skill": 150, "quality": 1},
    {"item_id": 3818, "name_ru": "\u0411\u043b\u0435\u0434\u043d\u043e\u043b\u0438\u0441\u0442", "name_en": "Fadeleaf", "min_skill": 160, "quality": 1},
    {"item_id": 3821, "name_ru": "\u0417\u043b\u0430\u0442\u043e\u0448\u0438\u043f", "name_en": "Goldthorn", "min_skill": 170, "quality": 1},
    {"item_id": 3358, "name_ru": "\u041a\u0430\u0434\u0433\u0430\u0440\u043e\u0432 \u0443\u0441", "name_en": "Khadgar's Whisker", "min_skill": 185, "quality": 1},
    {"item_id": 3819, "name_ru": "\u041c\u043e\u0440\u043e\u0437\u043d\u0438\u043a", "name_en": "Wintersbite", "min_skill": 195, "quality": 1},
    {"item_id": 4625, "name_ru": "\u041e\u0433\u043d\u0435\u0446\u0432\u0435\u0442", "name_en": "Firebloom", "min_skill": 205, "quality": 1},
    {"item_id": 8831, "name_ru": "\u041b\u0438\u043b\u043e\u0432\u044b\u0439 \u043b\u043e\u0442\u043e\u0441", "name_en": "Purple Lotus", "min_skill": 210, "quality": 1},
    {"item_id": 8836, "name_ru": "\u0421\u043b\u0435\u0437\u043a\u0438 \u0410\u0440\u0442\u0430\u0441\u0430", "name_en": "Arthas' Tears", "min_skill": 220, "quality": 1},
    {"item_id": 8838, "name_ru": "\u0421\u043e\u043b\u043d\u0435\u0447\u043d\u0438\u043a", "name_en": "Sungrass", "min_skill": 230, "quality": 1},
    {"item_id": 8839, "name_ru": "\u041f\u0430\u0441\u0442\u0443\u0448\u044c\u044f \u0441\u0443\u043c\u043a\u0430", "name_en": "Blindweed", "min_skill": 235, "quality": 1},
    {"item_id": 8845, "name_ru": "\u041f\u0440\u0438\u0437\u0440\u0430\u0447\u043d\u044b\u0439 \u0433\u0440\u0438\u0431", "name_en": "Ghost Mushroom", "min_skill": 245, "quality": 1},
    {"item_id": 8846, "name_ru": "\u041a\u0440\u043e\u0432\u044c \u0413\u0440\u043e\u043c\u0430", "name_en": "Gromsblood", "min_skill": 250, "quality": 1},
    {"item_id": 13464, "name_ru": "\u0417\u043e\u043b\u043e\u0442\u043e\u0439 \u0441\u0430\u043d\u0441\u0430\u043c", "name_en": "Golden Sansam", "min_skill": 260, "quality": 1},
    {"item_id": 13463, "name_ru": "\u0421\u043d\u043e\u043b\u0438\u0441\u0442", "name_en": "Dreamfoil", "min_skill": 270, "quality": 1},
    {"item_id": 13465, "name_ru": "\u0413\u043e\u0440\u043d\u044b\u0439 \u0441\u0435\u0440\u0435\u0431\u0440\u044f\u043d\u044b\u0439 \u0448\u0430\u043b\u0444\u0435\u0439", "name_en": "Mountain Silversage", "min_skill": 280, "quality": 1},
    {"item_id": 13466, "name_ru": "\u0427\u0443\u043c\u043e\u0446\u0432\u0435\u0442", "name_en": "Plaguebloom", "min_skill": 285, "quality": 1},
    {"item_id": 13467, "name_ru": "\u041b\u0435\u0434\u044f\u043d\u043e\u0439 \u0437\u0435\u0432", "name_en": "Icecap", "min_skill": 290, "quality": 1},
    {"item_id": 13468, "name_ru": "\u0427\u0435\u0440\u043d\u044b\u0439 \u043b\u043e\u0442\u043e\u0441", "name_en": "Black Lotus", "min_skill": 300, "quality": 2},
    {"item_id": 8153, "name_ru": "\u0414\u0438\u043a\u0430\u044f \u043b\u043e\u0437\u0430", "name_en": "Wildvine", "min_skill": 0, "quality": 1},
    {"item_id": 19726, "name_ru": "\u041a\u0440\u043e\u0432\u0430\u0432\u0430\u044f \u043b\u043e\u0437\u0430", "name_en": "Bloodvine", "min_skill": 300, "quality": 2},
]

reagents = [
    {"item_id": 3371, "name_ru": "\u041f\u0443\u0441\u0442\u0430\u044f \u043a\u043e\u043b\u0431\u0430", "name_en": "Empty Vial", "quality": 1, "vendor_buy_copper": 40, "vendor_sell_copper": 10},
    {"item_id": 3372, "name_ru": "\u041e\u0441\u0432\u0438\u043d\u0446\u043e\u0432\u0430\u043d\u043d\u0430\u044f \u043a\u043e\u043b\u0431\u0430", "name_en": "Leaded Vial", "quality": 1, "vendor_buy_copper": 320, "vendor_sell_copper": 80},
    {"item_id": 8925, "name_ru": "\u0425\u0440\u0443\u0441\u0442\u0430\u043b\u044c\u043d\u0430\u044f \u043a\u043e\u043b\u0431\u0430", "name_en": "Crystal Vial", "quality": 1, "vendor_buy_copper": 4000, "vendor_sell_copper": 1000},
    {"item_id": 6358, "name_ru": "\u0427\u0435\u0440\u043d\u043e\u0440\u043e\u0442\u0438\u043a", "name_en": "Oily Blackmouth", "quality": 1, "vendor_buy_copper": 0, "vendor_sell_copper": 25},
    {"item_id": 6359, "name_ru": "\u041e\u0433\u043d\u0435\u043f\u0435\u0440\u044b\u0439 \u043b\u0443\u0444\u0430\u0440\u044c", "name_en": "Firefin Snapper", "quality": 1, "vendor_buy_copper": 0, "vendor_sell_copper": 50},
    {"item_id": 13422, "name_ru": "\u041a\u0430\u043c\u0435\u043d\u043d\u043e\u0447\u0435\u0448\u0443\u0439\u0447\u0430\u0442\u044b\u0439 \u0443\u0433\u043e\u0440\u044c", "name_en": "Stonescale Eel", "quality": 1, "vendor_buy_copper": 0, "vendor_sell_copper": 125},
    {"item_id": 1708, "name_ru": "\u0421\u043b\u0430\u0434\u043a\u0438\u0439 \u043d\u0435\u043a\u0442\u0430\u0440", "name_en": "Sweet Nectar", "quality": 1, "vendor_buy_copper": 200, "vendor_sell_copper": 50},
    {"item_id": 159, "name_ru": "\u041e\u0441\u0432\u0435\u0436\u0430\u044e\u0449\u0438\u0439 \u043d\u0430\u043f\u0438\u0442\u043e\u043a", "name_en": "Refreshing Spring Water", "quality": 1, "vendor_buy_copper": 25, "vendor_sell_copper": 6},
    {"item_id": 12808, "name_ru": "\u0421\u0443\u0431\u0441\u0442\u0430\u043d\u0446\u0438\u044f \u041d\u0435\u0436\u0438\u0442\u0438", "name_en": "Essence of Undeath", "quality": 2, "vendor_buy_copper": 0, "vendor_sell_copper": 5000},
    {"item_id": 7068, "name_ru": "\u0421\u0442\u0438\u0445\u0438\u0439\u043d\u044b\u0439 \u043e\u0433\u043e\u043d\u044c", "name_en": "Elemental Fire", "quality": 2, "vendor_buy_copper": 0, "vendor_sell_copper": 2500},
    {"item_id": 7078, "name_ru": "\u0421\u0442\u0438\u0445\u0438\u0439\u043d\u0430\u044f \u0437\u0435\u043c\u043b\u044f", "name_en": "Elemental Earth", "quality": 2, "vendor_buy_copper": 0, "vendor_sell_copper": 2500},
    {"item_id": 7080, "name_ru": "\u0421\u0442\u0438\u0445\u0438\u0439\u043d\u0430\u044f \u0432\u043e\u0434\u0430", "name_en": "Elemental Water", "quality": 2, "vendor_buy_copper": 0, "vendor_sell_copper": 2500},
    {"item_id": 7082, "name_ru": "\u0421\u0442\u0438\u0445\u0438\u0439\u043d\u044b\u0439 \u0432\u043e\u0437\u0434\u0443\u0445", "name_en": "Elemental Air", "quality": 2, "vendor_buy_copper": 0, "vendor_sell_copper": 2500},
    {"item_id": 12803, "name_ru": "\u0421\u0443\u0431\u0441\u0442\u0430\u043d\u0446\u0438\u044f \u0416\u0438\u0437\u043d\u0438", "name_en": "Essence of Life", "quality": 2, "vendor_buy_copper": 0, "vendor_sell_copper": 5000},
    {"item_id": 12360, "name_ru": "\u0410\u0440\u043a\u0430\u043d\u0438\u0442\u043e\u0432\u044b\u0439 \u0441\u043b\u0438\u0442\u043e\u043a", "name_en": "Arcanite Bar", "quality": 2, "vendor_buy_copper": 0, "vendor_sell_copper": 50000},
    {"item_id": 12359, "name_ru": "\u0422\u043e\u0440\u0438\u0435\u0432\u044b\u0439 \u0441\u043b\u0438\u0442\u043e\u043a", "name_en": "Thorium Bar", "quality": 1, "vendor_buy_copper": 0, "vendor_sell_copper": 2800},
    {"item_id": 12363, "name_ru": "\u0427\u0430\u0440\u043e\u0434\u0435\u0439\u043d\u044b\u0439 \u043a\u0440\u0438\u0441\u0442\u0430\u043b\u043b", "name_en": "Arcane Crystal", "quality": 2, "vendor_buy_copper": 0, "vendor_sell_copper": 33333},
    {"item_id": 3575, "name_ru": "\u0416\u0435\u043b\u0435\u0437\u043d\u044b\u0439 \u0441\u043b\u0438\u0442\u043e\u043a", "name_en": "Iron Bar", "quality": 1, "vendor_buy_copper": 0, "vendor_sell_copper": 200},
    {"item_id": 3577, "name_ru": "\u0417\u043e\u043b\u043e\u0442\u043e\u0439 \u0441\u043b\u0438\u0442\u043e\u043a", "name_en": "Gold Bar", "quality": 1, "vendor_buy_copper": 0, "vendor_sell_copper": 900},
    {"item_id": 3860, "name_ru": "\u041c\u0438\u0444\u0440\u0438\u043b\u043e\u0432\u044b\u0439 \u0441\u043b\u0438\u0442\u043e\u043a", "name_en": "Mithril Bar", "quality": 1, "vendor_buy_copper": 0, "vendor_sell_copper": 800},
    {"item_id": 6037, "name_ru": "\u0421\u043b\u0438\u0442\u043e\u043a \u0438\u0441\u0442\u0438\u043d\u043d\u043e\u0433\u043e \u0441\u0435\u0440\u0435\u0431\u0440\u0430", "name_en": "Truesilver Bar", "quality": 1, "vendor_buy_copper": 0, "vendor_sell_copper": 1500},
]

recipes = [
    {
        "recipe_id": 2330, "recipe_item_id": None, "name_ru": "\u041a\u0440\u043e\u0448\u0435\u0447\u043d\u043e\u0435 \u0437\u0435\u043b\u044c\u0435 \u0438\u0441\u0446\u0435\u043b\u0435\u043d\u0438\u044f", "name_en": "Minor Healing Potion",
        "min_skill": 1, "crafted_item_id": 118, "yield": 1.0,
        "source": "trainer", "source_details_ru": "\u0423\u0447\u0438\u0442\u0435\u043b\u044c \u0430\u043b\u0445\u0438\u043c\u0438\u0438 (\u0432\u0441\u0435 \u0441\u0442\u043e\u043b\u0438\u0446\u044b)", "source_details_en": "Alchemy Trainer (All Capital Cities)",
        "reagents": [{"item_id": 2447, "count": 1}, {"item_id": 765, "count": 1}, {"item_id": 3371, "count": 1}]
    },
    {
        "recipe_id": 2329, "recipe_item_id": None, "name_ru": "\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u043b\u044c\u0432\u0438\u043d\u043e\u0439 \u0441\u0438\u043b\u044b", "name_en": "Elixir of Lion's Strength",
        "min_skill": 1, "crafted_item_id": 2454, "yield": 1.0,
        "source": "trainer", "source_details_ru": "\u0423\u0447\u0438\u0442\u0435\u043b\u044c \u0430\u043b\u0445\u0438\u043c\u0438\u0438", "source_details_en": "Alchemy Trainer",
        "reagents": [{"item_id": 2449, "count": 1}, {"item_id": 765, "count": 1}, {"item_id": 3371, "count": 1}]
    },
    {
        "recipe_id": 2331, "recipe_item_id": None, "name_ru": "\u041a\u0440\u043e\u0448\u0435\u0447\u043d\u043e\u0435 \u0437\u0435\u043b\u044c\u0435 \u043c\u0430\u043d\u044b", "name_en": "Minor Mana Potion",
        "min_skill": 25, "crafted_item_id": 2455, "yield": 1.0,
        "source": "trainer", "source_details_ru": "\u0423\u0447\u0438\u0442\u0435\u043b\u044c \u0430\u043b\u0445\u0438\u043c\u0438\u0438", "source_details_en": "Alchemy Trainer",
        "reagents": [{"item_id": 785, "count": 1}, {"item_id": 765, "count": 1}, {"item_id": 3371, "count": 1}]
    },
    {
        "recipe_id": 2332, "recipe_item_id": None, "name_ru": "\u041a\u0440\u043e\u0448\u0435\u0447\u043d\u043e\u0435 \u0437\u0435\u043b\u044c\u0435 \u043e\u043c\u043e\u043b\u043e\u0436\u0435\u043d\u0438\u044f", "name_en": "Minor Rejuvenation Potion",
        "min_skill": 40, "crafted_item_id": 2456, "yield": 1.0,
        "source": "trainer", "source_details_ru": "\u0423\u0447\u0438\u0442\u0435\u043b\u044c \u0430\u043b\u0445\u0438\u043c\u0438\u0438", "source_details_en": "Alchemy Trainer",
        "reagents": [{"item_id": 785, "count": 2}, {"item_id": 2447, "count": 1}, {"item_id": 3371, "count": 1}]
    },
    {
        "recipe_id": 7183, "recipe_item_id": None, "name_ru": "\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u043c\u0430\u043b\u043e\u0439 \u0437\u0430\u0449\u0438\u0442\u044b", "name_en": "Elixir of Minor Defense",
        "min_skill": 1, "crafted_item_id": 5997, "yield": 1.0,
        "source": "trainer", "source_details_ru": "\u0423\u0447\u0438\u0442\u0435\u043b\u044c \u0430\u043b\u0445\u0438\u043c\u0438\u0438", "source_details_en": "Alchemy Trainer",
        "reagents": [{"item_id": 765, "count": 2}, {"item_id": 3371, "count": 1}]
    },
    {
        "recipe_id": 2334, "recipe_item_id": None, "name_ru": "\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u043c\u0430\u043b\u043e\u0439 \u043b\u043e\u0432\u043a\u043e\u0441\u0442\u0438", "name_en": "Elixir of Minor Agility",
        "min_skill": 50, "crafted_item_id": 2457, "yield": 1.0,
        "source": "trainer", "source_details_ru": "\u0423\u0447\u0438\u0442\u0435\u043b\u044c \u0430\u043b\u0445\u0438\u043c\u0438\u0438", "source_details_en": "Alchemy Trainer",
        "reagents": [{"item_id": 2452, "count": 1}, {"item_id": 765, "count": 1}, {"item_id": 3371, "count": 1}]
    },
    {
        "recipe_id": 2337, "recipe_item_id": None, "name_ru": "\u041c\u0430\u043b\u043e\u0435 \u0437\u0435\u043b\u044c\u0435 \u0438\u0441\u0446\u0435\u043b\u0435\u043d\u0438\u044f", "name_en": "Lesser Healing Potion",
        "min_skill": 55, "crafted_item_id": 858, "yield": 1.0,
        "source": "trainer", "source_details_ru": "\u0423\u0447\u0438\u0442\u0435\u043b\u044c \u0430\u043b\u0445\u0438\u043c\u0438\u0438", "source_details_en": "Alchemy Trainer",
        "reagents": [{"item_id": 118, "count": 1}, {"item_id": 2450, "count": 1}]
    },
    {
        "recipe_id": 3170, "recipe_item_id": 2555, "name_ru": "\u0417\u0435\u043b\u044c\u0435 \u0441\u0442\u0440\u0435\u043c\u0438\u0442\u0435\u043b\u044c\u043d\u043e\u0441\u0442\u0438", "name_en": "Swiftness Potion",
        "min_skill": 60, "crafted_item_id": 2459, "yield": 1.0,
        "source": "drop", "source_details_ru": "\u041c\u0438\u0440\u043e\u0432\u043e\u0439 \u0434\u0440\u043e\u043f (10-20 \u0443\u0440. / \u0410\u0443\u043a\u0446\u0438\u043e\u043d)", "source_details_en": "World Drop (Lvl 10-20 / AH)",
        "reagents": [{"item_id": 2452, "count": 1}, {"item_id": 2450, "count": 1}, {"item_id": 3371, "count": 1}]
    },
    {
        "recipe_id": 7841, "recipe_item_id": 6375, "name_ru": "\u041c\u0430\u0441\u043b\u043e \u0447\u0435\u0440\u043d\u043e\u0440\u043e\u0442\u0438\u043a\u0430", "name_en": "Blackmouth Oil",
        "min_skill": 80, "crafted_item_id": 6370, "yield": 1.0,
        "source": "trainer", "source_details_ru": "\u0423\u0447\u0438\u0442\u0435\u043b\u044c \u0430\u043b\u0445\u0438\u043c\u0438\u0438", "source_details_en": "Alchemy Trainer",
        "reagents": [{"item_id": 6358, "count": 2}, {"item_id": 3371, "count": 1}]
    },
    {
        "recipe_id": 7845, "recipe_item_id": 6377, "name_ru": "\u0417\u0435\u043b\u044c\u0435 \u0431\u044b\u0441\u0442\u0440\u043e\u0433\u043e \u043f\u043b\u0430\u0432\u0430\u043d\u0438\u044f", "name_en": "Swim Speed Potion",
        "min_skill": 100, "crafted_item_id": 6372, "yield": 1.0,
        "source": "trainer", "source_details_ru": "\u0423\u0447\u0438\u0442\u0435\u043b\u044c \u0430\u043b\u0445\u0438\u043c\u0438\u0438", "source_details_en": "Alchemy Trainer",
        "reagents": [{"item_id": 6370, "count": 1}, {"item_id": 2452, "count": 1}, {"item_id": 3371, "count": 1}]
    },
    {
        "recipe_id": 2335, "recipe_item_id": None, "name_ru": "\u0417\u0435\u043b\u044c\u0435 \u0438\u0441\u0446\u0435\u043b\u0435\u043d\u0438\u044f", "name_en": "Healing Potion",
        "min_skill": 110, "crafted_item_id": 929, "yield": 1.0,
        "source": "trainer", "source_details_ru": "\u0423\u0447\u0438\u0442\u0435\u043b\u044c \u0430\u043b\u0445\u0438\u043c\u0438\u0438", "source_details_en": "Alchemy Trainer",
        "reagents": [{"item_id": 2453, "count": 1}, {"item_id": 2450, "count": 1}, {"item_id": 3372, "count": 1}]
    },
    {
        "recipe_id": 3173, "recipe_item_id": None, "name_ru": "\u041c\u0430\u043b\u043e\u0435 \u0437\u0435\u043b\u044c\u0435 \u043c\u0430\u043d\u044b", "name_en": "Lesser Mana Potion",
        "min_skill": 120, "crafted_item_id": 3385, "yield": 1.0,
        "source": "trainer", "source_details_ru": "\u0423\u0447\u0438\u0442\u0435\u043b\u044c \u0430\u043b\u0445\u0438\u043c\u0438\u0438", "source_details_en": "Alchemy Trainer",
        "reagents": [{"item_id": 785, "count": 1}, {"item_id": 3820, "count": 1}, {"item_id": 3371, "count": 1}]
    },
    {
        "recipe_id": 3176, "recipe_item_id": 5640, "name_ru": "\u0417\u0435\u043b\u044c\u0435 \u0441\u0432\u043e\u0431\u043e\u0434\u044b \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u0439", "name_en": "Free Action Potion",
        "min_skill": 150, "crafted_item_id": 5634, "yield": 1.0,
        "source": "vendor", "source_details_ru": "\u0412\u0435\u043d\u0434\u043e\u0440: \u041a\u043e\u0440\u0431 (\u042f\u0441\u0435\u043d\u0435\u0432\u044b\u0439 \u043b\u0435\u0441) / \u041a\u0432\u0430\u043d\u0438\u0438 (\u0422\u0435\u043c\u043d\u044b\u0435 \u0431\u0435\u0440\u0435\u0433\u0430)", "source_details_en": "Vendor: Kor'b (Ashenvale) / Kwanii (Darkshore)",
        "reagents": [{"item_id": 6370, "count": 2}, {"item_id": 3820, "count": 1}, {"item_id": 3372, "count": 1}]
    },
    {
        "recipe_id": 7842, "recipe_item_id": 6376, "name_ru": "\u041e\u0433\u043d\u0435\u0432\u043e\u0435 \u043c\u0430\u0441\u043b\u043e", "name_en": "Fire Oil",
        "min_skill": 130, "crafted_item_id": 6371, "yield": 1.0,
        "source": "trainer", "source_details_ru": "\u0423\u0447\u0438\u0442\u0435\u043b\u044c \u0430\u043b\u0445\u0438\u043c\u0438\u0438", "source_details_en": "Alchemy Trainer",
        "reagents": [{"item_id": 6359, "count": 2}, {"item_id": 3371, "count": 1}]
    },
    {
        "recipe_id": 7844, "recipe_item_id": None, "name_ru": "\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u0441\u0438\u043b\u044b \u043e\u0433\u043d\u044f", "name_en": "Elixir of Firepower",
        "min_skill": 140, "crafted_item_id": 6373, "yield": 1.0,
        "source": "trainer", "source_details_ru": "\u0423\u0447\u0438\u0442\u0435\u043b\u044c \u0430\u043b\u0445\u0438\u043c\u0438\u0438", "source_details_en": "Alchemy Trainer",
        "reagents": [{"item_id": 6371, "count": 2}, {"item_id": 3356, "count": 1}, {"item_id": 3372, "count": 1}]
    },
    {
        "recipe_id": 2333, "recipe_item_id": None, "name_ru": "\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u043d\u0435\u0437\u043d\u0430\u0447\u0438\u0442\u0435\u043b\u044c\u043d\u043e\u0439 \u043b\u043e\u0432\u043a\u043e\u0441\u0442\u0438", "name_en": "Elixir of Lesser Agility",
        "min_skill": 140, "crafted_item_id": 3390, "yield": 1.0,
        "source": "trainer", "source_details_ru": "\u0423\u0447\u0438\u0442\u0435\u043b\u044c \u0430\u043b\u0445\u0438\u043c\u0438\u0438", "source_details_en": "Alchemy Trainer",
        "reagents": [{"item_id": 3355, "count": 1}, {"item_id": 2452, "count": 1}, {"item_id": 3372, "count": 1}]
    },
    {
        "recipe_id": 3175, "recipe_item_id": None, "name_ru": "\u0417\u0435\u043b\u044c\u0435 \u0437\u0430\u0449\u0438\u0442\u044b \u043e\u0442 \u043e\u0433\u043d\u044f", "name_en": "Fire Protection Potion",
        "min_skill": 165, "crafted_item_id": 6048, "yield": 1.0,
        "source": "vendor", "source_details_ru": "\u0412\u0435\u043d\u0434\u043e\u0440: \u041d\u0430\u0440\u043c\u0430 (\u0413\u0440\u043e\u043c\u043e\u0432\u043e\u0439 \u0423\u0442\u0435\u0441) / \u041b\u0443\u043b\u0443 (\u0428\u0442\u043e\u0440\u043c\u0433\u0440\u0430\u0434)", "source_details_en": "Vendor: Narma / Loolrun",
        "reagents": [{"item_id": 3355, "count": 1}, {"item_id": 3356, "count": 1}, {"item_id": 3372, "count": 1}]
    },
    {
        "recipe_id": 3178, "recipe_item_id": None, "name_ru": "\u0411\u043e\u043b\u044c\u0448\u043e\u0435 \u0437\u0435\u043b\u044c\u0435 \u0438\u0441\u0446\u0435\u043b\u0435\u043d\u0438\u044f", "name_en": "Greater Healing Potion",
        "min_skill": 155, "crafted_item_id": 1710, "yield": 1.0,
        "source": "trainer", "source_details_ru": "\u0423\u0447\u0438\u0442\u0435\u043b\u044c \u0430\u043b\u0445\u0438\u043c\u0438\u0438", "source_details_en": "Alchemy Trainer",
        "reagents": [{"item_id": 3357, "count": 1}, {"item_id": 3356, "count": 1}, {"item_id": 3372, "count": 1}]
    },
    {
        "recipe_id": 3174, "recipe_item_id": None, "name_ru": "\u0417\u0435\u043b\u044c\u0435 \u043c\u0430\u043d\u044b", "name_en": "Mana Potion",
        "min_skill": 160, "crafted_item_id": 3827, "yield": 1.0,
        "source": "trainer", "source_details_ru": "\u0423\u0447\u0438\u0442\u0435\u043b\u044c \u0430\u043b\u0445\u0438\u043c\u0438\u0438", "source_details_en": "Alchemy Trainer",
        "reagents": [{"item_id": 3818, "count": 1}, {"item_id": 3820, "count": 1}, {"item_id": 3372, "count": 1}]
    },
    {
        "recipe_id": 3180, "recipe_item_id": None, "name_ru": "\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u0441\u0442\u043e\u0439\u043a\u043e\u0441\u0442\u0438", "name_en": "Elixir of Fortitude",
        "min_skill": 175, "crafted_item_id": 3825, "yield": 1.0,
        "source": "trainer", "source_details_ru": "\u0423\u0447\u0438\u0442\u0435\u043b\u044c \u0430\u043b\u0445\u0438\u043c\u0438\u0438", "source_details_en": "Alchemy Trainer",
        "reagents": [{"item_id": 3355, "count": 1}, {"item_id": 3821, "count": 1}, {"item_id": 3372, "count": 1}]
    },
    {
        "recipe_id": 3172, "recipe_item_id": None, "name_ru": "\u041c\u0430\u0441\u043b\u043e \u0442\u044c\u043c\u044b", "name_en": "Shadow Oil",
        "min_skill": 165, "crafted_item_id": 3824, "yield": 1.0,
        "source": "vendor", "source_details_ru": "\u0412\u0435\u043d\u0434\u043e\u0440: \u0411\u0440\u043e'\u043a\u0438\u043d (\u041f\u0440\u0435\u0434\u0433\u043e\u0440\u044c\u044f \u0425\u0438\u043b\u0441\u0431\u0440\u0430\u0434\u0430) / \u0411\u043b\u0438\u0437\u0442\u0438\u043a (\u0421\u0443\u043c\u0435\u0440\u0435\u0447\u043d\u044b\u0439 \u043b\u0435\u0441)", "source_details_en": "Vendor: Bro'kin / Bliztik",
        "reagents": [{"item_id": 3818, "count": 4}, {"item_id": 3369, "count": 4}, {"item_id": 3372, "count": 1}]
    },
    {
        "recipe_id": 3448, "recipe_item_id": 3831, "name_ru": "\u041c\u0430\u0441\u043b\u043e \u043b\u044c\u0434\u0430", "name_en": "Frost Oil",
        "min_skill": 200, "crafted_item_id": 3829, "yield": 1.0,
        "source": "vendor", "source_details_ru": "\u0412\u0435\u043d\u0434\u043e\u0440: \u0411\u0440\u043e'\u043a\u0438\u043d (\u0410\u043b\u044c\u0442\u0435\u0440\u0430\u043a\u0441\u043a\u0438\u0435 \u0433\u043e\u0440\u044b)", "source_details_en": "Vendor: Bro'kin (Alterac)",
        "reagents": [{"item_id": 3358, "count": 4}, {"item_id": 3819, "count": 2}, {"item_id": 3372, "count": 1}]
    },
    {
        "recipe_id": 11448, "recipe_item_id": 9260, "name_ru": "\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u0432\u0435\u043b\u0438\u043a\u0430\u043d\u043e\u0432", "name_en": "Elixir of Giants",
        "min_skill": 245, "crafted_item_id": 9206, "yield": 1.0,
        "source": "drop", "source_details_ru": "\u041c\u0438\u0440\u043e\u0432\u043e\u0439 \u0434\u0440\u043e\u043f (45-50 \u0443\u0440. / \u0410\u0443\u043a\u0446\u0438\u043e\u043d)", "source_details_en": "World Drop (AH)",
        "reagents": [{"item_id": 4625, "count": 1}, {"item_id": 8846, "count": 1}, {"item_id": 3372, "count": 1}]
    },
    {
        "recipe_id": 11453, "recipe_item_id": None, "name_ru": "\u0411\u043e\u043b\u044c\u0448\u043e\u0435 \u0437\u0435\u043b\u044c\u0435 \u043c\u0430\u043d\u044b", "name_en": "Greater Mana Potion",
        "min_skill": 205, "crafted_item_id": 6149, "yield": 1.0,
        "source": "trainer", "source_details_ru": "\u0423\u0447\u0438\u0442\u0435\u043b\u044c \u0430\u043b\u0445\u0438\u043c\u0438\u0438", "source_details_en": "Alchemy Trainer",
        "reagents": [{"item_id": 3358, "count": 1}, {"item_id": 3821, "count": 1}, {"item_id": 3372, "count": 1}]
    },
    {
        "recipe_id": 11457, "recipe_item_id": None, "name_ru": "\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u043b\u043e\u0432\u043a\u043e\u0441\u0442\u0438", "name_en": "Elixir of Agility",
        "min_skill": 185, "crafted_item_id": 8949, "yield": 1.0,
        "source": "trainer", "source_details_ru": "\u0423\u0447\u0438\u0442\u0435\u043b\u044c \u0430\u043b\u0445\u0438\u043c\u0438\u0438", "source_details_en": "Alchemy Trainer",
        "reagents": [{"item_id": 3818, "count": 1}, {"item_id": 3821, "count": 1}, {"item_id": 3372, "count": 1}]
    },
    {
        "recipe_id": 11464, "recipe_item_id": None, "name_ru": "\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u0432\u0435\u043b\u0438\u043a\u043e\u0439 \u0437\u0430\u0449\u0438\u0442\u044b", "name_en": "Elixir of Greater Defense",
        "min_skill": 195, "crafted_item_id": 8950, "yield": 1.0,
        "source": "trainer", "source_details_ru": "\u0423\u0447\u0438\u0442\u0435\u043b\u044c \u0430\u043b\u0445\u0438\u043c\u0438\u0438", "source_details_en": "Alchemy Trainer",
        "reagents": [{"item_id": 3355, "count": 1}, {"item_id": 3821, "count": 1}, {"item_id": 3372, "count": 1}]
    },
    {
        "recipe_id": 11467, "recipe_item_id": None, "name_ru": "\u041f\u0440\u0435\u0432\u043e\u0441\u0445\u043e\u0434\u043d\u043e\u0435 \u0437\u0435\u043b\u044c\u0435 \u0438\u0441\u0446\u0435\u043b\u0435\u043d\u0438\u044f", "name_en": "Superior Healing Potion",
        "min_skill": 215, "crafted_item_id": 3928, "yield": 1.0,
        "source": "trainer", "source_details_ru": "\u0423\u0447\u0438\u0442\u0435\u043b\u044c \u0430\u043b\u0445\u0438\u043c\u0438\u0438", "source_details_en": "Alchemy Trainer",
        "reagents": [{"item_id": 8838, "count": 1}, {"item_id": 3358, "count": 1}, {"item_id": 8925, "count": 1}]
    },
    {
        "recipe_id": 11468, "recipe_item_id": 9264, "name_ru": "\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u0441\u0438\u043b\u044b \u0442\u044c\u043c\u044b", "name_en": "Elixir of Shadow Power",
        "min_skill": 250, "crafted_item_id": 9264, "yield": 1.0,
        "source": "vendor", "source_details_ru": "\u0412\u0435\u043d\u0434\u043e\u0440: \u0410\u043b\u0445\u0438\u043c\u0438\u043a \u041f\u0435\u0441\u0442\u044b\u0448 (\u041f\u043e\u0434\u0433\u043e\u0440\u043e\u0434)", "source_details_en": "Vendor: Algernon (Undercity)",
        "reagents": [{"item_id": 8845, "count": 3}, {"item_id": 8925, "count": 1}]
    },
    {
        "recipe_id": 11472, "recipe_item_id": None, "name_ru": "\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u0432\u0435\u043b\u0438\u043a\u043e\u0433\u043e \u0438\u043d\u0442\u0435\u043b\u043b\u0435\u043a\u0442\u0430", "name_en": "Elixir of Greater Intellect",
        "min_skill": 235, "crafted_item_id": 9179, "yield": 1.0,
        "source": "trainer", "source_details_ru": "\u0423\u0447\u0438\u0442\u0435\u043b\u044c \u0430\u043b\u0445\u0438\u043c\u0438\u0438", "source_details_en": "Alchemy Trainer",
        "reagents": [{"item_id": 8839, "count": 1}, {"item_id": 3358, "count": 1}, {"item_id": 8925, "count": 1}]
    },
    {
        "recipe_id": 11478, "recipe_item_id": 9187, "name_ru": "\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u0432\u0435\u043b\u0438\u043a\u043e\u0439 \u043b\u043e\u0432\u043a\u043e\u0441\u0442\u0438", "name_en": "Elixir of Greater Agility",
        "min_skill": 240, "crafted_item_id": 9187, "yield": 1.0,
        "source": "trainer", "source_details_ru": "\u0423\u0447\u0438\u0442\u0435\u043b\u044c \u0430\u043b\u0445\u0438\u043c\u0438\u0438", "source_details_en": "Alchemy Trainer",
        "reagents": [{"item_id": 8838, "count": 1}, {"item_id": 3821, "count": 1}, {"item_id": 8925, "count": 1}]
    },
    {
        "recipe_id": 17551, "recipe_item_id": 13476, "name_ru": "\u041f\u0440\u0435\u0432\u043e\u0441\u0445\u043e\u0434\u043d\u043e\u0435 \u0437\u0435\u043b\u044c\u0435 \u043c\u0430\u043d\u044b", "name_en": "Superior Mana Potion",
        "min_skill": 260, "crafted_item_id": 13443, "yield": 1.0,
        "source": "vendor", "source_details_ru": "\u0412\u0435\u043d\u0434\u043e\u0440: \u0410\u043b\u0445\u0438\u043c\u0438\u043a \u041f\u0435\u0441\u0442\u044b\u0448 (\u041f\u043e\u0434\u0433\u043e\u0440\u043e\u0434) / \u0423\u043b\u044c\u0441\u043e\u0440\u043d (\u0414\u0430\u0440\u043d\u0430\u0441)", "source_details_en": "Vendor: Algernon / Ulthir",
        "reagents": [{"item_id": 8838, "count": 2}, {"item_id": 8839, "count": 2}, {"item_id": 8925, "count": 1}]
    },
    {
        "recipe_id": 17552, "recipe_item_id": 13477, "name_ru": "\u0421\u0438\u043b\u044c\u043d\u0435\u0439\u0448\u0435\u0435 \u0437\u0435\u043b\u044c\u0435 \u0438\u0441\u0446\u0435\u043b\u0435\u043d\u0438\u044f", "name_en": "Major Healing Potion",
        "min_skill": 275, "crafted_item_id": 13446, "yield": 1.0,
        "source": "vendor", "source_details_ru": "\u0412\u0435\u043d\u0434\u043e\u0440: \u042d\u0432\u0430\u043d\u0438 \u0425\u0435\u0434\u0436\u043f\u0430\u0442 (\u041a\u0440\u0443\u0433\u043e\u0432\u0437\u043e\u0440, \u0417\u0438\u043c\u043d\u0438\u0435 \u041a\u043b\u044e\u0447\u0438)", "source_details_en": "Vendor: Evie Whirlbrew (Winterspring)",
        "reagents": [{"item_id": 13464, "count": 2}, {"item_id": 13465, "count": 1}, {"item_id": 8925, "count": 1}]
    },
    {
        "recipe_id": 17553, "recipe_item_id": 13478, "name_ru": "\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u043f\u0440\u0435\u0432\u043e\u0441\u0445\u043e\u0434\u043d\u043e\u0439 \u0437\u0430\u0449\u0438\u0442\u044b", "name_en": "Elixir of Superior Defense",
        "min_skill": 265, "crafted_item_id": 13445, "yield": 1.0,
        "source": "vendor", "source_details_ru": "\u0412\u0435\u043d\u0434\u043e\u0440: \u0421\u0443\u043b\u0438 (\u0413\u0440\u043e\u043c\u043e\u0432\u043e\u0439 \u0423\u0442\u0435\u0441) / \u041a\u043e\u0440\u0432\u0438\u043d (\u0428\u0442\u043e\u0440\u043c\u0433\u0440\u0430\u0434)", "source_details_en": "Vendor: Soolie / Korvin",
        "reagents": [{"item_id": 13423, "count": 2}, {"item_id": 8838, "count": 1}, {"item_id": 8925, "count": 1}]
    },
    {
        "recipe_id": 17554, "recipe_item_id": 13479, "name_ru": "\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u043c\u0443\u0434\u0440\u0435\u0446\u043e\u0432", "name_en": "Elixir of the Sages",
        "min_skill": 270, "crafted_item_id": 13447, "yield": 1.0,
        "source": "drop", "source_details_ru": "\u0414\u0440\u043e\u043f: \u0412\u043e\u0441\u0442\u043e\u0447\u043d\u044b\u0435 \u0427\u0443\u043c\u043d\u044b\u0435 \u0437\u0435\u043c\u043b\u0438 / \u0410\u0443\u043a\u0446\u0438\u043e\u043d", "source_details_en": "Drop: Scarlet Spellbinder (EPL / AH)",
        "reagents": [{"item_id": 13463, "count": 1}, {"item_id": 13466, "count": 2}, {"item_id": 8925, "count": 1}]
    },
    {
        "recipe_id": 17570, "recipe_item_id": 13490, "name_ru": "\u0421\u0438\u043b\u044c\u043d\u043e\u0435 \u0437\u0435\u043b\u044c\u0435 \u0437\u0430\u0449\u0438\u0442\u044b \u043e\u0442 \u043e\u0433\u043d\u044f", "name_en": "Greater Fire Protection Potion",
        "min_skill": 290, "crafted_item_id": 13457, "yield": 1.0,
        "source": "drop", "source_details_ru": "\u0414\u0440\u043e\u043f: \u041f\u0438\u0440\u043e\u043c\u0430\u043d\u0442 \u0417\u0435\u0440\u043d\u043e \u0421\u043a\u0432\u0435\u0440\u043d\u044b (\u041b\u0420\u0421 / LBRS)", "source_details_en": "Drop: Firebrand Pyromancer (LBRS)",
        "reagents": [{"item_id": 7068, "count": 1}, {"item_id": 13463, "count": 1}, {"item_id": 8925, "count": 1}]
    },
    {
        "recipe_id": 17571, "recipe_item_id": 13491, "name_ru": "\u0421\u0438\u043b\u044c\u043d\u043e\u0435 \u0437\u0435\u043b\u044c\u0435 \u0437\u0430\u0449\u0438\u0442\u044b \u043e\u0442 \u043c\u0430\u0433\u0438\u0438 \u043b\u044c\u0434\u0430", "name_en": "Greater Frost Protection Potion",
        "min_skill": 290, "crafted_item_id": 13456, "yield": 1.0,
        "source": "drop", "source_details_ru": "\u0414\u0440\u043e\u043f: \u0412\u0435\u043b\u0438\u043a\u0430\u043d\u044b \u0417\u0438\u043c\u043d\u0438\u0445 \u041a\u043b\u044e\u0447\u0435\u0439 / \u0410\u0443\u043a\u0446\u0438\u043e\u043d", "source_details_en": "Drop: Frostmaul Giant (Winterspring)",
        "reagents": [{"item_id": 7080, "count": 1}, {"item_id": 13463, "count": 1}, {"item_id": 8925, "count": 1}]
    },
    {
        "recipe_id": 17572, "recipe_item_id": 13492, "name_ru": "\u0421\u0438\u043b\u044c\u043d\u043e\u0435 \u0437\u0435\u043b\u044c\u0435 \u0437\u0430\u0449\u0438\u0442\u044b \u043e\u0442 \u0441\u0438\u043b \u043f\u0440\u0438\u0440\u043e\u0434\u044b", "name_en": "Greater Nature Protection Potion",
        "min_skill": 290, "crafted_item_id": 13458, "yield": 1.0,
        "source": "drop", "source_details_ru": "\u0414\u0440\u043e\u043f: \u041c\u043e\u0431\u044b \u0432 \u0417\u0430\u043f\u0430\u0434\u043d\u044b\u0445 \u0427\u0443\u043c\u043d\u044b\u0445 \u0437\u0435\u043c\u043b\u044f\u0445 / \u0410\u0443\u043a\u0446\u0438\u043e\u043d", "source_details_en": "Drop: Weeping Horror (WPL / AH)",
        "reagents": [{"item_id": 7078, "count": 1}, {"item_id": 13463, "count": 1}, {"item_id": 8925, "count": 1}]
    },
    {
        "recipe_id": 17573, "recipe_item_id": 13493, "name_ru": "\u0421\u0438\u043b\u044c\u043d\u043e\u0435 \u0437\u0435\u043b\u044c\u0435 \u0437\u0430\u0449\u0438\u0442\u044b \u043e\u0442 \u0442\u0435\u043c\u043d\u043e\u0439 \u043c\u0430\u0433\u0438\u0438", "name_en": "Greater Shadow Protection Potion",
        "min_skill": 290, "crafted_item_id": 13459, "yield": 1.0,
        "source": "drop", "source_details_ru": "\u0414\u0440\u043e\u043f: \u0422\u0435\u043d\u0435\u043c\u0430\u0433\u0438 \u0432 \u0412\u043e\u0441\u0442\u043e\u0447\u043d\u044b\u0445 \u0427\u0443\u043c\u043d\u044b\u0445 \u0437\u0435\u043c\u043b\u044f\u0445 / \u0410\u0443\u043a\u0446\u0438\u043e\u043d", "source_details_en": "Drop: Shadowmage (EPL / AH)",
        "reagents": [{"item_id": 12808, "count": 1}, {"item_id": 13463, "count": 1}, {"item_id": 8925, "count": 1}]
    },
    {
        "recipe_id": 17574, "recipe_item_id": 13494, "name_ru": "\u0421\u0438\u043b\u044c\u043d\u043e\u0435 \u0437\u0435\u043b\u044c\u0435 \u0437\u0430\u0449\u0438\u0442\u044b \u043e\u0442 \u0442\u0430\u0439\u043d\u043e\u0439 \u043c\u0430\u0433\u0438\u0438", "name_en": "Greater Arcane Protection Potion",
        "min_skill": 290, "crafted_item_id": 13461, "yield": 1.0,
        "source": "drop", "source_details_ru": "\u0414\u0440\u043e\u043f: \u041a\u043e\u0431\u043e\u043b\u044c\u0434\u044b \u0438 \u0447\u0430\u0440\u043e\u0434\u0435\u0438 \u0432 \u0417\u0438\u043c\u043d\u0438\u0445 \u041a\u043b\u044e\u0447\u0430\u0445 / \u0410\u0443\u043a\u0446\u0438\u043e\u043d", "source_details_en": "Drop: Cobalt Mageweaver (Winterspring / AH)",
        "reagents": [{"item_id": 13463, "count": 1}, {"item_id": 13467, "count": 1}, {"item_id": 8925, "count": 1}]
    },
    {
        "recipe_id": 17577, "recipe_item_id": 13497, "name_ru": "\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u043c\u0430\u043d\u0433\u0443\u0441\u0442\u0430", "name_en": "Elixir of the Mongoose",
        "min_skill": 280, "crafted_item_id": 13452, "yield": 1.0,
        "source": "drop", "source_details_ru": "\u0414\u0440\u043e\u043f: \u0421\u0430\u0442\u0438\u0440\u044b \u0432 \u041e\u0441\u043a\u0432\u0435\u0440\u043d\u0435\u043d\u043d\u043e\u043c \u043b\u0435\u0441\u0443 / \u0410\u0443\u043a\u0446\u0438\u043e\u043d", "source_details_en": "Drop: Jadefire Satyrs (Felwood / AH)",
        "reagents": [{"item_id": 13465, "count": 2}, {"item_id": 13466, "count": 2}, {"item_id": 8925, "count": 1}]
    },
    {
        "recipe_id": 17578, "recipe_item_id": 13499, "name_ru": "\u0427\u0430\u0440\u043e\u0434\u0435\u0439\u0441\u043a\u0438\u0439 \u044d\u043b\u0438\u043a\u0441\u0438\u0440", "name_en": "Arcane Elixir",
        "min_skill": 275, "crafted_item_id": 9155, "yield": 1.0,
        "source": "trainer", "source_details_ru": "\u0423\u0447\u0438\u0442\u0435\u043b\u044c \u0430\u043b\u0445\u0438\u043c\u0438\u0438", "source_details_en": "Alchemy Trainer",
        "reagents": [{"item_id": 8839, "count": 1}, {"item_id": 13464, "count": 1}, {"item_id": 8925, "count": 1}]
    },
    {
        "recipe_id": 17579, "recipe_item_id": 13501, "name_ru": "\u041c\u043e\u0449\u043d\u044b\u0439 \u0447\u0430\u0440\u043e\u0434\u0435\u0439\u0441\u043a\u0438\u0439 \u044d\u043b\u0438\u043a\u0441\u0438\u0440", "name_en": "Greater Arcane Elixir",
        "min_skill": 285, "crafted_item_id": 13454, "yield": 1.0,
        "source": "drop", "source_details_ru": "\u0414\u0440\u043e\u043f: \u0417\u0430\u043a\u043b\u0438\u043d\u0430\u0442\u0435\u043b\u0438 \u0432 \u0421\u0442\u0440\u0430\u0442\u0445\u043e\u043b\u044c\u043c\u0435 / \u0410\u0443\u043a\u0446\u0438\u043e\u043d", "source_details_en": "Drop: Stratholme Sorcerers / AH",
        "reagents": [{"item_id": 13463, "count": 3}, {"item_id": 13465, "count": 1}, {"item_id": 8925, "count": 1}]
    },
    {
        "recipe_id": 17580, "recipe_item_id": 13502, "name_ru": "\u041e\u0447\u0438\u0449\u0430\u044e\u0449\u0435\u0435 \u0437\u0435\u043b\u044c\u0435", "name_en": "Purification Potion",
        "min_skill": 285, "crafted_item_id": 13462, "yield": 1.0,
        "source": "drop", "source_details_ru": "\u041c\u0438\u0440\u043e\u0432\u043e\u0439 \u0434\u0440\u043e\u043f (55-60 \u0443\u0440. / \u0410\u0443\u043a\u0446\u0438\u043e\u043d)", "source_details_en": "World Drop (55-60 / AH)",
        "reagents": [{"item_id": 13467, "count": 2}, {"item_id": 13466, "count": 2}, {"item_id": 8925, "count": 1}]
    },
    {
        "recipe_id": 17586, "recipe_item_id": 13517, "name_ru": "\u0421\u0438\u043b\u044c\u043d\u0435\u0439\u0448\u0435\u0435 \u0437\u0435\u043b\u044c\u0435 \u043c\u0430\u043d\u044b", "name_en": "Major Mana Potion",
        "min_skill": 295, "crafted_item_id": 13444, "yield": 1.0,
        "source": "drop", "source_details_ru": "\u0414\u0440\u043e\u043f: \u041d\u0435\u043a\u0440\u043e\u0441\u0438\u0442\u0435\u0442 (\u0418\u043d\u0441\u0442\u0440\u0443\u043a\u0442\u043e\u0440 \u041c\u0430\u043b\u0438\u0441\u0438\u044f / \u0413\u0430\u043d\u0434\u043b\u0438\u043d\u0433)", "source_details_en": "Drop: Scholomance (Gandling / Malicia)",
        "reagents": [{"item_id": 13463, "count": 3}, {"item_id": 13467, "count": 2}, {"item_id": 8925, "count": 1}]
    },
    {
        "recipe_id": 17624, "recipe_item_id": 13518, "name_ru": "\u041d\u0430\u0441\u0442\u043e\u0439 \u0442\u0438\u0442\u0430\u043d\u043e\u0432", "name_en": "Flask of the Titans",
        "min_skill": 300, "crafted_item_id": 13510, "yield": 1.0,
        "source": "drop", "source_details_ru": "\u0414\u0440\u043e\u043f: \u0413\u0435\u043d\u0435\u0440\u0430\u043b \u0414\u0440\u0430\u043a\u043a\u0438\u0441\u0430\u0442 (\u0412\u0435\u0440\u0448\u0438\u043d\u0430 \u0427\u0435\u0440\u043d\u043e\u0439 \u0433\u043e\u0440\u044b / UBRS)", "source_details_en": "Drop: General Drakkisath (UBRS)",
        "reagents": [{"item_id": 8846, "count": 30}, {"item_id": 13467, "count": 10}, {"item_id": 13468, "count": 1}, {"item_id": 8925, "count": 1}]
    },
    {
        "recipe_id": 17626, "recipe_item_id": 13520, "name_ru": "\u041d\u0430\u0441\u0442\u043e\u0439 \u0447\u0438\u0441\u0442\u043e\u0439 \u043c\u0443\u0434\u0440\u043e\u0441\u0442\u0438", "name_en": "Flask of Distilled Wisdom",
        "min_skill": 300, "crafted_item_id": 13511, "yield": 1.0,
        "source": "drop", "source_details_ru": "\u0414\u0440\u043e\u043f: \u0411\u0430\u043b\u043d\u0430\u0437\u0437\u0430\u0440 (\u0421\u0442\u0440\u0430\u0442\u0445\u043e\u043b\u044c\u043c)", "source_details_en": "Drop: Balnazzar (Stratholme)",
        "reagents": [{"item_id": 13463, "count": 30}, {"item_id": 13467, "count": 10}, {"item_id": 13468, "count": 1}, {"item_id": 8925, "count": 1}]
    },
    {
        "recipe_id": 17627, "recipe_item_id": 13521, "name_ru": "\u041d\u0430\u0441\u0442\u043e\u0439 \u0432\u0435\u043b\u0438\u043a\u043e\u0439 \u0441\u0438\u043b\u044b", "name_en": "Flask of Supreme Power",
        "min_skill": 300, "crafted_item_id": 13512, "yield": 1.0,
        "source": "drop", "source_details_ru": "\u0414\u0440\u043e\u043f: \u0420\u0430\u0441 \u041b\u0435\u0434\u044f\u043d\u043e\u0439 \u0428\u0435\u043f\u043e\u0442 (\u041d\u0435\u043a\u0440\u043e\u0441\u0438\u0442\u0435\u0442)", "source_details_en": "Drop: Ras Frostwhisper (Scholomance)",
        "reagents": [{"item_id": 13463, "count": 30}, {"item_id": 13465, "count": 10}, {"item_id": 13468, "count": 1}, {"item_id": 8925, "count": 1}]
    },
    {
        "recipe_id": 17628, "recipe_item_id": 13522, "name_ru": "\u041d\u0430\u0441\u0442\u043e\u0439 \u043e\u043a\u0430\u043c\u0435\u043d\u0435\u043d\u0438\u044f", "name_en": "Flask of Petrification",
        "min_skill": 300, "crafted_item_id": 13513, "yield": 1.0,
        "source": "drop", "source_details_ru": "\u041c\u0438\u0440\u043e\u0432\u043e\u0439 \u0434\u0440\u043e\u043f (58-60 \u0443\u0440. / \u0410\u0443\u043a\u0446\u0438\u043e\u043d)", "source_details_en": "World Drop (AH)",
        "reagents": [{"item_id": 13423, "count": 30}, {"item_id": 13465, "count": 10}, {"item_id": 13468, "count": 1}, {"item_id": 8925, "count": 1}]
    },
    {
        "recipe_id": 11479, "recipe_item_id": 9304, "name_ru": "\u0422\u0440\u0430\u043d\u0441\u043c\u0443\u0442\u0430\u0446\u0438\u044f: \u0436\u0435\u043b\u0435\u0437\u043e \u0432 \u0437\u043e\u043b\u043e\u0442\u043e", "name_en": "Transmute: Iron to Gold",
        "min_skill": 225, "crafted_item_id": 3577, "yield": 1.0,
        "source": "vendor", "source_details_ru": "\u0412\u0435\u043d\u0434\u043e\u0440: \u0410\u043b\u0445\u0438\u043c\u0438\u043a \u041f\u0435\u0441\u0442\u044b\u0448 (\u041f\u043e\u0434\u0433\u043e\u0440\u043e\u0434) / \u0421\u043a\u0438\u043d\u0434\u0440\u0435\u0439 (\u0414\u0430\u0440\u043d\u0430\u0441)", "source_details_en": "Vendor: Algernon / Alchemist",
        "reagents": [{"item_id": 3575, "count": 1}]
    },
    {
        "recipe_id": 11480, "recipe_item_id": 9305, "name_ru": "\u0422\u0440\u0430\u043d\u0441\u043c\u0443\u0442\u0430\u0446\u0438\u044f: \u043c\u0438\u0444\u0440\u0438\u043b \u0432 \u0438\u0441\u0442\u0438\u043d\u043d\u043e\u0435 \u0441\u0435\u0440\u0435\u0431\u0440\u043e", "name_en": "Transmute: Mithril to Truesilver",
        "min_skill": 225, "crafted_item_id": 6037, "yield": 1.0,
        "source": "vendor", "source_details_ru": "\u0412\u0435\u043d\u0434\u043e\u0440: \u0410\u043b\u0445\u0438\u043c\u0438\u043a \u041f\u0435\u0441\u0442\u044b\u0448 (\u041f\u043e\u0434\u0433\u043e\u0440\u043e\u0434) / \u0421\u043a\u0438\u043d\u0434\u0440\u0435\u0439 (\u0414\u0430\u0440\u043d\u0430\u0441)", "source_details_en": "Vendor: Algernon / Alchemist",
        "reagents": [{"item_id": 3860, "count": 1}]
    },
    {
        "recipe_id": 17187, "recipe_item_id": 12958, "name_ru": "\u0422\u0440\u0430\u043d\u0441\u043c\u0443\u0442\u0430\u0446\u0438\u044f: \u0430\u0440\u043a\u0430\u043d\u0438\u0442", "name_en": "Transmute: Arcanite",
        "min_skill": 275, "crafted_item_id": 12360, "yield": 1.0,
        "source": "vendor", "source_details_ru": "\u0412\u0435\u043d\u0434\u043e\u0440: \u041f\u0435\u0441\u0442\u044b\u0448 (\u0422\u0430\u043d\u0430\u0440\u0438\u0441)", "source_details_en": "Vendor: Piznik (Tanaris)",
        "reagents": [{"item_id": 12359, "count": 1}, {"item_id": 12363, "count": 1}]
    },
]

crafted_items = [
    {"item_id": 118, "name_ru": "\u041a\u0440\u043e\u0448\u0435\u0447\u043d\u043e\u0435 \u0437\u0435\u043b\u044c\u0435 \u0438\u0441\u0446\u0435\u043b\u0435\u043d\u0438\u044f", "name_en": "Minor Healing Potion", "quality": 1, "item_class": 0, "item_subclass": 1, "vendor_sell_copper": 15, "vendor_buy_copper": 60},
    {"item_id": 858, "name_ru": "\u041c\u0430\u043b\u043e\u0435 \u0437\u0435\u043b\u044c\u0435 \u0438\u0441\u0446\u0435\u043b\u0435\u043d\u0438\u044f", "name_en": "Lesser Healing Potion", "quality": 1, "item_class": 0, "item_subclass": 1, "vendor_sell_copper": 50, "vendor_buy_copper": 200},
    {"item_id": 929, "name_ru": "\u0417\u0435\u043b\u044c\u0435 \u0438\u0441\u0446\u0435\u043b\u0435\u043d\u0438\u044f", "name_en": "Healing Potion", "quality": 1, "item_class": 0, "item_subclass": 1, "vendor_sell_copper": 200, "vendor_buy_copper": 800},
    {"item_id": 1710, "name_ru": "\u0411\u043e\u043b\u044c\u0448\u043e\u0435 \u0437\u0435\u043b\u044c\u0435 \u0438\u0441\u0446\u0435\u043b\u0435\u043d\u0438\u044f", "name_en": "Greater Healing Potion", "quality": 1, "item_class": 0, "item_subclass": 1, "vendor_sell_copper": 625, "vendor_buy_copper": 2500},
    {"item_id": 3928, "name_ru": "\u041f\u0440\u0435\u0432\u043e\u0441\u0445\u043e\u0434\u043d\u043e\u0435 \u0437\u0435\u043b\u044c\u0435 \u0438\u0441\u0446\u0435\u043b\u0435\u043d\u0438\u044f", "name_en": "Superior Healing Potion", "quality": 1, "item_class": 0, "item_subclass": 1, "vendor_sell_copper": 1500, "vendor_buy_copper": 6000},
    {"item_id": 13446, "name_ru": "\u0421\u0438\u043b\u044c\u043d\u0435\u0439\u0448\u0435\u0435 \u0437\u0435\u043b\u044c\u0435 \u0438\u0441\u0446\u0435\u043b\u0435\u043d\u0438\u044f", "name_en": "Major Healing Potion", "quality": 1, "item_class": 0, "item_subclass": 1, "vendor_sell_copper": 3500, "vendor_buy_copper": 14000},
    {"item_id": 2455, "name_ru": "\u041a\u0440\u043e\u0448\u0435\u0447\u043d\u043e\u0435 \u0437\u0435\u043b\u044c\u0435 \u043c\u0430\u043d\u044b", "name_en": "Minor Mana Potion", "quality": 1, "item_class": 0, "item_subclass": 1, "vendor_sell_copper": 25, "vendor_buy_copper": 100},
    {"item_id": 3385, "name_ru": "\u041c\u0430\u043b\u043e\u0435 \u0437\u0435\u043b\u044c\u0435 \u043c\u0430\u043d\u044b", "name_en": "Lesser Mana Potion", "quality": 1, "item_class": 0, "item_subclass": 1, "vendor_sell_copper": 75, "vendor_buy_copper": 300},
    {"item_id": 3827, "name_ru": "\u0417\u0435\u043b\u044c\u0435 \u043c\u0430\u043d\u044b", "name_en": "Mana Potion", "quality": 1, "item_class": 0, "item_subclass": 1, "vendor_sell_copper": 300, "vendor_buy_copper": 1200},
    {"item_id": 6149, "name_ru": "\u0411\u043e\u043b\u044c\u0448\u043e\u0435 \u0437\u0435\u043b\u044c\u0435 \u043c\u0430\u043d\u044b", "name_en": "Greater Mana Potion", "quality": 1, "item_class": 0, "item_subclass": 1, "vendor_sell_copper": 1000, "vendor_buy_copper": 4000},
    {"item_id": 13443, "name_ru": "\u041f\u0440\u0435\u0432\u043e\u0441\u0445\u043e\u0434\u043d\u043e\u0435 \u0437\u0435\u043b\u044c\u0435 \u043c\u0430\u043d\u044b", "name_en": "Superior Mana Potion", "quality": 1, "item_class": 0, "item_subclass": 1, "vendor_sell_copper": 2500, "vendor_buy_copper": 10000},
    {"item_id": 13444, "name_ru": "\u0421\u0438\u043b\u044c\u043d\u0435\u0439\u0448\u0435\u0435 \u0437\u0435\u043b\u044c\u0435 \u043c\u0430\u043d\u044b", "name_en": "Major Mana Potion", "quality": 1, "item_class": 0, "item_subclass": 1, "vendor_sell_copper": 5000, "vendor_buy_copper": 20000},
    {"item_id": 2456, "name_ru": "\u041a\u0440\u043e\u0448\u0435\u0447\u043d\u043e\u0435 \u0437\u0435\u043b\u044c\u0435 \u043e\u043c\u043e\u043b\u043e\u0436\u0435\u043d\u0438\u044f", "name_en": "Minor Rejuvenation Potion", "quality": 1, "item_class": 0, "item_subclass": 1, "vendor_sell_copper": 40, "vendor_buy_copper": 160},
    {"item_id": 2459, "name_ru": "\u0417\u0435\u043b\u044c\u0435 \u0441\u0442\u0440\u0435\u043c\u0438\u0442\u0435\u043b\u044c\u043d\u043e\u0441\u0442\u0438", "name_en": "Swiftness Potion", "quality": 1, "item_class": 0, "item_subclass": 1, "vendor_sell_copper": 125, "vendor_buy_copper": 500},
    {"item_id": 5634, "name_ru": "\u0417\u0435\u043b\u044c\u0435 \u0441\u0432\u043e\u0431\u043e\u0434\u044b \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u0439", "name_en": "Free Action Potion", "quality": 1, "item_class": 0, "item_subclass": 1, "vendor_sell_copper": 500, "vendor_buy_copper": 2000},
    {"item_id": 6372, "name_ru": "\u0417\u0435\u043b\u044c\u0435 \u0431\u044b\u0441\u0442\u0440\u043e\u0433\u043e \u043f\u043b\u0430\u0432\u0430\u043d\u0438\u044f", "name_en": "Swim Speed Potion", "quality": 1, "item_class": 0, "item_subclass": 1, "vendor_sell_copper": 150, "vendor_buy_copper": 600},
    {"item_id": 6048, "name_ru": "\u0417\u0435\u043b\u044c\u0435 \u0437\u0430\u0449\u0438\u0442\u044b \u043e\u0442 \u043e\u0433\u043d\u044f", "name_en": "Fire Protection Potion", "quality": 1, "item_class": 0, "item_subclass": 1, "vendor_sell_copper": 400, "vendor_buy_copper": 1600},
    {"item_id": 13457, "name_ru": "\u0421\u0438\u043b\u044c\u043d\u043e\u0435 \u0437\u0435\u043b\u044c\u0435 \u0437\u0430\u0449\u0438\u0442\u044b \u043e\u0442 \u043e\u0433\u043d\u044f", "name_en": "Greater Fire Protection Potion", "quality": 1, "item_class": 0, "item_subclass": 1, "vendor_sell_copper": 3000, "vendor_buy_copper": 12000},
    {"item_id": 13456, "name_ru": "\u0421\u0438\u043b\u044c\u043d\u043e\u0435 \u0437\u0435\u043b\u044c\u0435 \u0437\u0430\u0449\u0438\u0442\u044b \u043e\u0442 \u043c\u0430\u0433\u0438\u0438 \u043b\u044c\u0434\u0430", "name_en": "Greater Frost Protection Potion", "quality": 1, "item_class": 0, "item_subclass": 1, "vendor_sell_copper": 3000, "vendor_buy_copper": 12000},
    {"item_id": 13458, "name_ru": "\u0421\u0438\u043b\u044c\u043d\u043e\u0435 \u0437\u0435\u043b\u044c\u0435 \u0437\u0430\u0449\u0438\u0442\u044b \u043e\u0442 \u0441\u0438\u043b \u043f\u0440\u0438\u0440\u043e\u0434\u044b", "name_en": "Greater Nature Protection Potion", "quality": 1, "item_class": 0, "item_subclass": 1, "vendor_sell_copper": 3000, "vendor_buy_copper": 12000},
    {"item_id": 13459, "name_ru": "\u0421\u0438\u043b\u044c\u043d\u043e\u0435 \u0437\u0435\u043b\u044c\u0435 \u0437\u0430\u0449\u0438\u0442\u044b \u043e\u0442 \u0442\u0435\u043c\u043d\u043e\u0439 \u043c\u0430\u0433\u0438\u0438", "name_en": "Greater Shadow Protection Potion", "quality": 1, "item_class": 0, "item_subclass": 1, "vendor_sell_copper": 3000, "vendor_buy_copper": 12000},
    {"item_id": 13461, "name_ru": "\u0421\u0438\u043b\u044c\u043d\u043e\u0435 \u0437\u0435\u043b\u044c\u0435 \u0437\u0430\u0449\u0438\u0442\u044b \u043e\u0442 \u0442\u0430\u0439\u043d\u043e\u0439 \u043c\u0430\u0433\u0438\u0438", "name_en": "Greater Arcane Protection Potion", "quality": 1, "item_class": 0, "item_subclass": 1, "vendor_sell_copper": 3000, "vendor_buy_copper": 12000},
    {"item_id": 13462, "name_ru": "\u041e\u0447\u0438\u0449\u0430\u044e\u0449\u0435\u0435 \u0437\u0435\u043b\u044c\u0435", "name_en": "Purification Potion", "quality": 1, "item_class": 0, "item_subclass": 1, "vendor_sell_copper": 2500, "vendor_buy_copper": 10000},
    {"item_id": 2454, "name_ru": "\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u043b\u044c\u0432\u0438\u043d\u043e\u0439 \u0441\u0438\u043b\u044b", "name_en": "Elixir of Lion's Strength", "quality": 1, "item_class": 0, "item_subclass": 2, "vendor_sell_copper": 20, "vendor_buy_copper": 80},
    {"item_id": 5997, "name_ru": "\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u043c\u0430\u043b\u043e\u0439 \u0437\u0430\u0449\u0438\u0442\u044b", "name_en": "Elixir of Minor Defense", "quality": 1, "item_class": 0, "item_subclass": 2, "vendor_sell_copper": 25, "vendor_buy_copper": 100},
    {"item_id": 2457, "name_ru": "\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u043c\u0430\u043b\u043e\u0439 \u043b\u043e\u0432\u043a\u043e\u0441\u0442\u0438", "name_en": "Elixir of Minor Agility", "quality": 1, "item_class": 0, "item_subclass": 2, "vendor_sell_copper": 35, "vendor_buy_copper": 140},
    {"item_id": 3390, "name_ru": "\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u043d\u0435\u0437\u043d\u0430\u0447\u0438\u0442\u0435\u043b\u044c\u043d\u043e\u0439 \u043b\u043e\u0432\u043a\u043e\u0441\u0442\u0438", "name_en": "Elixir of Lesser Agility", "quality": 1, "item_class": 0, "item_subclass": 2, "vendor_sell_copper": 150, "vendor_buy_copper": 600},
    {"item_id": 8949, "name_ru": "\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u043b\u043e\u0432\u043a\u043e\u0441\u0442\u0438", "name_en": "Elixir of Agility", "quality": 1, "item_class": 0, "item_subclass": 2, "vendor_sell_copper": 500, "vendor_buy_copper": 2000},
    {"item_id": 9187, "name_ru": "\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u0432\u0435\u043b\u0438\u043a\u043e\u0439 \u043b\u043e\u0432\u043a\u043e\u0441\u0442\u0438", "name_en": "Elixir of Greater Agility", "quality": 1, "item_class": 0, "item_subclass": 2, "vendor_sell_copper": 1500, "vendor_buy_copper": 6000},
    {"item_id": 13452, "name_ru": "\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u043c\u0430\u043d\u0433\u0443\u0441\u0442\u0430", "name_en": "Elixir of the Mongoose", "quality": 1, "item_class": 0, "item_subclass": 2, "vendor_sell_copper": 3500, "vendor_buy_copper": 14000},
    {"item_id": 9206, "name_ru": "\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u0432\u0435\u043b\u0438\u043a\u0430\u043d\u043e\u0432", "name_en": "Elixir of Giants", "quality": 1, "item_class": 0, "item_subclass": 2, "vendor_sell_copper": 1250, "vendor_buy_copper": 5000},
    {"item_id": 3825, "name_ru": "\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u0441\u0442\u043e\u0439\u043a\u043e\u0441\u0442\u0438", "name_en": "Elixir of Fortitude", "quality": 1, "item_class": 0, "item_subclass": 2, "vendor_sell_copper": 350, "vendor_buy_copper": 1400},
    {"item_id": 8950, "name_ru": "\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u0432\u0435\u043b\u0438\u043a\u043e\u0439 \u0437\u0430\u0449\u0438\u0442\u044b", "name_en": "Elixir of Greater Defense", "quality": 1, "item_class": 0, "item_subclass": 2, "vendor_sell_copper": 650, "vendor_buy_copper": 2600},
    {"item_id": 13445, "name_ru": "\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u043f\u0440\u0435\u0432\u043e\u0441\u0445\u043e\u0434\u043d\u043e\u0439 \u0437\u0430\u0449\u0438\u0442\u044b", "name_en": "Elixir of Superior Defense", "quality": 1, "item_class": 0, "item_subclass": 2, "vendor_sell_copper": 2000, "vendor_buy_copper": 8000},
    {"item_id": 9179, "name_ru": "\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u0432\u0435\u043b\u0438\u043a\u043e\u0433\u043e \u0438\u043d\u0442\u0435\u043b\u043b\u0435\u043a\u0442\u0430", "name_en": "Elixir of Greater Intellect", "quality": 1, "item_class": 0, "item_subclass": 2, "vendor_sell_copper": 1000, "vendor_buy_copper": 4000},
    {"item_id": 13447, "name_ru": "\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u043c\u0443\u0434\u0440\u0435\u0446\u043e\u0432", "name_en": "Elixir of the Sages", "quality": 1, "item_class": 0, "item_subclass": 2, "vendor_sell_copper": 2200, "vendor_buy_copper": 8800},
    {"item_id": 6373, "name_ru": "\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u0441\u0438\u043b\u044b \u043e\u0433\u043d\u044f", "name_en": "Elixir of Firepower", "quality": 1, "item_class": 0, "item_subclass": 2, "vendor_sell_copper": 450, "vendor_buy_copper": 1800},
    {"item_id": 9264, "name_ru": "\u042d\u043b\u0438\u043a\u0441\u0438\u0440 \u0441\u0438\u043b\u044b \u0442\u044c\u043c\u044b", "name_en": "Elixir of Shadow Power", "quality": 1, "item_class": 0, "item_subclass": 2, "vendor_sell_copper": 1500, "vendor_buy_copper": 6000},
    {"item_id": 9155, "name_ru": "\u0427\u0430\u0440\u043e\u0434\u0435\u0439\u0441\u043a\u0438\u0439 \u044d\u043b\u0438\u043a\u0441\u0438\u0440", "name_en": "Arcane Elixir", "quality": 1, "item_class": 0, "item_subclass": 2, "vendor_sell_copper": 1250, "vendor_buy_copper": 5000},
    {"item_id": 13454, "name_ru": "\u041c\u043e\u0449\u043d\u044b\u0439 \u0447\u0430\u0440\u043e\u0434\u0435\u0439\u0441\u043a\u0438\u0439 \u044d\u043b\u0438\u043a\u0441\u0438\u0440", "name_en": "Greater Arcane Elixir", "quality": 1, "item_class": 0, "item_subclass": 2, "vendor_sell_copper": 3500, "vendor_buy_copper": 14000},
    {"item_id": 6370, "name_ru": "\u041c\u0430\u0441\u043b\u043e \u0447\u0435\u0440\u043d\u043e\u0440\u043e\u0442\u0438\u043a\u0430", "name_en": "Blackmouth Oil", "quality": 1, "item_class": 0, "item_subclass": 1, "vendor_sell_copper": 60, "vendor_buy_copper": 240},
    {"item_id": 6371, "name_ru": "\u041e\u0433\u043d\u0435\u0432\u043e\u0435 \u043c\u0430\u0441\u043b\u043e", "name_en": "Fire Oil", "quality": 1, "item_class": 0, "item_subclass": 1, "vendor_sell_copper": 120, "vendor_buy_copper": 480},
    {"item_id": 13423, "name_ru": "\u041c\u0430\u0441\u043b\u043e \u043a\u0430\u043c\u0435\u043d\u043d\u043e\u0439 \u0447\u0435\u0448\u0443\u0438", "name_en": "Stonescale Oil", "quality": 1, "item_class": 0, "item_subclass": 1, "vendor_sell_copper": 300, "vendor_buy_copper": 1200},
    {"item_id": 3824, "name_ru": "\u041c\u0430\u0441\u043b\u043e \u0442\u044c\u043c\u044b", "name_en": "Shadow Oil", "quality": 1, "item_class": 0, "item_subclass": 1, "vendor_sell_copper": 400, "vendor_buy_copper": 1600},
    {"item_id": 3829, "name_ru": "\u041c\u0430\u0441\u043b\u043e \u043b\u044c\u0434\u0430", "name_en": "Frost Oil", "quality": 1, "item_class": 0, "item_subclass": 1, "vendor_sell_copper": 650, "vendor_buy_copper": 2600},
    {"item_id": 13510, "name_ru": "\u041d\u0430\u0441\u0442\u043e\u0439 \u0442\u0438\u0442\u0430\u043d\u043e\u0432", "name_en": "Flask of the Titans", "quality": 3, "item_class": 0, "item_subclass": 2, "vendor_sell_copper": 10000, "vendor_buy_copper": 40000},
    {"item_id": 13511, "name_ru": "\u041d\u0430\u0441\u0442\u043e\u0439 \u0447\u0438\u0441\u0442\u043e\u0439 \u043c\u0443\u0434\u0440\u043e\u0441\u0442\u0438", "name_en": "Flask of Distilled Wisdom", "quality": 3, "item_class": 0, "item_subclass": 2, "vendor_sell_copper": 10000, "vendor_buy_copper": 40000},
    {"item_id": 13512, "name_ru": "\u041d\u0430\u0441\u0442\u043e\u0439 \u0432\u0435\u043b\u0438\u043a\u043e\u0439 \u0441\u0438\u043b\u044b", "name_en": "Flask of Supreme Power", "quality": 3, "item_class": 0, "item_subclass": 2, "vendor_sell_copper": 10000, "vendor_buy_copper": 40000},
    {"item_id": 13513, "name_ru": "\u041d\u0430\u0441\u0442\u043e\u0439 \u043e\u043a\u0430\u043c\u0435\u043d\u0435\u043d\u0438\u044f", "name_en": "Flask of Petrification", "quality": 3, "item_class": 0, "item_subclass": 2, "vendor_sell_copper": 10000, "vendor_buy_copper": 40000},
]

out = {
    "herbs": herbs,
    "reagents": reagents,
    "recipes": recipes,
    "crafted_items": crafted_items
}

with open("data/herbalism_alchemy_era.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

print("Generated clean UTF-8 herbalism_alchemy_era.json")
