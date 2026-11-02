# Границы модулей и контракты (interfaces.md)

## Правила проекта (Ponytail & Engineering Constraints)
1. **Ponytail по умолчанию:** Минимальный достаточный рабочий diff. Никаких избыточных фабрик, метаклассов и декораторов.
2. **Безопасность и целостность данных:** Все операции с SQLite транзакционны. Парсинг Lua выполняется безопасно через `luadata` без `eval()`. При повреждении файла — автоматический fallback на `Auctionator.lua.bak`.
3. **Разделение ролей:** Агент-Кодер пишет только production-код в `src/`. Агент-Тестер пишет только тесты в `tests/`.
4. **Запрет на бумажные тесты:** Тесты проверяют только реальное выполнение функций и API с валидацией возвращаемых данных и состояния БД. Запрещены регулярные выражения по исходным файлам.

## Границы, решённые в спецификации

### 1. Модуль базы данных (`src/database.py`)
- **Ответственность:** Схема SQLite, первичная инициализация структуры (`init_schema`), фабрика соединений с таймаутами и WAL (`get_connection`), наполнение предметами Era 1.15 и оверрайдами Forever через честный UPSERT (`ON CONFLICT DO UPDATE`), загрузка рецептов и батчевая очистка устаревших снимков (`prune_snapshots`).
- **Сверка каталога предметов и рецептов (R45):**
  - ID 2450 — Briarthorn (Остротерн), цена вендора 50м, quality 1, item_class 7, subclass 9.
  - ID 2452 — Swiftthistle (Свистоцвет), цена вендора 40м, quality 1, item_class 7, subclass 9.
  - ID 2313 — Medium Armor Kit (Накладки из средней кожи), цена вендора 60м, покупка 250м.
  - ID 2316 — Toughened Leather Gloves (Плотные кожаные перчатки), вендор 137м, quality 2.
  - Рецепт 3004 (*Medium Armor Kit*): crafted_item_id = 2313.
  - Рецепты 4004 (*Lesser Healing Potion*) и 4005 (*Healing Potion*): реагент Briarthorn = 2450.
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
- **Свежесть и защита от мусорных данных (R48):**
  - `parse_auctionator_data` обязательно проставляет `_source_mtime` из mtime файла и `_content_hash` (sha256).
  - При повторном импорте неизменившегося файла (`_content_hash` или идентичный `mtime`) снимки не вставляются повторно при истечении 10 минут.
  - Отклонение невалидных цен: `price <= 0` отсекается на входе, исключая появление нулевых цен в `price_snapshots`.
- **Публичный интерфейс:**
  - `parse_auctionator_data(file_path: str) -> dict`
  - `normalize_item_id(raw_id: str) -> Optional[int]`
  - `ingest_price_snapshot(conn: sqlite3.Connection, raw_data: dict, realm: str, dedupe_window_minutes: int = 10, source_mtime: Optional[int] = None, current_time: Optional[int] = None) -> int`
  - `start_watcher(watch_path: str, db_path: str, realm: str, on_snapshot_callback: Optional[Callable] = None)`
- **Скрывает:** Работу потоков watchdog, десериализацию luadata, форматирование `watcher.log`.

### 3. Модуль аналитического ядра (`src/analytics.py`)
- **Ответственность:** Расчет вендорного арбитража, маржинальности крафта и Z-score аномалий с фильтрацией устаревших данных по TTL (`max_age_hours`).
- **Экономика Waylaid Crates (R46, R47):**
  - Закупка сырья/продукции под ящик стоит 100% рыночной цены: `pack_cost = target_count * p_out` (без ошибочной скидки 5%).
  - Выручка от продажи комплекта на AH: `pack_revenue = target_count * p_out * 0.95`.
  - Прибыль сдачи квеста NPC: `turnin_profit = reward_copper - pack_cost`. Лоты с отрицательной прибылью (`turnin_profit <= 0`) отсекаются.
  - Разделение в отчете: сдача NPC (Quest Turn-in) vs продажа комплекта материалов игрокам на AH.
- **Снайпинг STRONG_BUY с проверкой чистой прибыли (R49):**
  - Расчет ожидаемой выручки: `expected_revenue = mu * 0.95`.
  - Расчет чистой прибыли: `net_profit = expected_revenue - current_price - deposit`.
  - Сигнал `STRONG_BUY` выдается только если $Z \le -1.8$ **И** `net_profit > 0`.
- **Перки персонажа (R50):**
  - Значения по умолчанию: `has_bartering = False`, `has_master_chef = False` (стартовый персонаж не имеет перков).
- **Публичный интерфейс:**
  - `get_vendor_arbitrage(conn: sqlite3.Connection, min_profit_copper: int = 50, realm: Optional[str] = None, max_age_hours: Optional[float] = None) -> list[dict]`
  - `get_crafting_margins(conn: sqlite3.Connection, has_bartering: bool = False, has_master_chef: bool = False, min_roi: float = 0.25, realm: Optional[str] = None, include_ah_deposit: bool = True, max_age_hours: Optional[float] = None) -> list[dict]`
  - `get_zscore_anomalies(conn: sqlite3.Connection, window_size: int = 15, z_buy_threshold: float = -1.8, z_sell_threshold: float = 1.6, min_samples: int = 6, realm: Optional[str] = None, max_age_hours: Optional[float] = None, min_profit_copper: int = 1) -> list[dict]`
- **Скрывает:** Внутренние SQL агрегации и вычисления стандартного отклонения.

### 4. Модуль дашборда и оповещений (`src/dashboard.py`)
- **Ответственность:** Генерация автономного HTML-дашборда с отображением классического крафта и ящиков (R52), автообновлением страницы (`<meta http-equiv="refresh" content="30">`), отображением времени генерации и возраста данных, защитой от XSS через экранирование `html.escape()`, персональным графиком цен по клику (RU / EN) и кнопкой копирования в буфер обмена.
- **Отображение рецептов (R52):**
  - Таблица крафта показывает как крафт под Waylaid Crates, так и обычные прибыльные рецепты (не исключая `all_crafting` при наличии ящиков).
- **Публичный интерфейс:**
  - `format_currency(copper: Any) -> str`
  - `build_standalone_dashboard(conn: sqlite3.Connection, output_path: str = "portal.html", ai_advice: Optional[dict] = None, realm: Optional[str] = None, has_bartering: bool = False, has_master_chef: bool = False) -> str`
  - `notify_high_value_arbitrage(arbitrage_items: list[dict], threshold_copper: int = 10000) -> list[dict]`
- **Скрывает:** Шаблонизацию HTML, сборку графиков Plotly.js в JSON, вызов системных нотификаций, логику JS переключения языков и копирования в буфер обмена.

### 5. Точка входа демона (`run_watcher.py`)
- **Ответственность (R51):** При старте немедленно проверяет наличие `Auctionator.lua`. Если файл существует, распарсивает его, валидирует наличие реалма (при несовпадении выводит понятный лог со списком доступных миров), выполняет первичный инжест цен, запускает первичный расчет аналитики и генерацию `portal.html`, после чего передает управление фоновому `watchdog`.
- **Поддержка CLI флагов перков (R50):** `--bartering` и `--master-chef`.

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
