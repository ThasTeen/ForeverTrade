# Интерфейсы и контракты Forever Trade Sniper v2.1

## 1. Конфигурация аддона `config.lua`
```lua
FT_SNIPER_CONFIG = {
    version = 2,
    generated_at = <unix_timestamp>,
    min_profit_copper = 1000, -- Минимальный профит со стака в меди
    targets = {
        [<item_id>] = <max_buyout_unit_copper>,
        ...
    }
}
```

## 2. API функции микро-аддона `ForeverTrade_Sniper.lua`
- `FT_UpdateTargetPage()`: расчет индекса последней страницы с чередованием для защиты от сдвига.
- `FT_ArmSniperBuy(index, itemId, buyout, hash)`: взведение выкупа лота на колесо мыши, звуковой сигнал, пауза сканера.
- `FT_DisarmSniperBuy()`: снятие биндов колеса мыши и возобновление сканера.
- `FT_ExecuteVerifiedBuy(index, expectedItemId, expectedBuyout, hash)`: верификация слота и отправка `PlaceAuctionBid`.

## 3. Схема БД `market.db` (новая таблица)
```sql
CREATE TABLE IF NOT EXISTS item_market_metrics (
    item_id INTEGER,
    realm TEXT NOT NULL,
    median_price INTEGER NOT NULL,
    mad INTEGER NOT NULL,
    sigma_mad INTEGER NOT NULL,
    ewma_price INTEGER NOT NULL,
    liquidity_score REAL NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (item_id, realm),
    FOREIGN KEY (item_id) REFERENCES items(item_id)
);
```

## 4. Контракты Python API в `src/analytics.py`
- `compute_robust_market_metrics(prices_history, timestamps)` -> `dict(median, mad, sigma_mad, ewma)`
- `compute_liquidity_score(item_id, history, item_type)` -> `float in [0.01, 1.0]`
- `load_disenchant_tables(path)` -> `dict`
- `calculate_disenchant_value(item_info, market_prices, de_tables)` -> `dict(expected_value, reagents)`
- `get_disenchant_arbitrage(db_conn, realm, min_profit_copper, de_tables_path)` -> `list[dict]`
- `resolve_effective_material_cost(recipe_id, db_conn, realm, depth, visited)` -> `dict(unit_cost, tree)`
- `export_sniper_addon_config(db_conn, realm, output_path, min_profit_copper)` -> `dict(total_targets, path)`

## 5. Дополнительный CLI флаг в `run_watcher.py`
`--wow-addon-path`: путь к каталогу аддонов WoW (по умолчанию берется из `$WOW_WTF_PATH/../Interface/AddOns` или `addons/`).
