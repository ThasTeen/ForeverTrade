# 04 — Робастный DBMarket (Hampel Filter + EWMA) и таблица метрик

**Требования:** R18, R19, R20, R26, R27
**Зависит от:** 03
**Зона:** `src/database.py` · `src/analytics.py` · `tests/test_database.py` · `tests/test_analytics.py`
**Волна:** 4
**Ревью:** да
**Модель:** сильная

## Что должно заработать
1. Создать таблицу `item_market_metrics` в `init_db()` в `src/database.py`.
2. Реализовать `compute_robust_market_metrics()` в `src/analytics.py` с робастной $\sigma_{\text{MAD}} = 1.4826 \times \text{MAD}$ и EWMA с полураспадом 3.5 дня.
3. Интегрировать робастные метрики в расчет `dbmarket` и Z-score.
4. Написать тесты в `tests/test_database.py` и `tests/test_analytics.py`.
