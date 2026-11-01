# 19 — Аналитическое ядро: Vendor Floor/Ceiling, Vendor Shuffle и защита экономики Дня 1

**Требования:** R58, R59, R60, R61, R62, R68i, R69i, R70i
**Зависит от:** 15
**Зона:** `data/items_era_1_15.json` · `src/database.py` · `src/analytics.py` · `tests/test_analytics.py` · `tests/test_database.py`
**Волна:** 13
**Ревью:** нет
**Модель:** сильная

## Что должно заработать

1. Поддержка полей `vendor_buy_copper` и `vendor_sell_copper` (наряду с `vendor_buy_price` и `vendor_sell_price`) при загрузке каталога в БД и в структурах предметов.
2. При расчёте стоимости крафта и ящиков стоимость любого продаваемого у NPC реагента ограничивается сверху его ценой у вендора: $C_{\text{reagent}} = \min(P_{\text{AH}}, P_{\text{vendor\_buy}})$.
3. Блокировка сигнала `STRONG BUY` для предметов, свободно продаваемых у NPC, если $P_{\text{AH}} \ge P_{\text{vendor\_buy}}$.
4. Добавление категории сигналов `VENDOR_SHUFFLE` (Vendor Arbitrage) при мгновенной гарантированной прибыли сдачи NPC ($P_{\text{AH}} < P_{\text{vendor\_sell}}$).
5. Защита от экономики «Дня 1» (Fresh Server Mode): при малом числе исторических точек ($N < 5$) безопасная обработка без падений, `NaN` и ложных срабатываний.
6. Поддержка динамической перезагрузки `forever_overrides.json` по `mtime` файла без перезапуска процесса.

## Из брифа, дословно

> «Ticket 19: Vendor Floor/Ceiling & Day-1 Analytics Hardening
> Файлы: data/items_era_1_15.json, src/analytics.py, tests/test_analytics.py
> 1. Поддержать поля vendor_buy_copper и vendor_sell_copper в каталоге предметов.
> 2. При расчёте стоимости крафта и ящиков ограничивать стоимость любого продаваемого у NPC реагента сверху его ценой у вендора: C_reagent = min(P_AH, P_vendor_buy).
> 3. Блокировать сигнал STRONG BUY для предметов, у которых P_AH >= P_vendor_buy.
> 4. Добавить категорию сигнала VENDOR_SHUFFLE, если P_AH < P_vendor_sell.»

## Разделы спецификации

Истории 58, 59, 60, 61, 62, 68, 69, 70, interfaces.md §3.

## Критерии приёмки

- [ ] В каталоге и SQLite поддерживаются поля `vendor_buy_copper` и `vendor_sell_copper`
- [ ] Себестоимость крафта реагентов NPC ограничена ценой вендора: $\min(P_{\text{AH}}, P_{\text{vendor\_buy}})$
- [ ] Сигнал `STRONG BUY` заблокирован, если $P_{\text{AH}} \ge P_{\text{vendor\_buy}}$
- [ ] Добавлена категория `VENDOR_SHUFFLE` при $P_{\text{AH}} < P_{\text{vendor\_sell}}$
- [ ] При $N < 5$ Z-score снайпинг не выбрасывает ошибок и не дает ложных рекомендаций
- [ ] Тесты в `tests/test_analytics.py` и `tests/test_database.py` проверяют все граничные условия
