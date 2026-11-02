# 01 — Портирование таблиц распыления и Liquidity Shield

**Требования:** R15, R21, R22, R26, R27
**Зависит от:** —
**Зона:** `data/disenchant_tables_era.json` · `src/analytics.py` · `tests/test_analytics.py`
**Волна:** 1
**Ревью:** да
**Модель:** сильная

## Что должно заработать
1. Создать `data/disenchant_tables_era.json` на основе данных из `DisenchantResults.lua` (Classic Era ilvl 5-65, необычное оружие ilvl 16-30 дает Greater Magic Essence 10939).
2. Реализовать `load_disenchant_tables()` и `compute_liquidity_score(item_id, history, item_type)` в `src/analytics.py`.
3. Модифицировать `get_strong_buys()` в `src/analytics.py` для учета штрафа неликвидности $((1-L)/L) \cdot \text{deposit} + (1-L) \cdot 0.10 P_{\text{curr}}$ и маркировки $L < 0.20$ как `ILLIQUID_TRAP`.
4. Написать честные тесты в `tests/test_analytics.py` (роль Агент-Тестер).
