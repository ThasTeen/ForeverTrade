# 05 — Рекурсивный DAG Crafting Engine

**Требования:** R23, R24, R26, R27
**Зависит от:** 04
**Зона:** `src/analytics.py` · `src/dashboard.py` · `tests/test_analytics.py` · `tests/test_dashboard.py`
**Волна:** 5
**Ревью:** да
**Модель:** сильная

## Что должно заработать
1. Реализовать `resolve_effective_material_cost()` в `src/analytics.py` с защитой от циклов (посещенные рецепты) и выбором минимума между ценой вендора, AH и промежуточного крафта.
2. Обновить `get_profitable_crafts()` для использования эффективной себестоимости материалов по DAG.
3. Отрисовать в инспекторе дашборда (`src/dashboard.py`) древовидную структуру промежуточных крафтов с цветовой индикацией источника сырья.
4. Написать тесты в `tests/test_analytics.py` и `tests/test_dashboard.py`.
