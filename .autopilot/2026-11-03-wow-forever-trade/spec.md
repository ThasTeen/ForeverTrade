# Спецификация Forever Trade Sniper v2.1

## 1. Обзор
Полный переход системы Forever Trade на автономный стек:
1. Микро-аддон `ForeverTrade_Sniper` в `addons/ForeverTrade_Sniper/` (TOC, Lua-сканер, config.lua).
2. Аналитическое ядро: Hampel Filter + EWMA, Liquidity Shield, Disenchant Shuffle, DAG-крафт.
3. Мост генерации `config.lua` из демона прямо в папку аддона.
4. Вкладка `✨ Дизэнчант` и визуализация DAG-крафта в Goblin Terminal.

## 2. Разделение на этапы
- **Этап 1: Портирование данных и Liquidity Shield**
  - Таблицы `data/disenchant_tables_era.json`.
  - Функции расчета Liquidity Score $L$ и скорректированной прибыли Z-score снайпинга.
- **Этап 2: Микро-аддон ForeverTrade_Sniper**
  - Создание аддона `addons/ForeverTrade_Sniper/`.
  - Реализация `export_sniper_addon_config()` в `src/analytics.py`.
  - Интеграция `--wow-addon-path` в `run_watcher.py`.
- **Этап 3: Disenchant Shuffle в ядре и UI**
  - Функция `get_disenchant_arbitrage()` в `src/analytics.py`.
  - Вкладка `✨ Дизэнчант` и детализация в UI `src/dashboard.py`.
- **Этап 4: Робастный DBMarket (Hampel Filter + EWMA)**
  - Фильтрация выбросов по MAD и экспоненциальное затухание.
  - Таблица `item_market_metrics` в `src/database.py`.
- **Этап 5: Рекурсивный DAG Crafting Engine**
  - Разрешение себестоимости через `resolve_effective_material_cost()`.
  - Дерево промежуточных крафтов в инспекторе дашборда.

## 3. Требования Ponytail
- Код пишется предельно компактно и чисто, без усложнений.
- Роли: Агент-Кодер пишет только продакшен-код (`src/`, `addons/`), Агент-Тестер пишет только тесты (`tests/`).
- Тесты проверяют реальные вычисления и функции, никаких строковых проверок исходников.
