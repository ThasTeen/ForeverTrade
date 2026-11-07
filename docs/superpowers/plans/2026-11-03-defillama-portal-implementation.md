# Forever Trade DeFiLlama Portal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Перенести выбранный визуальный стиль DeFiLlama в автономный Forever Trade Portal, сохранив все торговые данные и интерактивные функции.

**Architecture:** Редизайн выполняется внутри существующего `build_standalone_dashboard()` без нового frontend-стека. Сначала меняются shell и навигация, затем компоненты и Plotly; существующие ID, `ITEM_STORE`, pane и JS-обработчики остаются контрактом между HTML и поведением.

**Tech Stack:** Python 3, SQLite, standalone HTML/CSS/JavaScript, Plotly, Wowhead Forever tooltips, pytest, Kimi Code built-in browser.

## Global Constraints

- Источник истины: `docs/superpowers/specs/2026-11-03-defillama-portal-design.md`.
- Один автономный `portal.html`, открытие через `file://`; никаких React, Tailwind, сборщиков, web-fonts, внешних icon-библиотек и новых зависимостей.
- Не менять аналитику, торговые пороги, комиссии, цены, ROI, Z-score, ETL и схему SQLite.
- Сохранить `ITEM_STORE`, `STRONG_BUY_ITEM_IDS`, существующие ID/классы/JS-функции, RU/EN и ключи `ft_active_tab`, `ft_search`, `ft_lang`, `ft_selected_item`.
- Сохранить Wowhead Forever tooltips, Plotly, калькулятор, crafting DAG, Disenchant, поиск, Ctrl/Cmd+K, сортировку, copy, Shopping List и meta refresh 30 секунд.
- На десктопе сохранить `100vh` и внутреннюю прокрутку; на экранах уже 1280 px разрешить нормальный документный поток по правилам спецификации.
- Не редактировать `portal.html` вручную: меняется генератор, затем артефакт генерируется заново.
- Не выполнять `git commit` без отдельного прямого запроса пользователя; шаги проверки в плане не заменяются коммитами.
- Не запускать внешние публикации или изменения серверов; вся работа локальная.

## File Map

| Файл | Изменение |
|---|---|
| `src/dashboard.py:124-320` | Привести Python Plotly theme к общим токенам |
| `src/dashboard.py:583-2800` | Shell, sidebar, topbar, CSS-компоненты, responsive rules и JS chart theme |
| `tests/test_dashboard.py:1014-1807` | Обновить layout contract, добавить проверки tokens/a11y/chart, сохранить функциональные тесты |
| `docs/architecture.md` | После реализации обновить описание shell и навигации |
| `docs/adr/0012-goblin-terminal-ui-and-vendor-ceiling.md` | Отметить DeFiLlama shell как развитие UI, не меняя Vendor Ceiling |
| `AGENTS.md` | Обновить устаревшее описание `src/dashboard.py` после реализации |
| `.autopilot/PROGRESS.md` | Зафиксировать правку по действующему проектному workflow |
| `portal.html` | Только сгенерировать штатным кодом |
| `tmp/` | Временные baseline/визуальные артефакты; не включать в поставку |

---

### Task 1: Зафиксировать работающий baseline

**Files:**
- Read: `src/dashboard.py`
- Read: `tests/test_dashboard.py`
- Generate: `portal.html`
- Temporary: `tmp/defillama-baseline.png`

**Interfaces:**
- Consumes: текущий `build_standalone_dashboard(conn, output_path="portal.html", ai_advice=None, realm=None, has_bartering=False, has_master_chef=False, max_age_hours=None)`.
- Produces: задокументированный baseline и список текущих проверок; никакого нового runtime API.

- [ ] **Step 1: Запустить тесты генератора до изменений**

Run:

```bash
python -m pytest tests/test_dashboard.py
```

Expected: PASS. Записать фактическое число passed и любые существующие warnings; не исправлять unrelated failures в рамках редизайна.

- [ ] **Step 2: Сгенерировать текущий реальный портал**

Run:

```bash
python run_watcher.py --run-once --realm "Everlook"
```

Expected: штатный ETL завершается без traceback, `portal.html` обновлён. Если на машине исполнителя нет источника Auctionator, не подменять проверку случайными данными: использовать существующую `market.db` и вызвать генератор напрямую:

```bash
python -c "import sqlite3; from src.dashboard import build_standalone_dashboard; c=sqlite3.connect('market.db'); build_standalone_dashboard(c, output_path='portal.html', realm='Everlook'); c.close()"
```

- [ ] **Step 3: Снять baseline через built-in browser**

Открыть `file:///D:/ai%20project/Forever%20Trade/portal.html`, установить viewport 1440×900, сохранить `tmp/defillama-baseline.png`. Проверить один раз: активный раздел, выбор строки, график, RU/EN, поиск и Shopping List.

Expected: baseline работает; зафиксировать существующие ошибки отдельно, не приписывая их редизайну.

- [ ] **Step 4: Сверить обязательные DOM-контракты**

Перед правками отметить в рабочем чек-листе: `.terminal-layout`, `.terminal-header`, `.summary-cards-row`, `.terminal-main`, `.terminal-left`, `.terminal-right`, `.tab-btn`, `.tab-pane`, `#item-search-input`, `#btn-export-shopping-list`, `#lang-ru`, `#lang-en`, `#inspector-item-name`, `#inspector-chart`, `#deal-calculator`, `#item-modal`, `#toast`.

Expected: все присутствуют; дальнейшие задачи могут перемещать их, но не удалять.

---

### Task 2: Ввести токены и новый shell с левой навигацией

**Files:**
- Modify: `tests/test_dashboard.py:1018-1085`
- Modify: `src/dashboard.py:1052-1793`
- Modify: `src/dashboard.py:1794-2082`

**Interfaces:**
- Consumes: существующие `.tab-btn[data-tab]`, `.tab-pane`, `switchTab(tabId)`, header/summary/main/inspector DOM.
- Produces: `.app-shell`, `.terminal-sidebar`, `.terminal-content`, обновлённые `.terminal-layout` и `.terminal-main`; прежние tab/pane ID остаются без изменений.

- [ ] **Step 1: Заменить старый layout-тест на failing test нового shell**

В `tests/test_dashboard.py` заменить `test_goblin_terminal_ui_layout` на следующий тест, сохранив проверки inspector из старого теста после приведённого блока:

```python
def test_defillama_terminal_shell(populated_dashboard_db, tmp_path):
    out_file = tmp_path / "defillama_terminal_portal.html"
    html = build_standalone_dashboard(
        populated_dashboard_db, output_path=str(out_file), realm="Everlook"
    )

    assert '<div class="terminal-layout app-shell">' in html
    assert '<aside class="terminal-sidebar">' in html
    assert '<div class="terminal-content">' in html
    assert '<header class="terminal-header">' in html
    assert '<nav class="terminal-nav" aria-label="Разделы рынка">' in html
    assert '<div class="summary-cards-row">' in html
    assert '<main class="terminal-main">' in html
    assert '<section class="terminal-left">' in html
    assert '<aside class="terminal-right">' in html

    for token in (
        "--ft-bg: #0b0e14;",
        "--ft-surface: #11161f;",
        "--ft-accent: #6b9dff;",
        "--ft-sidebar-w: 224px;",
    ):
        assert token in html

    layout_match = re.search(r"\.terminal-layout\s*\{([^}]+)\}", html)
    assert layout_match is not None
    assert "height: 100vh;" in layout_match.group(1)
    assert "display: grid;" in layout_match.group(1)
    assert "grid-template-columns: var(--ft-sidebar-w) minmax(0, 1fr);" in layout_match.group(1)

    main_match = re.search(r"\.terminal-main\s*\{([^}]+)\}", html)
    assert main_match is not None
    main_css = main_match.group(1)
    assert "display: grid;" in main_css
    assert "grid-template-columns: minmax(0, 1.65fr) minmax(320px, 0.85fr);" in main_css
    assert "min-height: 0;" in main_css
    assert "overflow: hidden;" in main_css
```

Expected после сохранения: новый тест FAIL на отсутствии `.app-shell` и sidebar; остальные тесты не меняются.

- [ ] **Step 2: Запустить только новый тест и подтвердить красный результат**

Run:

```bash
python -m pytest tests/test_dashboard.py::test_defillama_terminal_shell -v
```

Expected: FAIL с первым отсутствующим shell assertion.

- [ ] **Step 3: Добавить токены и базовый grid**

В начале CSS `build_standalone_dashboard()` вставить полный набор цветов, типографики, spacing, radius и sizes из раздела 4 спецификации. Базовый shell:

```css
.terminal-layout {
    height: 100vh;
    max-height: 100vh;
    display: grid;
    grid-template-columns: var(--ft-sidebar-w) minmax(0, 1fr);
    overflow: hidden;
    color: var(--ft-text);
    background: var(--ft-bg);
    font-family: var(--ft-font-ui);
}

.terminal-sidebar {
    min-height: 0;
    display: flex;
    flex-direction: column;
    padding: var(--ft-space-4) var(--ft-space-3);
    background: var(--ft-sidebar);
    border-right: 1px solid var(--ft-border);
    overflow-y: auto;
}

.terminal-content {
    min-width: 0;
    min-height: 0;
    display: flex;
    flex-direction: column;
}

.terminal-main {
    flex: 1;
    min-height: 0;
    display: grid;
    grid-template-columns: minmax(0, 1.65fr) minmax(320px, 0.85fr);
    gap: var(--ft-space-3);
    padding: 0 var(--ft-space-4) var(--ft-space-4);
    overflow: hidden;
}
```

Внутри f-string все CSS/JS-скобки записываются двойными (`{{` и `}}`), как в окружающем коде.

- [ ] **Step 4: Перестроить DOM без переименования контрактов**

В `src/dashboard.py` выполнить четыре точных перемещения:

1. Заменить открывающий `<div class="terminal-layout">` на `<div class="terminal-layout app-shell">` и сразу внутри добавить sidebar со следующим содержимым:

```html
<aside class="terminal-sidebar">
    <div class="sidebar-brand">
        <span class="brand-title">Forever Trade</span>
        <span class="brand-subtitle">WoW Forever</span>
    </div>
    <nav class="terminal-nav" aria-label="Разделы рынка">
        <button class="tab-btn active" data-tab="price-anomalies" onclick="switchTab('price-anomalies')">
            <span class="tab-label">Strong Buy</span>
            <span class="tab-count" id="strong-buy-count">{len(strong_buys)}</span>
        </button>
        <button class="tab-btn" data-tab="vendor-arbitrage" onclick="switchTab('vendor-arbitrage')">
            <span class="tab-label i18n" data-ru="Арбитраж" data-en="Arbitrage">Арбитраж</span>
            <span class="tab-count">{len(vendor_arbitrage)}</span>
        </button>
        <button class="tab-btn" data-tab="tab-disenchant" onclick="switchTab('tab-disenchant')">
            <span class="tab-label i18n" data-ru="Дизэнчант" data-en="Disenchant">Дизэнчант</span>
            <span class="tab-count">{len(disenchant_arbitrage)}</span>
        </button>
        <button class="tab-btn" data-tab="waylaid-crates" onclick="switchTab('waylaid-crates')">
            <span class="tab-label i18n" data-ru="Крафт (Dual)" data-en="Craft (Dual)">Крафт (Dual)</span>
            <span class="tab-count">{len(crafting_display)}</span>
        </button>
        <button class="tab-btn" data-tab="tab-crates" onclick="switchTab('tab-crates')">
            <span class="tab-label i18n" data-ru="Ящики" data-en="Waylaid Crates">Ящики</span>
            <span class="tab-count">{len(crates_only)}</span>
        </button>
        <button class="tab-btn" data-tab="tab-shuffle" onclick="switchTab('tab-shuffle')">
            <span class="tab-label i18n" data-ru="Вендор-шаффл" data-en="Vendor Shuffle">Вендор-шаффл</span>
            <span class="tab-count">{len(vendor_arbitrage)}</span>
        </button>
        <button class="tab-btn" data-tab="charts-section" onclick="switchTab('charts-section')">
            <span class="tab-label i18n" data-ru="Обзор рынка" data-en="Market Overview">Обзор рынка</span>
        </button>
    </nav>
    <div class="sidebar-status">
        <span class="timestamp-badge timestamp-gen">Generated: {generated_str}</span>
    </div>
</aside>
<div class="terminal-content">
```

2. Удалить старый `<nav class="tabs-bar">` после переноса его семи кнопок. В текущем `<header class="terminal-header">` оставить `.brand-cluster`, `.header-center` и `.header-actions`; из `.brand-cluster` удалить только повторный `.brand-title`, а `{escaped_realm}` и freshness оставить в topbar. `{generated_str}` перенести в sidebar-status. Поиск, Shopping List и языки сохраняют прежние ID, классы, данные и обработчики.
3. Оставить существующие `.summary-cards-row`, `#ai-banner-container`, `.terminal-main`, все семь `.tab-pane` и `.terminal-right` внутри новой `.terminal-content` в текущем порядке. Сами карточки, таблицы и inspector HTML на этом шаге не менять.
4. После закрывающего `</main>` добавить закрывающий `</div>` для `.terminal-content`; внешний `</div>` продолжает закрывать `.terminal-layout`. `#item-modal`, `.compat-hidden` и `#toast` остаются после app-shell в текущем месте.

`tab-crafting`, используемый как inspector crafting card, остаётся отдельным существующим элементом; не создавать второй элемент с этим ID.

- [ ] **Step 5: Запустить shell и весь dashboard suite**

Run:

```bash
python -m pytest tests/test_dashboard.py::test_defillama_terminal_shell -v
python -m pytest tests/test_dashboard.py
```

Expected: PASS. Если упал функциональный тест, восстановить прежний DOM/ID/handler, а не удалять assertion.

---

### Task 3: Оформить sidebar, topbar и элементы управления

**Files:**
- Modify: `tests/test_dashboard.py`
- Modify: `src/dashboard.py` — sidebar/topbar CSS, DOM и `setLanguage()`

**Interfaces:**
- Consumes: `.terminal-nav`, `#item-search-input`, `#btn-export-shopping-list`, `#lang-ru`, `#lang-en`, существующие `setLanguage(lang)` и `copyAuctionatorShoppingList()`.
- Produces: доступные active/focus states и `aria-pressed` для языка; функциональные имена не меняются.

- [ ] **Step 1: Добавить failing tests навигации и controls**

```python
def test_defillama_navigation_and_controls(populated_dashboard_db):
    html = build_standalone_dashboard(populated_dashboard_db, output_path=None, realm="Everlook")

    assert html.count('class="tab-btn') >= 7
    assert 'class="terminal-nav"' in html
    assert 'class="sidebar-brand"' in html
    assert 'aria-hidden="true"' in html
    assert 'id="item-search-input"' in html
    assert 'id="btn-export-shopping-list"' in html
    assert 'onclick="copyAuctionatorShoppingList()"' in html
    assert 'id="lang-ru"' in html
    assert 'id="lang-en"' in html
    assert ".tab-btn:focus-visible" in html
    assert ".tab-btn.active" in html
    assert "btnRu.setAttribute('aria-pressed'" in html
    assert "btnEn.setAttribute('aria-pressed'" in html
```

Run:

```bash
python -m pytest tests/test_dashboard.py::test_defillama_navigation_and_controls -v
```

Expected: FAIL на новых accessibility/style assertions.

- [ ] **Step 2: Добавить компактные стили навигации и controls**

```css
.terminal-nav {
    display: flex;
    flex-direction: column;
    gap: var(--ft-space-1);
}

.tab-btn {
    min-height: 36px;
    display: flex;
    align-items: center;
    gap: var(--ft-space-2);
    padding: var(--ft-space-2) var(--ft-space-3);
    color: var(--ft-text-secondary);
    background: transparent;
    border: 1px solid transparent;
    border-radius: var(--ft-radius-md);
    font: 500 var(--ft-text-md)/var(--ft-leading-ui) var(--ft-font-ui);
    text-align: left;
    cursor: pointer;
    transition: background-color var(--ft-transition), color var(--ft-transition);
}

.tab-btn:hover {
    color: var(--ft-text);
    background: var(--ft-surface-hover);
}

.tab-btn.active {
    color: var(--ft-text);
    background: var(--ft-accent-soft);
    border-left-color: var(--ft-accent);
}

.tab-btn:focus-visible,
button:focus-visible,
input:focus-visible,
select:focus-visible,
a:focus-visible {
    outline: 2px solid var(--ft-focus);
    outline-offset: 2px;
}
```

Добавить перед `.tab-label` в каждой навигационной кнопке inline SVG; сохранить существующий видимый RU/EN label, `data-tab` и `onclick` без изменений. Общий шаблон и пути:

```html
<svg class="nav-icon" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path d="M2 12 6 8 9 10 14 3" />
</svg>
```

| `data-tab` | Содержимое SVG вместо приведённого `path` |
|---|---|
| `price-anomalies` | `<path d="M2 12 6 8 9 10 14 3" /><path d="M10 3h4v4" />` |
| `vendor-arbitrage` | `<path d="M3 5h10M11 3l2 2-2 2M13 11H3M5 9l-2 2 2 2" />` |
| `tab-disenchant` | `<path d="m8 2 1.4 3.6L13 7l-3.6 1.4L8 12 6.6 8.4 3 7l3.6-1.4L8 2Z" />` |
| `waylaid-crates` | `<path d="M3 6h10v8H3zM5 6V3h6v3M6 10h4" />` |
| `tab-crates` | `<path d="m2 5 6-3 6 3-6 3-6-3Zm0 0v7l6 3 6-3V5M8 8v7" />` |
| `tab-shuffle` | `<circle cx="8" cy="8" r="5" /><path d="M6 8h4M8 6v4" />` |
| `charts-section` | `<path d="M3 13V8M8 13V3M13 13V6" />` |

- [ ] **Step 3: Синхронизировать `aria-pressed` в существующей локализации**

В `setLanguage(lang)` после текущего переключения `.active` добавить:

```javascript
if (btnRu) btnRu.setAttribute('aria-pressed', lang === 'ru' ? 'true' : 'false');
if (btnEn) btnEn.setAttribute('aria-pressed', lang === 'en' ? 'true' : 'false');
```

В исходной разметке кнопок задать начальные `aria-pressed="true"` для RU и `aria-pressed="false"` для EN. Не менять существующие `data-ru`/`data-en` строки Shopping List и другие тексты, покрытые тестами.

- [ ] **Step 4: Проверить controls и регрессии**

Run:

```bash
python -m pytest tests/test_dashboard.py::test_defillama_navigation_and_controls -v
python -m pytest tests/test_dashboard.py -k "language or shopping_list or search or defillama"
```

Expected: PASS; поиск, Shopping List и локализация остаются функционально совместимыми.

---

### Task 4: Перевести summary, таблицы и инспектор на компонентную систему

**Files:**
- Modify: `tests/test_dashboard.py`
- Modify: `src/dashboard.py` — CSS summary/table/inspector/modal/toast; при необходимости только class hooks в DOM

**Interfaces:**
- Consumes: существующие `.summary-cards-row`, `.sortable`, `.selected-row`, `.inspector-panel`, `.de-reagent-row`, `.crafting-tree-list`, `.toast`.
- Produces: единые panel/control/table состояния; DOM ID и формат данных не меняются.

- [ ] **Step 1: Добавить failing component contract test**

```python
def test_defillama_component_styles(populated_dashboard_db):
    html = build_standalone_dashboard(populated_dashboard_db, output_path=None, realm="Everlook")

    for selector in (
        ".summary-card",
        ".terminal-left",
        ".terminal-right",
        ".data-table",
        ".data-table thead",
        ".sortable th",
        ".selected-row",
        ".inspector-panel",
        ".deal-calculator",
        ".empty-state",
        ".toast",
    ):
        assert selector in html

    assert "position: sticky;" in html
    assert "font-variant-numeric: tabular-nums;" in html
    assert "background: var(--ft-accent-soft);" in html
    assert "border: 1px solid var(--ft-border);" in html
```

Run:

```bash
python -m pytest tests/test_dashboard.py::test_defillama_component_styles -v
```

Expected: FAIL на отсутствующих selectors/rules; если фактический table class отличается, добавить `.data-table` к существующим table, не удаляя `.sortable`.

- [ ] **Step 2: Применить панели и таблицы**

```css
.summary-card,
.terminal-left,
.terminal-right,
.inspector-panel {
    background: var(--ft-surface);
    border: 1px solid var(--ft-border);
    border-radius: var(--ft-radius-md);
}

.data-table {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--ft-text-sm);
    font-variant-numeric: tabular-nums;
}

.data-table thead {
    position: sticky;
    top: 0;
    z-index: 2;
    background: var(--ft-surface-raised);
}

.data-table th,
.data-table td {
    min-height: var(--ft-row-h);
    padding: var(--ft-space-2) var(--ft-space-3);
    border-bottom: 1px solid var(--ft-border);
}

.data-table td.num,
.data-table th.num,
.money-gold,
.money-silver,
.money-copper {
    font-variant-numeric: tabular-nums;
}

.data-table tbody tr:hover {
    background: var(--ft-surface-hover);
}

.data-table tbody tr.selected-row {
    background: var(--ft-accent-soft);
    box-shadow: inset 2px 0 0 var(--ft-accent);
}
```

Применить `.data-table` ко всем существующим сигнальным таблицам. Для числовых ячеек добавить `.num` без изменения `data-value`, сортировки и содержимого.

- [ ] **Step 3: Уплотнить summary и инспектор**

Summary: grid из четырёх равных колонок, `gap: 12px`, `margin-bottom: 12px`; card padding 12 px, label 11 px, value 20 px/600. Инспектор: `min-height: 0; overflow-y: auto`, секции разделяются `border-bottom`, deal calculator — двухколоночный definition grid.

```css
.summary-cards-row {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: var(--ft-space-3);
    margin: 0 var(--ft-space-4) var(--ft-space-3);
}

.terminal-left,
.terminal-right {
    min-width: 0;
    min-height: 0;
    overflow: hidden;
}

.inspector-panel {
    height: 100%;
    overflow-y: auto;
    border: 0;
    border-radius: 0;
}

.deal-calculator-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: var(--ft-space-2);
}
```

Если текущий калькулятор уже имеет другой container class, добавить `.deal-calculator-grid` к нему, сохранив `#deal-calculator` и все `#calc-*`.

- [ ] **Step 4: Запустить компонентные и функциональные тесты**

Run:

```bash
python -m pytest tests/test_dashboard.py::test_defillama_component_styles -v
python -m pytest tests/test_dashboard.py
```

Expected: PASS; копирование, Wowhead, таблицы, калькулятор и inspector assertions зелёные.

---

### Task 5: Синхронизировать обе Plotly-темы

**Files:**
- Modify: `tests/test_dashboard.py`
- Modify: `src/dashboard.py:124-320`
- Modify: `src/dashboard.py:2580-2659`

**Interfaces:**
- Consumes: `_build_price_history_chart(conn, realm=None)` и `updateInspectorChart(item, activeName)`.
- Produces: одинаковая dark theme; данные traces, timestamps, breakeven/mean формулы и существующий вызов `Plotly.react(plotContainer, traces, layout, { responsive: true, displayModeBar: false })` остаются прежними.

- [ ] **Step 1: Добавить failing chart palette test**

```python
def test_defillama_plotly_palette(populated_dashboard_db):
    html = build_standalone_dashboard(populated_dashboard_db, output_path=None, realm="Everlook")

    assert "paper_bgcolor: '#11161f'" in html
    assert "plot_bgcolor: '#11161f'" in html
    assert "font: { color: '#8b98a9'" in html
    assert "line: { color: '#6b9dff', width: 2 }" in html
    assert "line: { color: '#f85149', width: 1.5, dash: 'dot' }" in html
    assert "line: { color: '#d29922', width: 1.5, dash: 'dash' }" in html
```

Run:

```bash
python -m pytest tests/test_dashboard.py::test_defillama_plotly_palette -v
```

Expected: FAIL на старой палитре.

- [ ] **Step 2: Обновить JS chart layout и линии**

В `updateInspectorChart()` установить:

```javascript
var layout = {{
    template: 'plotly_dark',
    paper_bgcolor: '#11161f',
    plot_bgcolor: '#11161f',
    font: {{ color: '#8b98a9', family: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif", size: 10 }},
    margin: {{ l: 45, r: 15, t: 25, b: 30 }},
    height: 250,
    showlegend: true,
    legend: {{ orientation: 'h', yanchor: 'bottom', y: 1.02, xanchor: 'right', x: 1, font: {{ size: 9 }} }},
    xaxis: {{ gridcolor: '#263142', zerolinecolor: '#35445a' }},
    yaxis: {{ gridcolor: '#263142', zerolinecolor: '#35445a' }}
}};
```

Основная trace line: `#6b9dff`; breakeven: `#f85149`, `dot`; mean: `#d29922`, `dash`. Не менять расчёты `cost`, `breakeven`, `item.mean_price` и условия добавления traces.

- [ ] **Step 3: Обновить Python chart theme**

Во всех traces `_build_price_history_chart()` использовать `#6b9dff` для основной серии и существующие доменные серии только с согласованными semantic colors. В `fig.update_layout()` установить:

```python
fig.update_layout(
    template="plotly_dark",
    paper_bgcolor="#11161f",
    plot_bgcolor="#11161f",
    font={"color": "#8b98a9", "size": 10},
)
fig.update_xaxes(gridcolor="#263142", zerolinecolor="#35445a")
fig.update_yaxes(gridcolor="#263142", zerolinecolor="#35445a")
```

Сохранить текущие title, hover, dimensions и выбор top-8; не переписывать SQL.

- [ ] **Step 4: Проверить palette и chart regressions**

Run:

```bash
python -m pytest tests/test_dashboard.py::test_defillama_plotly_palette -v
python -m pytest tests/test_dashboard.py -k "chart or plotly or inspector"
```

Expected: PASS.

---

### Task 6: Добавить responsive и accessibility финиш

**Files:**
- Modify: `tests/test_dashboard.py`
- Modify: `src/dashboard.py` — конец CSS и существующие controls

**Interfaces:**
- Consumes: shell из Task 2 и компоненты из Tasks 3–5.
- Produces: breakpoints 1280/1024/768 px, reduced motion, мобильный table overflow; никаких новых JS API.

- [ ] **Step 1: Добавить failing responsive/a11y test**

```python
def test_defillama_responsive_and_accessibility(populated_dashboard_db):
    html = build_standalone_dashboard(populated_dashboard_db, output_path=None, realm="Everlook")

    assert "@media (max-width: 1279px)" in html
    assert "@media (max-width: 1023px)" in html
    assert "@media (max-width: 767px)" in html
    assert "@media (prefers-reduced-motion: reduce)" in html
    assert "grid-template-columns: 72px minmax(0, 1fr);" in html
    assert "grid-template-columns: 1fr;" in html
    assert "overflow-x: auto;" in html
    assert "outline: 2px solid var(--ft-focus);" in html
```

Run:

```bash
python -m pytest tests/test_dashboard.py::test_defillama_responsive_and_accessibility -v
```

Expected: FAIL до добавления media queries.

- [ ] **Step 2: Добавить точные responsive rules**

```css
@media (max-width: 1279px) {
    .terminal-layout {
        grid-template-columns: 72px minmax(0, 1fr);
    }
    .terminal-nav .tab-label,
    .sidebar-brand .brand-subtitle {
        position: absolute;
        width: 1px;
        height: 1px;
        overflow: hidden;
        clip-path: inset(50%);
    }
    .terminal-main {
        grid-template-columns: 1fr;
        overflow-y: auto;
    }
    .terminal-left,
    .terminal-right {
        min-height: 360px;
    }
}

@media (max-width: 1023px) {
    .terminal-layout {
        height: auto;
        max-height: none;
        display: block;
        overflow: visible;
    }
    .terminal-sidebar {
        padding: var(--ft-space-2) var(--ft-space-3);
        border-right: 0;
        border-bottom: 1px solid var(--ft-border);
        overflow: visible;
    }
    .terminal-nav {
        flex-direction: row;
        overflow-x: auto;
    }
    .terminal-nav .tab-label {
        position: static;
        width: auto;
        height: auto;
        clip-path: none;
    }
    .summary-cards-row {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .terminal-main {
        overflow: visible;
    }
}

@media (max-width: 767px) {
    .terminal-header {
        min-height: var(--ft-topbar-h);
        height: auto;
        flex-wrap: wrap;
        padding: var(--ft-space-2) var(--ft-space-3);
    }
    .header-center {
        order: 3;
        min-width: 100%;
    }
    .summary-cards-row {
        margin: var(--ft-space-3);
    }
    .terminal-main {
        padding: 0 var(--ft-space-3) var(--ft-space-3);
    }
    .table-scroll {
        overflow-x: auto;
        overscroll-behavior-inline: contain;
    }
}

@media (prefers-reduced-motion: reduce) {
    *,
    *::before,
    *::after {
        scroll-behavior: auto !important;
        transition-duration: 0.01ms !important;
        animation-duration: 0.01ms !important;
        animation-iteration-count: 1 !important;
    }
}
```

Все существующие таблицы поместить в `.table-scroll`, не меняя `tbody tr` поисковые селекторы. В 1279 px labels визуально скрыты, но остаются в accessibility tree; не использовать `display: none` для них.

- [ ] **Step 3: Проверить клавиатурные и семантические состояния**

Убедиться, что навигация остаётся `<button>`, а ссылки имеют href. В существующем `document.querySelectorAll("table.sortable").forEach(...)` внутри `headers.forEach(...)` добавить перед текущим click listener:

```javascript
header.setAttribute("tabindex", "0");
header.setAttribute("role", "button");
header.addEventListener("keydown", function(e) {{
    if (e.key === "Enter" || e.key === " ") {{
        e.preventDefault();
        header.click();
    }}
}});
```

Это вызывает существующий click handler и не дублирует алгоритм сортировки. В тест `test_defillama_responsive_and_accessibility` добавить:

```python
assert 'header.setAttribute("tabindex", "0")' in html
assert 'header.addEventListener("keydown"' in html
assert 'header.click();' in html
```

Expected: Tab доходит до поиска, языка, Shopping List, семи разделов, sortable headers, controls и Wowhead-ссылок; Enter/Space меняют сортировку; focus не скрыт под sticky header.

- [ ] **Step 4: Запустить тесты**

Run:

```bash
python -m pytest tests/test_dashboard.py::test_defillama_responsive_and_accessibility -v
python -m pytest tests/test_dashboard.py
```

Expected: PASS.

---

### Task 7: Полная проверка и обновление проектной документации

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/adr/0012-goblin-terminal-ui-and-vendor-ceiling.md`
- Modify: `AGENTS.md`
- Modify: `.autopilot/PROGRESS.md`
- Generate: `portal.html`

**Interfaces:**
- Consumes: завершённые Tasks 2–6.
- Produces: актуальная документация и реальный standalone artifact; runtime API не меняется.

- [ ] **Step 1: Запустить полный набор тестов**

Run:

```bash
python -m pytest
```

Expected: PASS, 0 failed. Зафиксировать фактическое количество тестов; не утверждать заранее число 149 или 196.

- [ ] **Step 2: Обновить описания старого UI**

В `docs/architecture.md`, `AGENTS.md` и ADR 0012 заменить устаревшее описание верхних tabs/фиксированного 65/35 flex-shell на: левая навигация, CSS grid 65/35 на десктопе, токены DeFiLlama, внутренняя прокрутка и responsive breakpoints. Не менять описание Vendor Floor/Ceiling, торговых моделей и AI fallback.

В `.autopilot/PROGRESS.md` добавить запись по правилам проекта: дата, что изменено и почему; договорённость об одноэкранном терминале сохранить, уточнив, что на десктопе используется sidebar.

- [ ] **Step 3: Сгенерировать итоговый реальный портал**

Run:

```bash
python run_watcher.py --run-once --realm "Everlook"
```

Expected: `portal.html` содержит `.app-shell`, sidebar и новые токены; генератор не выбрасывает ошибок f-string/JS.

- [ ] **Step 4: Проверить состав изменений**

Run:

```bash
git status --short
```

Expected: только запланированные исходники/тесты/docs и сгенерированный `portal.html`, если он отслеживается. Удалить временные логи/выводы, созданные проверками; baseline в `tmp/` не включать в поставку. Не выполнять commit.

---

### Task 8: Браузерная приёмка итогового `portal.html`

**Files:**
- Verify: `portal.html`
- Temporary: `tmp/defillama-1440.png`, `tmp/defillama-1024.png`, `tmp/defillama-768.png`, `tmp/defillama-375.png`

**Interfaces:**
- Consumes: итоговый файл из Task 7.
- Produces: подтверждение пользовательских сценариев; никаких изменений данных.

- [ ] **Step 1: Проверить десктопный shell**

Открыть именно `file:///D:/ai%20project/Forever%20Trade/portal.html` в built-in browser, viewport 1440×900.

Expected: sidebar 224 px, topbar 56 px, четыре summary, таблица и инспектор видны одновременно; body не прокручивается; длинные таблица/инспектор прокручиваются внутри; нет горизонтального overflow.

- [ ] **Step 2: Пройти все функциональные сценарии**

В браузере выполнить:

1. Открыть каждый из семи разделов через sidebar.
2. Ввести поисковый запрос, очистить его, нажать Ctrl+K.
3. Отсортировать числовую и текстовую колонку в обе стороны.
4. Выбрать строку, проверить selected-row, график, калькулятор и Wowhead-ссылку.
5. На подходящих данных проверить Disenchant и crafting DAG; если реальный dataset их не содержит, проверить отдельно на тестовом fixture через временный HTML, не объявляя пустой реальный случай успешным.
6. Переключить RU→EN→RU; убедиться, что выбранный предмет и раздел не сброшены.
7. Скопировать название и Shopping List; проверить toast и строку `ForeverTrade^` через доступный browser clipboard flow.
8. Перезагрузить страницу; проверить четыре `localStorage` состояния.
9. Проверить наличие `<meta http-equiv="refresh" content="30">`; не ждать многократных циклов, если один reload уже подтверждает сохранение состояния.

Expected: все сценарии работают, console не содержит новых ошибок; отсутствие сети для Wowhead/Plotly фиксируется отдельно от ошибок редизайна.

- [ ] **Step 3: Проверить responsive matrix**

Снять скриншоты 1024×768, 768×1024 и 375×812.

Expected:
- 1024: compact sidebar 72 px, inspector под таблицей;
- 768: горизонтальная навигация, summary 2×2;
- 375: topbar переносится, нет overflow всей страницы, таблица прокручивается локально;
- labels доступны, controls не перекрыты, цены не обрезаны.

- [ ] **Step 4: Проверить доступность и финальную консоль**

Пройти основной сценарий только клавиатурой; включить reduced motion в browser emulation; проверить focus-visible и текстовые обозначения прибыли/риска/freshness.

Expected: видимый focus, отсутствие keyboard trap, motion отключён, цвет не является единственным носителем состояния. Console без новых JavaScript errors.

- [ ] **Step 5: Зафиксировать результат без коммита**

Сопоставить итог со всеми Success Criteria спецификации. Сообщить пользователю фактические результаты pytest и браузерных сценариев, приложить финальный 1440 px screenshot и перечислить только реально оставшиеся ограничения. Не объявлять завершением этап, который не был проверен.
