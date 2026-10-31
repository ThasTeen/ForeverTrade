# Границы модулей и контракты (interfaces.md)

## Правила проекта (Ponytail & Engineering Constraints)
1. **Ponytail по умолчанию:** Минимальный достаточный рабочий diff. Никаких избыточных фабрик, метаклассов и декораторов.
2. **Безопасность и целостность данных:** Все операции с SQLite транзакционны. Парсинг Lua выполняется безопасно через `luadata` без `eval()`. При повреждении файла — автоматический fallback на `Auctionator.lua.bak`.
3. **Разделение ролей:** Агент-Кодер пишет только production-код в `src/`. Агент-Тестер пишет только тесты в `tests/`.
4. **Запрет на бумажные тесты:** Тесты проверяют только реальное выполнение функций и API с валидацией возвращаемых данных и состояния БД. Запрещены регулярные выражения по исходным файлам.

## Границы, решённые в спецификации

### 1. Модуль базы данных (`src/database.py`)
- **Ответственность:** Схема SQLite, инициализация таблиц, индексов, наполнение базовыми предметами Era 1.15 (`data/items_era_1_15.json`), оверрайдами Forever (`forever_overrides.json`), графом рецептов (5 профессий, включая Кулинарию), и очистка старых снимков.
- **Публичный интерфейс:**
  - `init_db(db_path: str = "market.db") -> sqlite3.Connection`
  - `load_catalog(conn: sqlite3.Connection, era_catalog_path: str = "data/items_era_1_15.json", overrides_path: str = "forever_overrides.json") -> int`
  - `load_recipes(conn: sqlite3.Connection) -> int`
  - `prune_snapshots(conn: sqlite3.Connection, max_days: int = 30) -> int`
  - `get_item(conn: sqlite3.Connection, item_id: int) -> Optional[dict]`
- **Скрывает:** DDL строки, детали SQL транзакций.

### 2. Модуль мониторинга и ETL (`src/etl.py`)
- **Ответственность:** Конфигурируемый мониторинг пути к файлу `Auctionator.lua`, парсинг Lua-файла (с fallback на `.bak`), нормализация сигнатур `itemId`, дедупликация (окно 10 минут) и батч-инжест в `price_snapshots`.
- **Публичный интерфейс:**
  - `parse_auctionator_data(file_path: str) -> dict`
  - `normalize_item_id(raw_id: str) -> Optional[int]`
  - `ingest_price_snapshot(conn: sqlite3.Connection, raw_data: dict, realm: str, dedupe_window_minutes: int = 10) -> int`
  - `start_watcher(watch_path: str, db_path: str, realm: str)` (на базе `watchdog`)
- **Скрывает:** Работу потоков watchdog, десериализацию luadata, форматирование `watcher.log`.

### 3. Модуль аналитического ядра (`src/analytics.py`)
- **Ответственность:** Расчет вендорного арбитража, маржинальности крафта (с учетом 5% Bartering, 1.1x Master Chef для кулинарии, 5% аукционной комиссии) и Z-score ценовых аномалий с защитой от деления на 0.
- **Публичный интерфейс:**
  - `get_vendor_arbitrage(conn: sqlite3.Connection, min_profit_copper: int = 50) -> list[dict]`
  - `get_crafting_margins(conn: sqlite3.Connection, has_bartering: bool = True, has_master_chef: bool = True, min_roi: float = 0.25) -> list[dict]`
  - `get_zscore_anomalies(conn: sqlite3.Connection, window_size: int = 15, z_buy_threshold: float = -1.8, z_sell_threshold: float = 1.6) -> list[dict]`
- **Скрывает:** Внутренние SQL агрегации и вычисления стандартного отклонения.

### 4. Модуль дашборда и оповещений (`src/dashboard.py`)
- **Ответственность:** Генерация автономного HTML-дашборда с 3 блоками сигналов («Вендорный арбитраж», «Крафт под Waylaid Crates», «Аномальные сбросы цен»), графиками динамики цен на сырье и расходники Plotly.js и форматированием валют (g/s/c). Локальные уведомления об арбитраже > 1g.
- **Публичный интерфейс:**
  - `format_currency(copper: int) -> str`
  - `build_standalone_dashboard(conn: sqlite3.Connection, output_path: str = "portal.html", ai_advice: Optional[dict] = None) -> str`
  - `notify_high_value_arbitrage(arbitrage_items: list[dict], threshold_copper: int = 10000) -> list[dict]`
- **Скрывает:** Шаблонизацию HTML, сборку графиков Plotly.js в JSON, вызов системных нотификаций.

### 5. Модуль AI-ассистента (`src/ai_advisor.py`)
- **Ответственность:** Агрегация компактного среза рыночных данных (топ-3 вендорных, топ-3 маржинальных ящиков, топ-3 ценовых просадок) и формирование структурированного чек-листа сессии через Antigravity Manager / Gemini 3.8.
- **Публичный интерфейс:**
  - `build_compact_context(conn: sqlite3.Connection) -> dict`
  - `generate_market_advice(context: dict, client: Optional[Any] = None) -> dict`
  - `class AntigravityManagerClient`
- **Скрывает:** Промптинг LLM, парсинг JSON ответов, эвристический fallback при отсутствии соединения.

## Тестовые швы
- `tests/test_database.py`: проверка создания DDL, составных индексов, загрузки каталога, оверрайдов, рецептов и очистки снимков старше 30 дней.
- `tests/test_etl.py`: проверка безопасного парсинга Lua, fallback на `.bak`, нормализации `item_id`, дедупликации и пакетной вставки.
- `tests/test_analytics.py`: математическая верификация формул вендорного арбитража, маржи крафта (Bartering + Master Chef для кулинарии) и Z-score.
- `tests/test_dashboard.py`: генерация standalone HTML с тремя именованными блоками, графиками сырья, форматированием монет и нотификациями > 1g.
- `tests/test_ai_advisor.py`: генерация компактного JSON-контекста и парсинг рекомендаций чек-листа.
