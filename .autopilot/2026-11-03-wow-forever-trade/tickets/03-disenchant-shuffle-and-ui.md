# 03 — Disenchant Shuffle в ядре и UI Goblin Terminal

**Требования:** R16, R17, R26, R27
**Зависит от:** 02
**Зона:** `src/analytics.py` · `src/dashboard.py` · `tests/test_analytics.py` · `tests/test_dashboard.py`
**Волна:** 3
**Ревью:** да
**Модель:** сильная

## Что должно заработать
1. Реализовать `calculate_disenchant_value()` и `get_disenchant_arbitrage()` в `src/analytics.py`.
2. Добавить вкладку `✨ Дизэнчант` в `src/dashboard.py` с фильтрами и таблицей возможностей.
3. Добавить в инспектор предмета карточку распыления с выпадающими реагентами, вероятностями и ценами на аукционе.
4. Написать тесты в `tests/test_analytics.py` и `tests/test_dashboard.py`.
