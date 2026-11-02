# Архитектура Forever Trade

Автономный аналитический агент, снайперский стек и торговый портал для World of Warcraft: Forever.

Проект построен по двухконтурной архитектуре: фоновый Python-демон с робастным статистическим ядром, SQLite и генератором автономного портала `portal.html`, сопряженный с ультралегким микро-аддоном `ForeverTrade_Sniper` (<50 КБ) в клиенте игры для безопасного аппаратного перехвата лотов без использования тяжелых и нестабильных фреймворков вроде TSM/Ace3.

---

## 1. Общая схема системы (Двухконтурная модель)

```
  +========================================================================+
  |                   ВНУТРЕННИЙ ИГРОВОЙ КОНТУР (WoW Client)               |
  |                                                                        |
  |  +---------------------------+       +------------------------------+  |
  |  |   Микро-аддон             |       |  SavedVariables              |  |
  |  |   ForeverTrade_Sniper     |       |  - Auctionator.lua (.bak)    |  |
  |  |  - Опрос последней стр.   |       +------------------------------+  |
  |  |    с защитой от сдвига    |                      |                  |
  |  |  - Hardware Mousewheel    |                      | (watchdog events)|
  |  |    Buyout & Slot Verify   |                      v                  |
  +==|===========================|======================|==================+
     ^                           |                      |
     | (FT_SNIPER_CONFIG)        | (PlaceAuctionBid)    |
     |                           v                      |
  +--|--------------------------------------------------|------------------+
  |  |                ВНЕШНИЙ КОНТУР (Python Daemon)    |                  |
  |  |                                                  v                  |
  |  |   +--------------------------------------------------------------+  |
  |  |   |                 ETL Конвейер (src/etl.py)                    |  |
  |  |   |  - Безопасный парсинг luadata (с .bak fallback, без eval)    |  |
  |  |   |  - Нормализация сигнатур itemId (2589:0:0:0 -> 2589)         |  |
  |  |   |  - Дедупликация (10-минутное окно и SHA-256)                 |  |
  |  |   |  - Batch Ingestion в price_snapshots                         |  |
  |  |   +--------------------------------------------------------------+  |
  |  |                                  |                                  |
  |  |                                  v                                  |
  |  |   +--------------------------------------------------------------+  |
  |  |   |              Хранилище SQLite (src/database.py)              |  |
  |  |   |                      Файл: market.db                         |  |
  |  |   |  - WAL режим, PRAGMA foreign_keys = ON                       |  |
  |  |   |  - items, price_snapshots, recipes, recipe_reagents          |  |
  |  |   |  - item_market_metrics (Hampel MAD, EWMA, Liquidity)         |  |
  |  |   |  - Ротация снимков старше 30 дней                            |  |
  |  |   +--------------------------------------------------------------+  |
  |  |                                  |                                  |
  |  |                                  v                                  |
  |  |   +--------------------------------------------------------------+  |
  |  |   |             Аналитическое ядро (src/analytics.py)            |  |
  |  |   |  - Робастный DBMarket: Hampel Filter (MAD) & EWMA            |  |
  |  |   |  - Liquidity Shield (мультипликатор оборачиваемости L)       |  |
  |  |   |  - Вендорный арбитраж и Vendor Shuffle (P_ah < V_sell)       |  |
  |  |   |  - Disenchant Shuffle: матожидание распыления E[DE]          |  |
  |  |   |  - Рекурсивный DAG Make-vs-Buy Crafting Engine               |  |
  |  |   |  - Z-score снайпинг со строгой валидацией безубытка          |  |
  |  |   +--------------------------------------------------------------+  |
  |  |           |                         |                   |           |
  |  +-----------+                         v                   v           |
  |  (export config.lua)         +-------------------+  +---------------+  |
  |                              |   AI-ассистент    |  | Дашборд       |  |
  |                              |   (src/ai_advisor)|  | (dashboard.py)|  |
  |                              |  - Топ-3 контекст |  | - 100vh UI    |  |
  |                              |  - Gemini 3.8     |  | - Дизэнчант   |  |
  |                              |  - Fallback       |  | - DAG дерево  |  |
  |                              +-------------------+  +---------------+  |
  +------------------------------------------------------------------------+
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

CREATE TABLE IF NOT EXISTS item_market_metrics (
    item_id INTEGER,
    realm TEXT NOT NULL,
    median_price INTEGER NOT NULL,
    mad INTEGER NOT NULL,
    sigma_mad INTEGER NOT NULL,
    ewma_price INTEGER NOT NULL,
    liquidity_score REAL NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (item_id, realm),
    FOREIGN KEY (item_id) REFERENCES items(item_id)
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

### 4.4. Робастная оценка рыночных цен: Hampel Filter и EWMA (`compute_robust_market_metrics`)
Для защиты от ценовых атак (фиктивные лоты с экстремальной ценой) и адаптации к трендам вместо классических $\mu$ и $\sigma$ применяется робастная статистика:
- **Медиана и MAD (Median Absolute Deviation):**
  $$\text{median} = \text{med}(P), \qquad \text{MAD} = \text{med}(|P_k - \text{median}|)$$
  $$\sigma_{\text{MAD}} = 1.4826 \times \text{MAD}$$
- **Фильтр Hampel (отсечение выбросов):**
  Цены, отклоняющиеся более чем на $3 \times \sigma_{\text{MAD}}$ от медианы, исключаются из ретроспективной выборки как статистические аномалии.
- **Экспоненциальное скользящее среднее (EWMA):**
  Сглаживает временной ряд с весовым коэффициентом свежих данных $\alpha = 0.2$:
  $$S_t = \alpha P_t + (1 - \alpha) S_{t-1}$$
Метрики кэшируются в таблице `item_market_metrics`.

### 4.5. Liquidity Shield и защита от неликвидных лотов (`compute_liquidity_score`)
Снайпинг низколиквидных товаров (редкие рецепты, специфичная экипировка) несет риск долгосрочной заморозки капитала. Внедрен мультипликатор ликвидности $L \in [0.01, 1.0]$:
- Оценивает оборачиваемость на основе:
  1. Частоты появления лота в снимках $f_{\text{snap}}$;
  2. Категории предмета (высокая у расходников, сырья, сумок; пониженная у экипировки и рецептов);
  3. Волатильности цены во времени.
- Скорректированная прибыль для ранжирования сигналов:
  $$\Pi_{\text{adj}} = \Pi_{\text{raw}} \times L$$

### 4.6. Disenchant Shuffle и математическое ожидание распыления (`calculate_disenchant_value`, `get_disenchant_arbitrage`)
Оценка эффективности скупки экипировки на аукционе для последующего распыления:
- **База данных распыления:** `data/disenchant_tables_era.json` на основе канонических таблиц Classic Era 1.15 и кастомных модификаторов Forever (клиент 1.60):
  - Зеленое оружие ilvl 16–30 на сервере Forever гарантированно распыляется в `Greater Magic Essence` (ID 10939), броня — в `Lesser/Greater Astral Essence`.
  - Синяя экипировка ilvl 1–80 дает Shards и Nexus Crystals по диапазонам уровней.
- **Математическое ожидание распыления:**
  $$E[\text{DE}] = \sum_{r \in \text{reagents}} P_{\text{market}}(r) \times p(r) \times Q_{\text{avg}}(r)$$
  Где $P_{\text{market}}(r)$ — робастная рыночная цена порошка/эссенции/осколка, $p(r)$ — вероятность дропа, $Q_{\text{avg}}(r)$ — средний размер стека.
- **Арбитражное условие:**
  $$\Pi_{\text{de}} = E[\text{DE}] - P_{\text{buyout}} \ge \Pi_{\text{min}} \quad (\ge 1000\text{ copper})$$

### 4.7. Рекурсивный DAG Make-vs-Buy Crafting Engine (`resolve_effective_material_cost`)
Производственные цепочки моделируются как ориентированный ациклический граф (DAG):
- Позволяет выявлять скрытую маржу, когда покупка готового полуфабриката на аукционе дороже его самостоятельного изготовления из базового сырья или покупки у вендора.
- Для каждого реагента рекурсивно вычисляется эффективная себестоимость:
  $$C_{\text{eff}}(i) = \min \left( P_{\text{AH}}(i), \; P_{\text{vendor\_buy}}(i), \; C_{\text{craft}}(i) \right)$$
- Защита от зацикливания: отслеживание множества `visited` и ограничение максимальной глубины рекурсии (`depth <= 5`).
- Формирует дерево вложенности для визуализации в дашборде.

### 4.8. Мост генерации конфигурации микро-аддона (`export_sniper_addon_config`)
Трансляция аналитических результатов во внутриигровой аддон:
- Извлекает высокомаржинальные цели (снайпинг, распыление, арбитраж).
- Записывает плоскую таблицу `FT_SNIPER_CONFIG` в файл `config.lua` в каталоге аддона:
  ```lua
  FT_SNIPER_CONFIG = {
      version = 2,
      generated_at = 1730599200,
      min_profit_copper = 1000,
      targets = {
          [10940] = 450, -- itemId = max_buyout_unit_copper
          ...
      }
  }
  ```

---

## 5. Генератор автономного HTML-дашборда (`src/dashboard.py`)

Генерирует монолитный отчет `portal.html`, не требующий локального веб-сервера.

- **Автономность**: файл открывается двойным кликом в браузере (`file:///...`). Все CSS-стили темной темы инлайнятся в документ.
- **Интерфейс Goblin Terminal UI (100vh)**: компактная двухпанельная компоновка:
  - 65% левая панель: таблицы сигналов с сортировкой, поиском и вкладками:
    1. `Strong Buy` — сигналы перепроданности Z-score с валидацией безубытка;
    2. `Арбитраж` — позиции мгновенного выкупа и сдачи торговцу;
    3. `Крафт Dual` — стандартный крафт и ящики снабжения Waylaid Crates с раскладкой промежуточных полуфабрикатов Make-vs-Buy;
    4. `✨ Дизэнчант` — распыление экипировки с вероятностями дропа реагентов и маржой $E[\text{DE}] - P_{\text{buyout}}$;
    5. `Вендор-шаффл` — лоты аукциона дешевле цены прямой сдачи торговцу.
  - 35% правая панель: детальный инспектор предмета с единым переиспользуемым графиком Plotly.js, линией безубытка, интерактивным деревом DAG-крафта и калькулятором сделки.
- **Интерактивные графики**: динамика цен на сырье и расходники строится на базе `Plotly.js` (`plotly.graph_objects.Figure`) и встраивается через CDN (`include_plotlyjs="cdn"`).
- **Экспорт данных**:
  - Генерация Shopping List для импорта в игровой Auctionator (`ForeverTrade^...`);
  - Экспорт конфигурации `config.lua` для микро-аддона `ForeverTrade_Sniper`.
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

---

## 7. Внутриигровой микро-аддон `ForeverTrade_Sniper` (`addons/ForeverTrade_Sniper/`)

Легковесный автономный модуль без зависимостей от сторонних библиотек (без Ace3), обеспечивающий мгновенный перехват недооцененных лотов в клиенте игры (1.60.1.70707 LegacyAH).

### Архитектура и ограничения
- **Минимальный футпринт:** объем занимаемой оперативной памяти не превышает 50 КБ (в отличие от сотен мегабайт у TSM).
- **Файловая структура:**
  - `ForeverTrade_Sniper.toc` — метаданные аддона и регистрация файлов;
  - `config.lua` — автоматически обновляемая внешним Python-демоном таблица целей `FT_SNIPER_CONFIG`;
  - `ForeverTrade_Sniper.lua` — логика опроса страниц, защиты от сдвига и защищенного выкупа.

### Защита от сдвига страниц (Page Drift Protection)
При непрерывном выставлении новых лотов другими игроками последняя страница аукциона LegacyAH (`totalCount / 50`) непрерывно смещается:
- Функция `FT_UpdateTargetPage()` рассчитывает текущий индекс последней страницы.
- Реализовано адаптивное чередование: опрос чередуется между страницами `targetPage` и `targetPage - 1`. Это гарантирует отсутствие "слепых зон" и исключает пропуск свежих поступлений.

### Безопасный выкуп через аппаратные клики (Hardware Event Mousewheel Buyout)
Функция `PlaceAuctionBid` является защищенной в API WoW и может быть вызвана только в ответ на аппаратное событие:
1. При совпадении цены лота с порогом `targets[itemId]`:
   - Аддон воспроизводит звуковой сигнал тревоги (`PlaySoundFile`).
   - Паузирует периодический опрос страниц.
   - Взводит защищенную кнопку `SecureActionButtonTemplate` и временно привязывает ее к прокрутке колеса мыши (`MOUSEWHEELUP` / `MOUSEWHEELDOWN`).
2. При скролле колеса мыши вызывается верифицированная покупка (`FT_ExecuteVerifiedBuy`):
   - **Валидация слота:** проверяется, что по текущему индексу лота на аукционе все еще находится ожидаемый `itemId`, цена совпадает с `expectedBuyout`, и хэш лота валиден.
   - Если лот сместился или выкуплен, сделка мгновенно прерывается без отправки запроса, защищая игрока от покупки чужого лота при сетевых задержках.
   - При успешной проверке отправляется `PlaceAuctionBid("list", index, buyoutPrice)`.
3. После завершения попытки бинды колеса мыши немедленно сбрасываются (`ClearOverrideBindings`), и сканирование продолжается.
