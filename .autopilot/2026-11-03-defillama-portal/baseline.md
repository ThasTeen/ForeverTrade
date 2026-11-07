# Baseline — Task 1

- `python -m pytest tests/test_dashboard.py` → 40 passed in 3.18s.
- `python run_watcher.py --run-once --realm "Everlook"` → успешно; `portal.html` обновлён. AI использовал fallback после timeout `agy` 15 секунд; генератору это не мешает.
- Реальный набор: Everlook, снимков нет, все счётчики сигналов 0. Поэтому строка, selected-row и график предмета непроверяемы без отдельного fixture; это не засчитано как успешная проверка.
- `tmp/defillama-baseline.png` прочитан: старый верхний tab-bar, summary и пустой инспектор Peacebloom при 1440×900.
- DOM-контракты Task 1 найдены в реальном артефакте: `.terminal-layout`, `.terminal-header`, `.summary-cards-row`, `.terminal-main`, `.terminal-left`, `.terminal-right`, `.tab-btn`, `.tab-pane`, `#item-search-input`, `#btn-export-shopping-list`, `#lang-ru`, `#lang-en`, `#inspector-item-name`, `#inspector-chart`, `#deal-calculator`, `#item-modal`, `#toast`.
- Браузер: RU→EN меняет заголовки и таблицу; поиск `no-such-item` скрывает строку empty-state; вкладка Market Overview открывает overview/empty data. Shopping List click принят браузером, но содержимое clipboard/toast этой проверкой не подтверждено — повторить в Task 8.
- `http://localhost:10459/dashboard.html` открыт и обновлён: видны текущий прогон DeFiLlama Portal, 8 тасков, 14 требований. Никаких новых портов.
- Способ просмотра: `.autopilot/portal.html` → `../portal.html` на существующем сервере, так как built-in browser не принимает `file://`. Runtime standalone-контракт портала не изменяется.
