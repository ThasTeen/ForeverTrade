# 13 — Исправление каталога предметов и рецептов (Swiftthistle, Briarthorn, Medium Armor Kit)

**Требования:** R45
**Зависит от:** —
**Зона:** `data/` · `src/database.py` · `forever_overrides.json` · `tests/test_database.py`
**Волна:** 10
**Ревью:** да — фундамент
**Модель:** сильная

## Что должно заработать

В каталоге Era 1.15 и коде базы данных устранены коллизии ID предметов:
1. ID 2450 — Briarthorn (Остротерн), цена вендора 50м.
2. ID 2452 — Swiftthistle (Свистоцвет), цена вендора 40м.
3. ID 2313 — Medium Armor Kit (Накладки из средней кожи), продажа 60м, покупка 250м.
4. ID 2316 — Toughened Leather Gloves (Плотные кожаные перчатки), продажа 137м.
5. Рецепт 3004 (Medium Armor Kit) создает crafted_item_id = 2313.
6. Рецепты 4004 (Lesser Healing Potion) и 4005 (Healing Potion) используют Briarthorn (2450) вместо Swiftthistle (2452).

## Из брифа, дословно

> «1. Ошибки в каталоге связывают цены с другими предметами. Например, ID 2452 записан как Briarthorn, хотя это Swiftthistle; Medium Armor Kit имеет ID 2313, а проект использует 2316. В результате рецепты получают чужие цены. Нужно сверить ID, реагенты и цены торговцев с данными клиента.»

## Разделы спецификации

Истории 45, Решения по реализации, interfaces.md §1.

## Критерии приёмки

- [ ] В `data/items_era_1_15.json` и `src/database.py` ID 2450 соответствует Briarthorn, а ID 2452 — Swiftthistle
- [ ] В `data/items_era_1_15.json` и `src/database.py` ID 2313 соответствует Medium Armor Kit, а ID 2316 — Toughened Leather Gloves
- [ ] Рецепт 3004 имеет crafted_item_id == 2313
- [ ] Рецепты 4004 и 4005 имеют reagent_item_id == 2450 для Briarthorn
- [ ] `tests/test_database.py` проверяет корректность маппинга 2450, 2452, 2313, 2316 и рецептов
