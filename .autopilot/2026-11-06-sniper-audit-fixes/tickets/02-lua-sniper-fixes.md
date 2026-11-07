# 02 — [Coder Agent] Реализация исправлений в ForeverTrade_Sniper.lua

**Требования:** R03, R07, R08, R09, R10, R11, R12, R15, R16
**Зона:** `addons/ForeverTrade_Sniper/`
**Волна:** 2
**Зависит от:** 01
**Итог:** Устранены дефекты сдвига стака, боевого локдауна, макро-инъекций и UI кнопки

## Что делаем
Устранить выявленные аудитом дефекты в `addons/ForeverTrade_Sniper/ForeverTrade_Sniper.lua`:
1. Защита от сдвига стака: `expectedCount`, `unitTarget`, проверка `unitPrice <= unitTarget`.
2. Устранение двойного фильтра прибыли (срабатывание при `unitPrice <= unitTarget and (minProfit <= 0 or totalSaved >= minProfit)`).
3. Изоляция/очистка выкупа: устранение коллизии `auctionID`/`itemId`.
4. Добавление `PLAYER_REGEN_ENABLED` / `PLAYER_REGEN_DISABLED` и отложенная очистка биндов.
5. Замена макроса `/run` на прямое назначение `buyBtn._target` и обработчик `OnClick`.
6. Устранение самопроизвольного переключения при Shift+Drag.
7. Распаковка `GetAuctionItemInfo` в локальные переменные без аллокаций таблиц.
8. Поддержка `/fts help` / `/fts ?`.

## Критерии приёмки
- Код компилируется синтаксически в Lua.
- Продакшен-файл изменен строго агентом-кодером.
