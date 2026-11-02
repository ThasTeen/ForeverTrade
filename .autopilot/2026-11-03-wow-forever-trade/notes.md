# Техническое исследование кодовой базы и окружения WoW Forever (2026-11-03)

## 1. Исследование клиента WoW Forever и точных сигнатур API
- **Путь к клиенту:** `D:\Games\World of Warcraft\_forever_`
- **Версия клиента:** 1.60.1.70707 (Product Flavor `wowf`, 64-bit DirectX 12).
- **Механизмы запуска скриптов без входа в мир:**
  - В бинарнике `Wow.exe` обнаружены флаги CLI: `[-gluescriptFile]`, `[-gamescriptFile]`, `[-console]`, `[-windowed]`, `[-nosound]`.
  - Команда консоли `ExportInterfaceFiles code` экспортирует полное древо исходников интерфейса Blizzard (FrameXML, GlueXML, SharedXML).
- **Сигнатуры Auction House API:**
  - Проанализирован установленный аддон `Auctionator` (`Source_LegacyAH`):
    - Вызов `GetNumAuctionItems("list")` возвращает `batchCount, totalCount`.
    - Вызов `GetAuctionItemInfo("list", i)` в LegacyAH клиенте:
      - Индекс 3: `count` (Quantity)
      - Индекс 8: `minBid` (MinBid)
      - Индекс 10: `buyoutPrice` (Buyout)
      - Индекс 14: `owner` (Owner)
      - Индекс 16: `saleStatus` (SaleStatus)
      - Индекс 17: `itemId` (ItemID)
    - Вызов `GetAuctionItemLink("list", i)` возвращает гиперссылку предмета `item:(\d+)`.
    - Вызов `PlaceAuctionBid("list", index, buyoutPrice)` — защищенная функция, требует аппаратного клика/скролла (Hardware Event).
    - `QueryAuctionItems("", nil, nil, FT_TargetPage, 0, 0, false, false, nil)` опрашивает конкретную страницу.

## 2. Анализ данных распыления (Disenchanting)
- В клиенте уже установлен аддон `Disenchantry` (`D:\Games\World of Warcraft\_forever_\Interface\AddOns\Disenchantry`).
- В файле `DisenchantResults.lua` зафиксированы канонические таблицы Classic Era и кастомная специфика сервера Forever:
  - `IsForever()` определяется по версии клиента `1.60.x`.
  - Необычное оружие (зелень) ilvl 16–30 на сервере Forever всегда дает `Greater Magic Essence` (ID 10939), тогда как броня дает `Lesser/Greater Astral Essence`.
  - Диапазоны ilvl: 5–15, 16–20, 21–25, 26–30, 31–35, 36–40, 41–45, 46–50, 51–55, 56–60, 61–65.
  - Редкая экипировка (синька): 1–25, 26–30, 31–35, 36–40, 41–45, 46–50, 51–55, 56–80 (Small/Large Radiant/Brilliant Shard + Nexus Crystal).
  - Эпическая экипировка (фиолет): 40–45, 46–50, 51–55 (2–4 Shard), 56–60 (1 Nexus), 61–88 (1–2 Nexus).

## 3. Архитектура интеграции Forever Trade
- `src/analytics.py`:
  - Добавляются функции: Hampel filter & EWMA, Liquidity score $L$, `get_disenchant_arbitrage()`, `resolve_effective_material_cost()` (DAG), `export_sniper_addon_config()`.
- `src/database.py`:
  - Таблица `item_market_metrics` для кэширования $\mu$, $\text{MAD}$, $\sigma_{\text{MAD}}$, EWMA, $L$.
- `src/dashboard.py`:
  - Новая вкладка `✨ Дизэнчант`, раскладка вероятностей дропа реагентов в инспекторе.
  - Дерево рекурсивного крафта с индикацией источника сырья (AH / Vendor / Sub-craft).
- `addons/ForeverTrade_Sniper/`:
  - `ForeverTrade_Sniper.toc`, `ForeverTrade_Sniper.lua`, генерируемый `config.lua`.
- `run_watcher.py`:
  - Добавление CLI аргумента `--wow-addon-path` для авто-записи `config.lua`.
