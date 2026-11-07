# 05 — Единая Plotly-тема

**Требования:** R05, R11i, R12i, G01
**Зависит от:** 04
**Зона:** `src/dashboard.py` · `tests/test_dashboard.py`
**Волна:** 5
**Ревью:** нет
**Модель:** обычная

## Что должно заработать

Python overview и JS inspector charts имеют общую тёмную палитру и semantic colors; торговые series, timestamps, формулы и вызов `Plotly.react` не изменены.

## Из брифа, дословно

> «Останавливаешь работу только после полного закрытия плана».

## Разделы спецификации

§5–6, §8, §11; план Task 5 — источник точных палитр и assertions.

## Критерии приёмки

- [ ] Test agent добавил palette test и зафиксировал FAIL до кода.
- [ ] Coder изменил только тему обеих Plotly-конфигураций, не SQL и не расчёты.
- [ ] Palette и chart/inspector regressions зелёные; review без открытых проблем.
