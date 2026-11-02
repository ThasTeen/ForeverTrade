# 02 — Микро-аддон ForeverTrade_Sniper и экспорт config.lua

**Требования:** R01, R02, R03, R04, R05, R06, R07, R08, R09, R10, R11, R12, R13, R14, R25, R26, R27
**Зависит от:** 01
**Зона:** `addons/ForeverTrade_Sniper/` · `src/analytics.py` · `run_watcher.py` · `tests/test_analytics.py`
**Волна:** 2
**Ревью:** да
**Модель:** сильная

## Что должно заработать
1. Создать `addons/ForeverTrade_Sniper/ForeverTrade_Sniper.toc`.
2. Создать `addons/ForeverTrade_Sniper/ForeverTrade_Sniper.lua` со всеми защищенными механизмами TSM Sniper:
   - Индексация последней страницы `FT_TargetPage` с защитой от сдвига `FT_CheckPrevPageNext`.
   - Token-bucket рейт-лимит (1.2с) и таймаут ответа 3.0с.
   - Поштучный расчет `buyout / count` и фильтр выгоды со стака `totalSaved >= min_profit_copper`.
   - LRU-кэш на 200 записей (без owner/timeLeft в ключе).
   - Безопасный выкуп по колесу мыши через `SecureActionButtonTemplate` с проверкой слота `FT_ExecuteVerifiedBuy` и сбросом при `AUCTION_HOUSE_CLOSED`.
   - Вывод приветствия с `generated_at` и числом целей.
3. Реализовать `export_sniper_addon_config()` в `src/analytics.py` по единой формуле MaxBuyout с дельтой слота сумки.
4. Добавить `--wow-addon-path` в `run_watcher.py`.
5. Написать тесты генерации конфига и структур аддона в `tests/test_analytics.py`.
