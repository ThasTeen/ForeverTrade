# 06 — Responsive и accessibility

**Требования:** R05, R11i, R12i, R13i, G01
**Зависит от:** 05
**Зона:** `src/dashboard.py` · `tests/test_dashboard.py`
**Волна:** 6
**Ревью:** нет
**Модель:** обычная

## Что должно заработать

Desktop сохраняет 100vh; 1024 получает compact sidebar, 768 — горизонтальную навигацию, 375 — поток без overflow страницы. Сортировка доступна с клавиатуры; reduced motion поддержан.

## Из брифа, дословно

> «Останавливаешь работу только после полного закрытия плана».

## Разделы спецификации

§6, §8, §10–12; план Task 6 — источник точных breakpoints, rules и assertions.

## Критерии приёмки

- [ ] Test agent добавил responsive/a11y контракт и доказал FAIL до production-кода.
- [ ] Coder добавил точные breakpoints, local table scrolling, focus и keyboard sorting; существующие поисковые селекторы не изменены.
- [ ] Responsive/a11y и полный dashboard suite проходят; review завершён.
