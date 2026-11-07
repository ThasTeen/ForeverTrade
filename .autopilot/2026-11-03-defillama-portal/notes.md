# Исследование текущего портала

## Где живут UI и Plotly

- Весь портал собирается в `src/dashboard.py`, отдельных HTML/CSS/JS-шаблонов и frontend-сборки нет.
- `build_standalone_dashboard()` (`src/dashboard.py:583`) собирает один большой f-string: CSS — `src/dashboard.py:1051-1791`, HTML — `src/dashboard.py:1793-2127`, клиентский JS — `src/dashboard.py:2130-2788`.
- Python Plotly overview: `_build_price_history_chart()` (`src/dashboard.py:124`), тема `plotly_dark` и цвета — `src/dashboard.py:259-286`; `fig.to_html(full_html=False, include_plotlyjs="cdn")` — `src/dashboard.py:288`.
- График инспектора строится отдельно в JS `updateInspectorChart()` (`src/dashboard.py:2580`), его layout/theme — `src/dashboard.py:2643-2658`.
- `requirements.txt` содержит незакреплённые по версии `pytest`, `luadata`, `watchdog`, `pandas`, `numpy`, `plotly`.

## DOM/JS-контракты, которые проверяются тестами или используются обработчиками

- Layout: `.terminal-layout`, `.terminal-header`, `.summary-cards-row`, `.terminal-main`, `.terminal-left` (65%), `.terminal-right` (35%), `.inspector-panel`; одноэкранный `100vh` с `overflow: hidden`.
- Tabs: `#price-anomalies`, `#vendor-arbitrage`, `#tab-disenchant`, `#waylaid-crates`, `#tab-crates`, `#tab-shuffle`, `#charts-section`; классы `.tab-btn`, `.tab-pane`, `.tab-count`, `table.sortable`, `#strong-buy-count`, `#overview-chart-container`. Активный дефолт — `#price-anomalies`.
- Header/state: `#item-search-input`, `#btn-export-shopping-list`, `#lang-ru`, `#lang-en`, `#ai-banner-container`, `#ai-advisor-banner`, `#toast`.
- Inspector: `#inspector-item-name`, `#inspector-wowhead-link`, `#inspector-copy-btn`, `#inspector-chart`, `#deal-calculator`, `#calc-cost`, `#calc-breakeven`, `#calc-ah-cut`, `#calc-profit`, `#calc-roi`.
- Detail cards: `#disenchant-card`, `#de-card-expected-val`, `#de-reagents-list`, `#tab-crafting`, `#crafting-tree`.
- Row/item contracts: `.clickable-row`, `.selected-row`, `.item-cell-wrap`, `.item-name`, `.copy-btn`, `.graph-btn`, `.money-gold/.money-silver/.money-copper`, `.profit-cell`, `.roi-cell`; атрибуты `data-item-id`, `data-value`, `data-tab`, `data-ru/data-en`, `data-*-placeholder`, Wowhead `data-wowhead="domain=forever"`.
- Глобальные данные: `ITEM_STORE`, `STRONG_BUY_ITEM_IDS`; localStorage keys `ft_active_tab`, `ft_search`, `ft_lang`, `ft_selected_item`.
- JS functions: `setLanguage`, `getStoredLang`, `setStoredLang`, `switchTab`, `selectInspectorItem`, `updateInspectorChart`, `onItemSearch`, `onRowClick`, `openItemModal`, `closeItemModal`, `onModalBackdropClick`, `copyItemName`, `copyTextToClipboard`, `fallbackCopy`, `showToast`, `copyAuctionatorShoppingList`, `filterCategory`, `applyItemSelectFilter`, `onSelectDropdownItem`, `renderCraftingTree`, `toggleAiBanner`, `formatCurrencyHtml`, `formatCurrencyText`.
- `.compat-hidden` (`src/dashboard.py:2084-2103`) содержит старые category controls и `#item-select`; `#item-modal` с `#modal-item-name`, `#modal-copy-btn`, `#modal-stat-*`, `#modal-plot-container` сохранён ради тестовой совместимости (`src/dashboard.py:2105-2124`). Их нельзя считать просто мёртвым кодом без пересмотра тестов.
- Тесты проверяют многие имена и даже буквальные JS-фрагменты, поэтому переименование функций/ID или изменение форматов вызовов ломает контракт.

## Как генерируется `portal.html`

- `run_watcher.py:main()` инициализирует SQLite, загружает каталог/рецепты, делает initial ETL, рассчитывает аналитику и AI-совет, затем вызывает `_build_dashboard()` (`run_watcher.py:52-88`, вызов — `run_watcher.py:196-205`).
- `_build_dashboard()` передаёт `output_path`, `ai_advice`, `realm`, `has_bartering`, `has_master_chef`, `max_age_hours`; CLI defaults: `--dashboard-out portal.html`, `--max-age-hours 48.0`.
- После каждого нового снапшота watcher worker повторяет расчёт и пересобирает тот же файл (`run_watcher.py:231-290`). Разовый запуск: `python run_watcher.py --run-once --realm "Everlook"`.
- `build_standalone_dashboard()` получает сигналы из analytics, агрегирует `ITEM_STORE`, строит overview/AI/строки таблиц, пишет UTF-8 HTML в `output_path` и возвращает ту же строку (`src/dashboard.py:2793-2800`). Портал открывается как статический `file://`, веб-сервер не нужен.

## Тесты и текущие fixtures

- Полный набор: `python -m pytest`; только портал: `python -m pytest tests/test_dashboard.py`.
- `memory_db` создаёт чистую `init_db(":memory:")`; `populated_dashboard_db` добавляет vendor items 101/102, craft 201 + reagent 202 + crate 301, Peacebloom 401 с 16 снимками и текущим ценовым падением (`tests/test_dashboard.py:37-111`).
- Остальные тесты используют `tmp_path` для реальных HTML-файлов, отдельные in-memory наборы и `monkeypatch`/`patch` analytics-функций.
- `test_goblin_terminal_ui_layout` (`tests/test_dashboard.py:1018`) проверяет `100vh`, flex/min-height/overflow, 65/35, DOM inspector и calculator. Другие contract-тесты покрывают tabs, modal, filters, i18n, copy/toast, Wowhead, localStorage, shopping list, DAG/disenchant, XSS, realm filtering и decimation до 300 точек.
- Это преимущественно string/regex/HTML-data assertions, а не реальный браузерный visual/layout regression: геометрия, console errors и runtime Plotly/clipboard в браузере здесь не исполняются.

## Ограничения редизайна

- Весь документ — f-string: литеральные CSS/JS `{` и `}` должны быть записаны как `{{`/`}}`; одинарные фигурные скобки интерполируются Python. Нельзя вставлять обычный JS/CSS без экранирования.
- «Standalone» означает один HTML без приложения/сервера, но не полную offline-автономность: Wowhead tooltip script и Plotly CDN требуют сеть. Inline CSS/JS и сериализованные данные находятся в самом файле.
- `<meta http-equiv="refresh" content="30">` перезагружает файл; состояние восстанавливается через localStorage в `DOMContentLoaded`.
- Для защиты от script breakout/XSS имена проходят `html.escape`, `ITEM_STORE` экранирует `<`/`</`, а overview Plotly HTML отдельно обрабатывает `</` (`src/dashboard.py:288-290`, `src/dashboard.py:1034-1038`). При редизайне надо сохранить безопасную сериализацию.
- В `carried.md` изменений после прошлой сборки нет; закреплённые решения включают 100vh Goblin Terminal, отдельный `#inspector-chart`, `.copy-btn`, RU/EN, Shopping List и localStorage.

Тесты и генерация портала в рамках этого исследования не запускались; продакшен-код и тесты не изменялись.
