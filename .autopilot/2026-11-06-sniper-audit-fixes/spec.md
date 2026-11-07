# Спецификация: Устранение дефектов аудита ForeverTrade_Sniper

## 1. Контекст и цели
По результатам проведенного аудита микро-аддона `ForeverTrade_Sniper` v2.1 и связанного с ним генератора конфигурации выявлен ряд критических уязвимостей (риск потери золота при сдвиге лота, блокировка 90% сигналов из-за двойного фильтра прибыли, зависание колеса мыши при боевом локдауне) и эксплуатационных дефектов.

Цель прогона: устранить все 10 пунктов аудита минимальным и надежным диффом в соответствии с доктриной Ponytail, разделив работу между агентом-кодером и агентом-тестером.

## 2. Изменения в коде аддона (`addons/ForeverTrade_Sniper/ForeverTrade_Sniper.lua`)
1. **Защита от сдвига стака (R07.1):**
   - В функцию `FT_ExecuteVerifiedBuy(index, expectedItemId, expectedBuyout, expectedCount, unitTarget, hash)` передавать параметры `expectedCount` и `unitTarget`.
   - Проверять условие: `actualItemId == expectedItemId and buyoutPrice == expectedBuyout and count == expectedCount and (buyoutPrice / count) <= unitTarget and buyoutPrice > 0`.
   - Если условие нарушено, покупку отменять с выводом предупреждения в чат.

2. **Устранение двойной фильтрации прибыли (R07.2):**
   - Условие перехвата лота: `if unitPrice <= unitTarget and (minProfit <= 0 or totalSaved >= minProfit) then`.
   - При дефолтном `minProfit == 0` лоты срабатывают сразу по достижении расчетного потолка выкупа, не блокируя дешевые травы/зелья.

3. **Очистка и защита ветки выкупа (R07.3):**
   - Для Legacy AH (клиент Forever 1.60 / Era 1.14/1.15) оставить отточенный прямой вызов `PlaceAuctionBid("list", index, expectedBuyout)`.
   - Устранить передачу `itemId` под видом `auctionID` и убрать блокирующие синхронные вызовы `ConfirmCommoditiesPurchase`.

4. **Обработка боевого локдауна (R07.4):**
   - Зарегистрировать события `PLAYER_REGEN_ENABLED` и `PLAYER_REGEN_DISABLED`.
   - Ввести локальный флаг `local pendingClearBindings = false`.
   - При `FT_DisarmSniperBuy()` во время `InCombatLockdown()` взводить `pendingClearBindings = true`.
   - При получении `PLAYER_REGEN_ENABLED` выполнять `ClearOverrideBindings(buyBtn)` и сбрасывать флаг.

5. **Безопасный выкуп без макро-инъекций (R07.5):**
   - Избавить `buyBtn` от типа `"macro"` и вызова `/run`.
   - Создавать кнопку как стандартный `Button`, обрабатывать клик через `buyBtn:SetScript("OnClick", ...)`, сохранять текущую цель в `buyBtn._target`.
   - Инкапсулировать состояние в `local`, оставив наружу управляющий фасад `ForeverTradeSniper`.

6. **UI исправление Shift+Drag (R07.6):**
   - В `toggleBtn:SetScript("OnDragStart", ...)` ставить `self.isDragging = true`.
   - В `toggleBtn:SetScript("OnClick", ...)` проверять `if self.isDragging or IsShiftKeyDown() then self.isDragging = false return end`.

7. **Оптимизация GC и OnUpdate (R07.9):**
   - Заменить `{ GetAuctionItemInfo("list", i) }` на прямую распаковку локальных переменных.
   - Убрать лишние проверки видимости фрейма на каждом тике `OnUpdate`.

8. **Справка по slash-командам (R07.10):**
   - Обрабатывать `msg == "help" or msg == "?"` выводом справки по доступным командам `/fts on|off|status|test|min|reset`.

## 3. Изменения в Python (`src/analytics.py`, `run_watcher.py`)
1. **Атомарная запись `config.lua` (R07.7):**
   - В `export_sniper_addon_config` писать сначала во временный файл `tmp_path = output_path + ".tmp"`, затем заменять через `os.replace(tmp_path, output_path)`.
   - Дефолтное значение `min_profit_copper = 0` (поскольку маржа уже учтена в формуле потолка).
2. **Устранение двойного тяжелого перерасчета (R07.8):**
   - В `run_watcher.py` при экспорте в `--wow-addon-path` не запускать `export_sniper_addon_config` повторно, а копировать готовый `config.lua` через `shutil.copyfile`.

## 4. Тестирование и верификация (`tests/test_sniper_addon.py`, `tests/test_analytics.py`)
- Дополнить `WoWSniperHarness` поддержкой новых инвариантов: `expectedCount`, `unitTarget`, `PLAYER_REGEN_ENABLED`, атомарности экспорта.
- Проверить тесты на прохождение без "бумажных" фиктивных проверок.
- Полный прогон `pytest` должен вернуть 224+ пройденных тестов без ошибок.

## 5. Таски (Tickets)
- **T01:** Подготовка и синхронизация дашборда Autopilot (порт 10459).
- **T02:** [Coder Agent] Реализация исправлений в `addons/ForeverTrade_Sniper/ForeverTrade_Sniper.lua`.
- **T03:** [Coder Agent] Реализация атомарности и оптимизации экспорта в `src/analytics.py` и `run_watcher.py`.
- **T04:** [Tester Agent] Обновление эмулятора и тестов в `tests/test_sniper_addon.py` и `tests/test_analytics.py`.
- **T05:** Полная приёмка: прогон pytest, верификация статуса дашборда и закрытие прогона.
