# Границы модулей и контракты (interfaces.md)

## Правила проекта (Ponytail & Engineering Constraints)
1. **Ponytail по умолчанию:** Минимальный достаточный рабочий diff. Никаких избыточных фабрик, метаклассов и декораторов.
2. **Безопасность и целостность данных:** Все операции с SQLite транзакционны. Парсинг Lua выполняется безопасно через `luadata` без `eval()`. При повреждении файла — автоматический fallback на `Auctionator.lua.bak`.
3. **Разделение ролей:** Агент-Кодер пишет только production-код в `src/`. Агент-Тестер пишет только тесты в `tests/`.
4. **Запрет на бумажные тесты:** Тесты проверяют только реальное выполнение функций и API с валидацией возвращаемых данных и состояния БД. Запрещены регулярные выражения по исходным файлам.

## Границы, решённые в спецификации

### 1. Модуль базы данных (`src/database.py`)
- **Ответственность:** Схема SQLite, первичная инициализация структуры (`init_schema`), фабрика соединений с таймаутами и WAL (`get_connection`), наполнение предметами Era 1.15 и оверрайдами Forever через честный UPSERT (`ON CONFLICT DO UPDATE`), загрузка рецептов и батчевая очистка устаревших снимков (`prune_snapshots`).
- **Публичный интерфейс:**
  - `init_schema(conn: sqlite3.Connection) -> None`
  - `get_connection(db_path: str = "market.db") -> sqlite3.Connection`
  - `init_db(db_path: str = "market.db") -> sqlite3.Connection`
  - `load_catalog(conn: sqlite3.Connection, era_catalog_path: str = "data/items_era_1_15.json", overrides_path: str = "forever_overrides.json") -> int`
  - `load_recipes(conn: sqlite3.Connection) -> int`
  - `prune_snapshots(conn: sqlite3.Connection, max_days: int = 30, batch_size: int = 5000) -> int`
  - `get_item(conn: sqlite3.Connection, item_id: int) -> Optional[dict]`
- **Скрывает:** DDL строки, детали SQL транзакций.

### 2. Модуль мониторинга и ETL (`src/etl.py`)
- **Ответственность:** Создание каталога SavedVariables при отсутствии, trailing-edge дебаунс (500 мс) с проверкой стабильности размера файла `Auctionator.lua`, парсинг цен из поля `'m'` (Auctionator v341), поддержка экипировки `gr:itemId:suffix`, fallback на `.bak` со штампом времени `mtime` файла (отсечение старше 2 часов), защита от `bool` цен, дедупликация и батч-инжест в `price_snapshots`.
- **Публичный интерфейс:**
  - `parse_auctionator_data(file_path: str) -> dict`
  - `normalize_item_id(raw_id: str) -> Optional[int]`
  - `ingest_price_snapshot(conn: sqlite3.Connection, raw_data: dict, realm: str, dedupe_window_minutes: int = 10, source_mtime: Optional[int] = None) -> int`
  - `start_watcher(watch_path: str, db_path: str, realm: str, on_snapshot_callback: Optional[Callable] = None)`
- **Скрывает:** Работу потоков watchdog, десериализацию luadata, форматирование `watcher.log`.

### 3. Модуль аналитического ядра (`src/analytics.py`)
- **Ответственность:** Расчет вендорного арбитража, маржинальности крафта (с белым списком вендорных реагентов `VENDOR_REAGENTS_WHITELIST`, вычетом AH депозита, разделением AH-комплектов и сдачи ящиков Waylaid Crates) и Z-score ценовых аномалий с выборкой $N \ge 6$ и несмещенной дисперсией.
- **Публичный интерфейс:**
  - `get_vendor_arbitrage(conn: sqlite3.Connection, min_profit_copper: int = 50, realm: Optional[str] = None) -> list[dict]`
  - `get_crafting_margins(conn: sqlite3.Connection, has_bartering: bool = True, has_master_chef: bool = True, min_roi: float = 0.25, realm: Optional[str] = None) -> list[dict]`
  - `get_zscore_anomalies(conn: sqlite3.Connection, window_size: int = 15, z_buy_threshold: float = -1.8, z_sell_threshold: float = 1.6, min_samples: int = 6, realm: Optional[str] = None) -> list[dict]`
- **Скрывает:** Внутренние SQL агрегации и вычисления стандартного отклонения.

### 4. Модуль дашборда и оповещений (`src/dashboard.py`)
- **Ответственность:** Генерация автономного HTML-дашборда с 3 блоками сигналов («Вендорный арбитраж», «Крафт под Waylaid Crates», «Аномальные сбросы цен»), защита от XSS через экранирование `html.escape()` и сериализации Plotly, персональный интерактивный график цены для каждого предмета по клику (с историей, средней ценой и Z-score) вместо перегруженного общего графика, мгновенный клиентский переключатель языка (RU / EN) с локализацией названий предметов и интерфейса, кнопка быстрого копирования названия предмета (1-click copy) для поиска на внутриигровом аукционе, безопасное форматирование валют (`OverflowError` handling) и корректный рендер шагов AI-баннера.
- **Публичный интерфейс:**
  - `format_currency(copper: Any) -> str`
  - `build_standalone_dashboard(conn: sqlite3.Connection, output_path: str = "portal.html", ai_advice: Optional[dict] = None, realm: Optional[str] = None) -> str`
  - `notify_high_value_arbitrage(arbitrage_items: list[dict], threshold_copper: int = 10000) -> list[dict]`
- **Скрывает:** Шаблонизацию HTML, сборку графиков Plotly.js в JSON, вызов системных нотификаций, логику JS переключения языков и копирования в буфер обмена.

### 5. Модуль AI-ассистента (`src/ai_advisor.py`)
- **Ответственность:** Агрегация компактного среза рыночных данных с санитизацией имен и разметкой `<market_data>`, защищенный запуск CLI `agy` через `stdin` (защита от WinError 206), передача API ключа через HTTP header `x-goog-api-key`, надежный эвристический fallback (`_safe_int`, `_safe_float`).
- **Публичный интерфейс:**
  - `build_compact_context(conn: sqlite3.Connection, realm: Optional[str] = None) -> dict`
  - `generate_market_advice(context: dict, client: Optional[Any] = None) -> dict`
  - `class AntigravityManagerClient`
- **Скрывает:** Промптинг LLM, парсинг JSON ответов, эвристический fallback при отсутствии соединения.

## Тестовые швы
- `tests/test_database.py`: проверка создания DDL, составных индексов, загрузки каталога, оверрайдов, рецептов и очистки снимков старше 30 дней.
- `tests/test_etl.py`: проверка безопасного парсинга Lua, fallback на `.bak`, нормализации `item_id`, дедупликации и пакетной вставки.
- `tests/test_analytics.py`: математическая верификация формул вендорного арбитража, маржи крафта (Bartering + Master Chef для кулинарии) и Z-score.
- `tests/test_dashboard.py`: генерация standalone HTML с тремя именованными блоками, графиками сырья, форматированием монет и нотификациями > 1g.
- `tests/test_ai_advisor.py`: генерация компактного JSON-контекста и парсинг рекомендаций чек-листа.
