# Архитектура Forever Trade

Автономный аналитический агент и торговый портал для World of Warcraft: Forever.

Проект отслеживает рыночные срезы цен аддона `Auctionator`, сохраняет временные ряды в SQLite, рассчитывает прибыльные арбитражные цепочки и ценовые аномалии с учетом серверных модификаторов (перков) Forever, запрашивает тактические рекомендации у нейросетевого ассистента и генерирует автономный HTML-дашборд.

---

## 1. Общая схема системы

```
  +--------------------------------------------------------------+
  |                   Игровой клиент WoW                         |
  |  Аддон Auctionator -> SavedVariables/Auctionator.lua (.bak)  |
  +--------------------------------------------------------------+
                                 |
                                 v  (watchdog file events)
  +--------------------------------------------------------------+
  |                 ETL Конвейер (src/etl.py)                    |
  |  - Безопасный парсинг luadata (с .bak fallback, без eval)    |
  |  - Нормализация сигнатур itemId (2589:0:0:0 -> 2589)         |
  |  - Дедупликация (10-минутное окно по ценам)                 |
  |  - Batch Ingestion в price_snapshots                         |
  +--------------------------------------------------------------+
                                 |
                                 v
  +--------------------------------------------------------------+
  |              Хранилище SQLite (src/database.py)              |
  |                      Файл: market.db                         |
  |  - WAL режим, PRAGMA foreign_keys = ON                       |
  |  - items, price_snapshots, recipes, recipe_reagents          |
  |  - Составные индексы (item_id, time), (realm, time)          |
  |  - Ротация снимков старше 30 дней                            |
  +--------------------------------------------------------------+
                                 |
                                 v
  +--------------------------------------------------------------+
  |             Аналитическое ядро (src/analytics.py)            |
  |  - Вендорный арбитраж (P_buyout < V_sell, profit >= 50c)     |
  |  - Маржа крафта: скидка 5% Bartering, выход 1.1x Cooking     |
  |    (Master Chef), 5% аукционный сбор, ROI >= 25%             |
  |  - Z-score аномалий (Z <= -1.8 STRONG_BUY, Z >= 1.6 SELL)    |
  +--------------------------------------------------------------+
                     |                         |
                     v                         v
  +---------------------------------+  +-------------------------------+
  |   AI-ассистент                  |  |  Генератор дашборда           |
  |   (src/ai_advisor.py)           |  |  (src/dashboard.py)           |
  |  - Компактный контекст (топ-3)  |  |  - Монолитный portal.html     |
  |  - AntigravityManagerClient     |  |  - 3 блока сигналов           |
  |    (Gemini 3.8 Flash High)      |  |  - Графика Plotly.js (CDN)    |
  |  - Эвристический fallback       |  |  - Форматирование g/s/c       |
  |    (детерминированный)          |  |  - Звуковой алерт > 1g        |
  +---------------------------------+  +-------------------------------+
```

---

## 2. Структура данных SQLite (`src/database.py`)

Хранилище реализовано на бессерверной SQLite 3 (`market.db`).

### Параметры подключения
- `PRAGMA foreign_keys = ON;` — обеспечение целостности внешних ключей.
- `PRAGMA journal_mode = WAL;` — Write-Ahead Logging для неблокирующих параллельных чтений при фоновой записи.
- `conn.row_factory = sqlite3.Row` — доступ к полям по именам.

### Схема таблиц

```sql
CREATE TABLE IF NOT EXISTS items (
    item_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    quality INTEGER DEFAULT 1,
    item_class INTEGER DEFAULT 0,
    item_subclass INTEGER DEFAULT 0,
    vendor_sell_price INTEGER DEFAULT 0,
    vendor_buy_price INTEGER DEFAULT 0,
    target_item_id INTEGER,
    target_item_count INTEGER DEFAULT 0,
    reward_copper INTEGER DEFAULT 0,
    category TEXT,
    is_override INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS price_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES items(item_id),
    realm TEXT NOT NULL,
    price INTEGER NOT NULL,
    timestamp INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS recipes (
    recipe_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    profession TEXT NOT NULL,
    crafted_item_id INTEGER NOT NULL REFERENCES items(item_id),
    yield REAL DEFAULT 1.0,
    yield_min INTEGER DEFAULT 1,
    yield_max INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS recipe_reagents (
    recipe_id INTEGER NOT NULL REFERENCES recipes(recipe_id),
    reagent_item_id INTEGER NOT NULL REFERENCES items(item_id),
    reagent_id INTEGER NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    quantity INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (recipe_id, reagent_item_id)
);
```

### Индексы
- `idx_snapshots_item_time` на `price_snapshots(item_id, timestamp)` — для извлечения последних цен через оконные функции `ROW_NUMBER()` и расчета скользящего окна Z-score.
- `idx_snapshots_realm_time` на `price_snapshots(realm, timestamp)` — для быстрой фильтрации по игровым мирам.

### Каталог и рецепты
1. `load_catalog(conn, era_catalog_path, overrides_path)`:
   - Базовый слой: `data/items_era_1_15.json` (предметы Classic Era 1.15).
   - Слой оверрайдов: `forever_overrides.json` (предметы с `is_override = 1`, контейнеры снабжения `category = 'waylaid_crate'` с маппингом `target_item_id` и `reward_copper`).
   - Использует `INSERT OR REPLACE INTO items`.
2. `load_recipes(conn)`:
   - Граф рецептов для 5 ключевых профессий: Инженерное дело (Engineering), Портняжное дело (Tailoring), Кожевничество (Leatherworking), Алхимия (Alchemy), Кулинария (Cooking).
   - Наполняет таблицы `recipes` и `recipe_reagents`.
3. `prune_snapshots(conn, max_days=30)`:
   - Удаление снимков цен старше 30 дней.
   - Поддерживает временные метки как в формате целых чисел Unix epoch, так и в строковом ISO-формате.

---

## 3. ETL конвейер (`src/etl.py`)

Модуль осуществляет реактивный мониторинг изменений файлов аддона и пакетный инжест данных в базу.

### Источник и мониторинг
- Использует библиотеку `watchdog` (`Observer` и `AuctionatorEventHandler`).
- Мониторит файл `Auctionator.lua` в каталоге `WTF/Account/<ACCOUNT>/SavedVariables` (путь передается через CLI-аргумент `--watch-dir` или переменную окружения `WOW_WTF_PATH`).
- Поддерживает дебаунс событий (`debounce_seconds = 0.5`) и кратковременную паузу (50 мс) перед чтением, давая файловой системе завершить сброс буферов на диск.

### Безопасный парсинг `luadata` и fallback
- **Полный запрет `eval()`/`exec()`**: разбор выполняется строго библиотекой `luadata`.
- Каскад парсинга:
  1. `luadata.read(path)` напрямую из файла;
  2. При наличии обертки или служебных заголовков — поиск границ таблицы `AUCTIONATOR_PRICE_DATABASE = { ... }` алгоритмом подсчета баланса фигурных скобок `_extract_lua_table` с корректной обработкой строковых литералов и комментариев Lua (`--` и `--[[ ... ]]`), после чего вызывается `luadata.unserialize(extracted)`;
  3. Десериализация всего текста через `luadata.unserialize(content)`.
- **Отказоустойчивый fallback**: если основной файл `Auctionator.lua` заблокирован игрой или поврежден при аварийном завершении клиента, парсер автоматически пробует загрузить резервные копии:
  - `Auctionator.lua.bak`
  - `Auctionator.bak`
- События сбоя и переключения логируются с уровнем `WARNING`/`INFO` в `watcher.log`.

### Нормализация и дедупликация
- `normalize_item_id(raw_id)`: преобразует сложные строковые сигнатуры предметов (например, `"2589:0:0:0:0"`, `"item:2589"`) в целое число `2589`.
- **Дедупликация цен (`ingest_price_snapshot`)**:
  - Окно дедупликации: 10 минут (`dedupe_window_minutes = 10`).
  - Перед вставкой запрашивается последняя известная цена каждого предмета в игровом мире:
    `SELECT item_id, price, MAX(timestamp) FROM price_snapshots WHERE realm = ? GROUP BY item_id`.
  - Если цена лота совпадает с предыдущим значением и разница во времени меньше окна — запись пропускается.
- **Foreign Key Safety**: перед вставкой в `price_snapshots` для всех новых `item_id` выполняется пакетный `INSERT OR IGNORE INTO items (item_id, name) VALUES (?, ?)`, чтобы не нарушать `PRAGMA foreign_keys = ON`.
- Вставка выполняется пакетно через `executemany` в рамках одной транзакции.

---

## 4. Аналитическое ядро (`src/analytics.py`)

Модуль реализует математические модели оценки рынка с учетом специфики правил сервера Forever.

### 4.1. Вендорный арбитраж (`get_vendor_arbitrage`)
Поиск лотов на аукционе, цена выкупа которых ниже гарантированной цены сдачи торговцу:

$$\Pi_{\text{vendor}} = V_{\text{sell}} - P_{\text{buyout}}$$

- **Условие входа**: $P_{\text{buyout}} < V_{\text{sell}}$ и $\Pi_{\text{vendor}} \ge 50\text{ copper}$.
- Порог в 50 меди отсекает шум и не стоящие времени операции.
- Результаты сортируются по убыванию чистой прибыли $\Pi_{\text{vendor}}$.

### 4.2. Маржинальность крафта (`get_crafting_margins`)
Расчет экономической эффективности производства с учетом биржевого сбора и серверных перков:

$$\Pi_{\text{craft}} = \left( P_{\text{out}} \times (1 - \tau) \times E[Y] \right) - \sum_{i=1}^{n} (C_{\text{in}, i} \times Q_i)$$

Где:
- $P_{\text{out}}$ — минимальная текущая цена готового изделия на аукционе;
- $\tau = 0.05$ — аукционная комиссия ($5\%$);
- $E[Y]$ — математическое ожидание выхода готовых изделий:
  - $E[Y] = 1.1 \times Y_{\text{base}}$ для профессии **Кулинария (Cooking)** при включенном перке **Master Chef** ($10\%$-й шанс дополнительного выхода);
  - $E[Y] = 1.0 \times Y_{\text{base}}$ для остальных профессий.
- $C_{\text{in}, i}$ — стоимость единицы реагента:
  - $C_{\text{in}, i} = V_{\text{buy}, i} \times 0.95$, если реагент продается у NPC-вендора и активен перк **Bartering** (скидка $5\%$);
  - $C_{\text{in}, i} = P_{\text{ah}, i}$, если компонент закупается с аукциона по последнему снимку.
- $Q_i$ — требуемое количество реагента.

Рентабельность инвестиций:

$$\text{ROI} = \frac{\Pi_{\text{craft}}}{\sum_{i=1}^{n} (C_{\text{in}, i} \times Q_i)}$$

- **Критерий фильтрации**: $\Pi_{\text{craft}} > 0$ и $\text{ROI} \ge 25\%$ (порог отсечения низкомаржинальных и рискованных рецептов).
- **Связка с Waylaid Crates**: сопоставление с таблицей `items` (`category = 'waylaid_crate'`) выявляет рецепты, целевые предметы которых требуются для упаковки в ящики снабжения (`is_waylaid_target = True`).

### 4.3. Z-score ценовых аномалий (`get_zscore_anomalies`)
Выявление отклонений цен сырья и ликвидных товаров от скользящего среднего по истории из $N$ снимков ($2 \le N \le 15$):

$$\mu = \frac{1}{N} \sum_{k=1}^{N} P_k, \qquad \sigma = \sqrt{\frac{1}{N} \sum_{k=1}^{N} (P_k - \mu)^2}$$

$$Z = \frac{P_{\text{cur}} - \mu}{\sigma}$$

- **Защита от деления на ноль**: при $\sigma = 0$ (стабильная цена без изменений) значение $Z = 0.0$.
- **Торговые сигналы**:
  - $Z \le -1.8$ $\rightarrow$ **`STRONG_BUY`**: панический сброс цен, ликвидация запасов, сильная перепроданность. Рекомендация к агрессивной закупке на склад.
  - $Z \ge 1.6$ $\rightarrow$ **`STRONG_SELL`**: локальный спекулятивный пик, дефицит. Рекомендация к фиксации прибыли и распродаже запасов.

---

## 5. Генератор автономного HTML-дашборда (`src/dashboard.py`)

Генерирует монолитный отчет `portal.html`, не требующий локального веб-сервера.

- **Автономность**: файл открывается двойным кликом в браузере (`file:///...`). Все CSS-стили темной темы инлайнятся в документ.
- **Интерактивные графики**: динамика цен на сырье и расходники строится на базе `Plotly.js` (`plotly.graph_objects.Figure`) и встраивается через CDN (`include_plotlyjs="cdn"`). Графики поддерживают зум, выбор временных интервалов и подсказки при наведении.
- **Три блока рыночных сигналов**:
  1. `Вендорный арбитраж` — позиции мгновенного выкупа и сдачи NPC;
  2. `Крафт под Waylaid Crates` — маржинальные рецепты под ящики с расчетом себестоимости и ROI;
  3. `Аномальные сбросы цен` — сигналы Z-score с бейджами `STRONG_BUY` и `STRONG_SELL`.
- **Форматирование валюты (`format_currency`)**: конвертация медных монет в классический формат WoW (1g = 100s = 10000c) со стилизованными HTML-классами (`money-gold`, `money-silver`, `money-copper`).
- **Звуковые и консольные оповещения (`notify_high_value_arbitrage`)**: при наличии вендорного арбитража с чистой прибылью $\ge 1\text{g}$ ($10\,000$ меди) выдается звуковой сигнал терминала (`\a`) и предупреждающая запись в лог.

---

## 6. AI-ассистент и Antigravity Manager (`src/ai_advisor.py`)

Модуль синтезирует тактический оперативный план на игровую сессию.

### Компактный рыночный срез (`build_compact_context`)
Агрегирует ультракомпактный JSON, минимизируя потребление контекстного окна LLM:
1. Топ-3 сделки вендорного арбитража;
2. Топ-3 маржинальных рецепта под ящики снабжения Waylaid Crates;
3. Топ-3 сырьевых сброса цен ($Z \le -1.8$).

### Клиент `AntigravityManagerClient`
- Выполняет HTTP REST-запросы к Google Generative AI API с использованием стандартной библиотеки `urllib.request`.
- Модель по умолчанию: `gemini-3.8-flash-high`.
- Читает API-ключ из переменных `GEMINI_API_KEY` или `GOOGLE_API_KEY`.
- Запрашивает строгий JSON со структурированным чек-листом действий игрока (шаг, категория, конкретное действие, цель, ожидаемая прибыль, приоритет `HIGH`/`MEDIUM`/`LOW`).

### Детерминированный эвристический fallback (`generate_heuristic_advice`)
- При отсутствии API-ключа, сетевых ошибках или таймаутах автоматически и прозрачно формирует детерминированный структурированный чек-лист на чистом Python.
- Приоритеты действий:
  - `HIGH`: вендорный выкуп (моментальная гарантированная прибыль без риска);
  - `HIGH`/`MEDIUM`: крафт под Waylaid Crates (в зависимости от ROI $\ge 50\%$);
  - `MEDIUM`: скупка перепроданного сырья по сигналам Z-score под склад.
- Вердикт внедряется в верхний баннер дашборда `portal.html`.
