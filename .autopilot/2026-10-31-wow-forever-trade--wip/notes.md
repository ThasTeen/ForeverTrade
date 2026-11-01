# Исследование кодовой базы Forever Trade (Audit Review & Technical Analysis)

Дата: 2026-11-01  
Анализ выполнен по замечаниям: `audit/audit.md` (сборка `0841a89`, 106 тестов).

---

## 1. Каталог: `data/items_era_1_15.json`, `forever_overrides.json`, `src/database.py`

### Выявленные дефекты:

1. **ID 2452: Briarthorn vs Swiftthistle**:
   - **В коде проекта**:
     - `data/items_era_1_15.json` (строка 28):
       ```json
       {"item_id": 2452, "name": "Briarthorn", "quality": 1, "item_class": 7, "item_subclass": 9, "vendor_sell_price": 50, "vendor_buy_price": 0}
       ```
     - `src/database.py` (строка 57):
       ```python
       2452: {"en": "Briarthorn", "ru": "Остротерн"},
       ```
     - `src/database.py` (строки 659, 667 в рецептах алхимии):
       - Рецепт 4004 (*Lesser Healing Potion*): `reagents: [(118, 1), (2452, 1)]` (якобы Briarthorn).
       - Рецепт 4005 (*Healing Potion*): `reagents: [(2453, 1), (2452, 1), (3372, 1)]` (якобы Briarthorn).
   - **Фактическое состояние в WoW Classic Era 1.15**:
     - **ID 2450** — **Briarthorn** (*Остротерн*), обычная трава низкого уровня.
     - **ID 2452** — **Swiftthistle** (*Свистоцвет* / *Скорополох*), редкая трава, собираемая вместе с Briarthorn/Mageroyal, ключевой ингредиент для разбойничьего *Thistle Tea* и зелья скорости (*Swiftness Potion*).
   - **Последствия**:
     - На аукционе игроки выставляют Briarthorn под ID 2450, а Swiftthistle — под ID 2452.
     - Проект подтягивает цены редкого дорогого Swiftthistle (2452) и подставляет их в себестоимость простых лечебных зелий (Lesser Healing Potion и Healing Potion). Себестоимость зелий завышается в разы, маржинальность становится отрицательной, а реальный Briarthorn (2450) полностью отсутствует в базе.

2. **ID 2313 vs 2316: Medium Armor Kit**:
   - **В коде проекта**:
     - `data/items_era_1_15.json` (строка 43):
       ```json
       {"item_id": 2316, "name": "Medium Armor Kit", "quality": 1, "item_class": 7, "item_subclass": 0, "vendor_sell_price": 60, "vendor_buy_price": 250}
       ```
     - `src/database.py` (строка 138):
       ```python
       2316: {"en": "Medium Armor Kit", "ru": "Накладки из средней кожи"},
       ```
     - `src/database.py` (строка 567, рецепт 3004):
       - Рецепт кожевничества: `"crafted_item_id": 2316`.
   - **Фактическое состояние в WoW Classic Era 1.15**:
     - **ID 2313** — **Medium Armor Kit** (*Накладки из средней кожи*).
     - **ID 2316** — **Toughened Leather Gloves** (*Плотные кожаные перчатки*).
   - **Последствия**:
     - При сканировании аукциона цены на Medium Armor Kit записываются для ID 2313.
     - Аналитика крафта ищет рыночную цену продукта по ID 2316 (перчатки). Если перчаток нет на аукционе, рецепт признается неприбыльным или получает цену редких крафтовых перчаток вместо накладок.

3. **Объем каталога**:
   - В `data/items_era_1_15.json` всего 152 предмета, в `forever_overrides.json` — 24 оверрайда, в `src/database.py` — 50 жестко прописанных рецептов.
   - Любое расхождение ID между аддоном Auctionator и базой намертво ломает расчеты графа крафта.

---

## 2. Модель ящиков Waylaid Crates (`forever_overrides.json`, `src/analytics.py`)

### Как задаются ящики:
- В `forever_overrides.json` (ID 211501–211516):
  - Поля: `item_id`, `name`, `target_item_id`, `target_item_count`, `reward_copper`, `category: "waylaid_crate"`.
  - Например:
    - 211501: *Waylaid Supplies: Copper Bars* (цель: 2840 x20, награда: 500 меди).
    - 211502: *Waylaid Supplies: Silverleaf* (цель: 765 x20, награда: 400 меди).
    - 211508: *Waylaid Supplies: Copper Tubes* (цель: 4361 x2, награда: 1500 меди).

### Проблема экономической модели (Merchant's Favor vs медь):
- В реальном клиенте WoW: Forever (по аналогии с Season of Discovery) ящики снабжения (*Waylaid Supplies*) служат для прокачки репутации с фракцией снабжения (*Durotar Supply and Logistics* / *Azeroth Commerce Authority*) и дают репутацию, опыт и знаки/валюту интендантов (**Merchant's Favor**).
- Прямая награда серебром/медью за сдачу ящика либо отсутствует, либо символическая и не покрывает стоимости ресурсов.
- Проект моделирует ящики исключительно как источник прямой медной прибыли («сдал NPC — получил `reward_copper`»), что создает иллюзию безрискового «золотого арбитража сдачи», которого на сервере может не существовать в чистом виде. Покупка ресурсов игроками на AH обусловлена необходимостью закрыть репутацию ради рун и рецептов, а не выводом золота из NPC.

### Расчет себестоимости и арбитража сдачи:
- Для крафтовых предметов: `pack_cost = total_cost * target_item_count`.
- Для сырьевых ресурсов (211501–211507): `pack_cost = p_out * target_item_count`.
- В проекте смешаны две принципиально разные метрики:
  1. **Прямая сдача NPC (Quest Turn-in)**: игрок тратит `pack_cost` на закупку/крафт и получает от NPC фиксированную награду `reward_copper`.
  2. **Альтернативная продажа на AH (Opportunity Cost)**: игрок продает собранную пачку на AH и получает `pack_revenue = pack_cost_or_ah_price * 0.95`.

---

## 3. Ошибка комиссии ящиков в `src/analytics.py` (Строки 266 и 343)

### Локализация бага:
В файле `src/analytics.py`:
- **Строка 266** (крафтовые цели ящиков):
  ```python
  pack_cost = total_cost * target_count
  pack_revenue = p_out * target_count * 0.95
  turnin_profit = reward_copper - (target_count * p_out * 0.95)
  ```
- **Строка 343** (сырьевые цели ящиков 211501–211507):
  ```python
  total_cost = float(p_out)
  pack_cost = total_cost * target_count
  pack_revenue = p_out * target_count * 0.95
  turnin_profit = reward_copper - (target_count * p_out * 0.95)
  direct_turnin_profit = reward_copper - pack_cost
  turnin_roi = direct_turnin_profit / pack_cost if pack_cost > 0 else 0.0
  ```

### В чем суть ошибки:
1. Выражение `(target_count * p_out * 0.95)` — это **чистая выручка от продажи товаров на аукционе** (за вычетом 5% комиссии аукциона).
2. При закупке материалов на аукционе покупатель **всегда платит 100% стоимости** (`target_count * p_out`), аукционный сбор платит продавец.
3. При сдаче квеста/ящика NPC никакой аукционный сбор **не взимается**.
4. Автор кода по ошибке умножил расходы игрока на закупку на `0.95`, искусственно уменьшив затраты на 5%!

### Воспроизведение кейса из аудита:
- Ящик требует 20 шт. Silverleaf (ID 765).
- Текущая цена Silverleaf на AH = 21 медь за штуку.
- Награда NPC за ящик (`reward_copper`) = 400 меди.
- **Реальная экономика**:
  - Расход на закупку пачки: $20 \times 21 = 420$ меди.
  - Награда от NPC: $400$ меди.
  - Фактический финансовый результат: $400 - 420 = -20$ меди (**чистый убыток 20 меди**).
- **Расчет в кодовой базе (строка 343)**:
  - `turnin_profit = 400 - (20 * 21 * 0.95) = 400 - (420 * 0.95) = 400 - 399 = +1` медь!
- **Проход через фильтры (строка 350)**:
  ```python
  if turnin_profit <= 0 and (direct_turnin_profit <= 0 or turnin_roi < min_roi):
      continue
  ```
  Поскольку `turnin_profit == +1 > 0`, условие `turnin_profit <= 0` **ложно**, и убыточный лот успешно добавляется в результаты.
- **Отображение в результатах (строки 383-384)**:
  ```python
  "profit": round(turnin_profit, 2),
  "profit_copper": int(round(turnin_profit)),
  ```
  В дашборд отправляется `profit = +1` медь при фактическом убытке в $-20$ меди!

---

## 4. ETL и дедупликация в `src/etl.py` (Строки 230–380)

### 1. Механизм дедупликации и сбой `source_mtime`:
- В `ingest_price_snapshot` (строки 240–245):
  ```python
  if source_mtime is None and isinstance(raw_data, dict):
      source_mtime = raw_data.get("_source_mtime")

  effective_time = source_mtime if source_mtime is not None else current_time
  now = int(time.time()) if effective_time is None else int(effective_time)
  window_seconds = int(dedupe_window_minutes * 60)
  ```
- **Почему `source_mtime` отсутствует**:
  - В `src/etl.py` метод `parse_auctionator_data` для основного пути (строка 188 `_parse_single_lua_file(file_path)`) **не проставляет** `_source_mtime` в возвращаемый словарь! Поле `_source_mtime` проставляется **только** при чтении бэкапов `.bak` (строки 179, 211).
  - В `_trigger_etl` (строка 487) `src_mtime = raw_data.get("_source_mtime")` оказывается `None`.
  - В итоге `effective_time` всегда `None`, а `now` берется как системное время `int(time.time())`.

### 2. Почему неизменившийся кэш через 601 секунду повторно вставляется:
- Проверка дедупликации (строки 375–378):
  ```python
  if item_id in existing_latest:
      prev_price, prev_time = existing_latest[item_id]
      if prev_price == price and abs(now - prev_time) < window_seconds:
          continue
  ```
- Если watcher перезапускается, файл трогается другим процессом или происходит периодический импорт:
  - Прошло 601 секунда с момента предыдущего снимка: `now - prev_time >= 601 >= 600`.
  - Условие `abs(now - prev_time) < 600` становится **False**.
  - Старые цены повторно вставляются в БД с новым штампом времени `now = time.time()`.
- **В проекте отсутствует**:
  - Хеширование содержимого файла (SHA-256 по телу `AUCTIONATOR_PRICE_DATABASE`).
  - Привязка ко времени последней модификации исходного файла `os.path.getmtime(target_file)`.
  - Ограничение максимального возраста снимков при формировании торговых сигналов в `src/analytics.py`.

### 3. Обработка нулевых цен:
- Строка 371 `src/etl.py`:
  ```python
  if price is None or price < 0:
      continue
  ```
- Условие отсекает только отрицательные цены (`price < 0`), пропуская `price == 0`!
- **Последствия нулевых цен**:
  - В `get_vendor_arbitrage` (строка 55 `src/analytics.py`):
    `profit = vendor_sell_price - ah_price`. Если `ah_price == 0`, то `profit = vendor_sell_price`. Если `vendor_sell_price >= 50`, генерируется ложный сигнал мгновенного обогащения.
  - В Z-score и расчете себестоимости крафта нули ломают среднее, искажают дисперсию и могут вызывать деление на ноль при расчете ROI.

---

## 5. Снайпинг Z-score в `src/analytics.py` (Строки 470–520)

### Логика формирования сигнала:
- Строки 484–489:
  ```python
  signal = None
  if z <= z_buy_threshold:
      signal = "STRONG_BUY"
  elif z >= z_sell_threshold:
      signal = "STRONG_SELL"
  ```
  Порог по умолчанию: `z_buy_threshold = -1.8`.

### Отсутствие учета Exit Price и 5% аукционной комиссии:
- Функция `get_zscore_anomalies` смотрит исключительно на статистическое отклонение текущей цены от выборочного среднего $\mu$ в единицах $\sigma$:
  $$Z = \frac{P_{current} - \mu}{\sigma}$$
- **Воспроизведение ложного STRONG_BUY из аудита**:
  - История цен лота: `[100, 100, 100, 100, 100, 96]`.
  - Размер выборки $N = 6$.
  - Выборочное среднее: $\mu = \frac{596}{6} = 99.33$ меди.
  - Несмещенная дисперсия ($ddof=1$): $\sigma^2 = \frac{13.333}{5} = 2.667 \implies \sigma \approx 1.633$.
  - Z-score текущей цены 96:
    $$Z = \frac{96 - 99.333}{1.633} = -2.04$$
  - Так как $-2.04 \le -1.8$, выставляется сигнал **STRONG_BUY**.
- **Финансовый результат сделки**:
  - Игрок покупает лот на дампе за 96 меди.
  - Ожидаемая цена возврата к среднему (Exit Price): 100 меди.
  - При продаже на аукционе за 100 меди удерживается 5% комиссия: выручка $= 100 \times 0.95 = 95$ меди.
  - Залог за выставление лота (AH deposit): минимум 15 меди или $5\%$ от цены вендора.
  - Финансовый результат: $95 - 96 = -1$ медь (**убыток без учета залога**).
- **Что необходимо**:
  - Ввести расчет чистой прибыли перепродажи:
    $$\text{Expected Revenue} = \mu \times 0.95$$
    $$\text{Expected Net Profit} = \text{Expected Revenue} - P_{current} - \text{Deposit}$$
  - Выставлять `STRONG_BUY` **только** при одновременном выполнении условий: $Z \le -1.8$ **И** $\text{Expected Net Profit} > 0$ **И** $\text{Expected ROI} \ge \text{min\_sniping\_roi}$ (например, $\ge 10\%$).

---

## 6. Перки в `src/analytics.py` (Строки 110–150)

### Где и как захардкожены:
- В объявлении функции `get_crafting_margins` (строки 115–122):
  ```python
  def get_crafting_margins(
      conn: sqlite3.Connection,
      has_bartering: bool = True,
      has_master_chef: bool = True,
      min_roi: float = 0.25,
      realm: Optional[str] = None,
      include_ah_deposit: bool = True,
  ) -> list[dict]:
  ```
- **Использование перков**:
  - Строка 210:
    ```python
    expected_yield = (1.1 * base_yield) if (is_cooking and has_master_chef) else (1.0 * base_yield)
    ```
  - Строка 231:
    ```python
    if is_vendor:
        unit_cost = (v_buy * 0.95) if has_bartering else float(v_buy)
    ```

### Несоответствие игровой механике:
- Дефолты `has_bartering = True` и `has_master_chef = True` активируют 5% скидку на вендорные материалы и 10% бонусный выход кулинарии для **любого** персонажа по умолчанию.
- В системе навыков и Legacy перки открываются не на старте, а при достижении персонажем определенного уровня (25+) или высокого уровня владения профессией (150+).
- Все вызывающие модули (`run_watcher.py`, `src/ai_advisor.py`, `src/dashboard.py`) вызывают `get_crafting_margins` без аргументов, завышая ожидаемую прибыль стартового игрока.

### Как сделать конфигурируемыми:
1. В `src/analytics.py` установить значения по умолчанию `has_bartering: bool = False`, `has_master_chef: bool = False`.
2. В `run_watcher.py` добавить аргументы командной строки:
   ```python
   parser.add_argument("--bartering", action="store_true", default=False, help="Enable Bartering perk (-5% vendor reagent cost)")
   parser.add_argument("--master-chef", action="store_true", default=False, help="Enable Master Chef perk (+10% cooking yield)")
   ```
3. Пробросить флаги в `get_crafting_margins`, `build_compact_context` и `build_standalone_dashboard`.

---

## 7. Первоначальный импорт в `run_watcher.py` (Строки 47–75, 130–145)

### Причина отсутствия импорта при старте:
- В функции `main()`:
  - Строки 48–74: инициализируется БД, загружаются каталог и рецепты, затем **сразу же** вызываются аналитические функции `get_vendor_arbitrage`, `get_crafting_margins`, `get_zscore_anomalies` и генерируется `portal.html`.
  - В этот момент в таблице `price_snapshots` **нет ни одной записи** (если база только что создана) или содержатся старые данные.
  - Только на строке 135 вызывается `observer = start_watcher(...)`.
- `watchdog.Observer` реагирует исключительно на файловые события ОС (`on_modified`, `on_created`, `on_moved`), происходящие **после** его старта. Если файл `Auctionator.lua` уже лежал в директории `data/` или WTF до запуска демона, он никогда не прочитается, пока пользователь вручную не изменит его в игре или редакторе.

### Отсутствие валидации реалма:
- По умолчанию передан параметр `--realm ForeverRealm_Alliance`.
- Если в файле реалм называется иначе (например, `ForeverRealm_Horde` или `Everlook`), `ingest_price_snapshot` молча возвращает `0` обработанных записей без вывода предупреждений или списка доступных реалмов в файле.

### Решение:
Перед запуском фонового цикла аналитики и наблюдателя выполнить явный начальный импорт:
1. Проверить наличие `Auctionator.lua` по целевому пути.
2. Распарсить файл, проверить наличие целевого `realm` (в случае отсутствия вывести информативное исключение/лог со списком обнаруженных реалмов).
3. Выполнить разовый вызов `ingest_price_snapshot(conn, raw_data, args.realm, source_mtime=...)`.
4. Только после успешного начального наполнения БД запускать генерацию первичного дашборда и стартовать `watchdog`.

---

## 8. Дашборд в `src/dashboard.py` (Строки 500–530, 699–720, 1250–1270)

### 1. Почему скрываются обычные рецепты при наличии ящиков:
- Строки 500–501 `src/dashboard.py`:
  ```python
  # Filter crafting margins for Waylaid Crates (with fallback to all crafting if none are tagged)
  waylaid_crafting = [c for c in all_crafting if c.get("is_waylaid_target")]
  crafting_display = waylaid_crafting if waylaid_crafting else all_crafting
  ```
- Если среди прибыльных рецептов есть хотя бы один ящик или предмет под ящик (`len(waylaid_crafting) > 0`), переменная `crafting_display` полностью перезаписывается массивом `waylaid_crafting`.
- Все классические высокомаржинальные крафты (сумки из льняной ткани, лечебные зелья, экипировка), не связанные с ящиками, **вырезаются из итоговой HTML-таблицы**.

### 2. Отсутствие автообновления:
- В `<head>` (строки 699–720) отсутствует метатег:
  ```html
  <meta http-equiv="refresh" content="30">
  ```
- В JavaScript дашборда нет polling-механизма (например, `setInterval` с проверкой заголовка `Last-Modified` или перезагрузкой страницы). Открытый в браузере дашборд не обновляется при поступлении новых данных от демона.

### 3. Отсутствие отображения времени данных:
- В шапке дашборда (строки 1254–1270) выводятся только стат-пилюли с количеством сигналов.
- Нет информации о том:
  - Когда был сгенерирован портал (`Generated at: YYYY-MM-DD HH:MM:SS`).
  - Каково время самого свежего снапшота цен (`Latest market snapshot: ...`).
  - Пользователь не может оценить актуальность данных и рискует торговать по устаревшей информации.

---

## 9. Анализ тестов в `tests/`

### Текущее состояние тестового набора:
- Полный прогон: `python -m pytest` $\to$ **106 passed** (время выполнения ~7.2 сек).
- Распределение по файлам:
  1. `tests/test_database.py` (13 тестов): схема БД, внешние ключи, загрузка каталога и оверрайдов, загрузка 50 рецептов 5 профессий, ротация и удаление снимков старше 30 дней, локализация названий предметов.
  2. `tests/test_etl.py` (31 тест): нормализация ID предметов, обработка форматов `item:1234:0:0`, чтение `.lua` и `.bak` через `luadata`, отсечение бэкапов старше 2 часов, дедупликация в 10-минутном окне, обработка специфичных полей Auctionator v341 (`m`, `mr`), события watchdog.
  3. `tests/test_analytics.py` (15 тестов): вендорный арбитраж ($\ge 50$ меди), скидка 5% Bartering, множитель 1.1x Master Chef, вычет 5% аукционной комиссии, аукционный залог, расчет Z-score ($Z \le -1.8, Z \ge 1.6$), модель Waylaid Crates.
  4. `tests/test_dashboard.py` (23 теста): генерация HTML-файла `portal.html`, наличие трех таблиц сигналов, Plotly.js график цен, XSS-экранирование названий, звуковые нотификации арбитража $> 1\text{g}$, двуязычный интерфейс RU/EN.
  5. `tests/test_ai_advisor.py` (24 теста): сбор топ-3 контекста для модели, валидация промпта, клиент `AntigravityManagerClient`, эвристический fallback при отсутствии API-ключа.

### Почему существующие тесты не поймали дефекты аудита:
1. **Тест дедупликации `test_deduplication_ten_minute_window`** (`tests/test_etl.py`, строки 323–328) прямо фиксирует и утверждает поведение, признанное аудитом ошибочным:
   ```python
   # Fourth ingest at t = 2050 (10.8 minutes after t2): 2589 unchanged at 1100, window elapsed -> inserted
   assert inserted_4 == 2  # Both inserted because > 10 minutes elapsed
   ```
2. **Тест ящиков `test_waylaid_crates_economy_and_raw_resources`** (`tests/test_analytics.py`, строки 987, 1003) прямо закрепляет формулу с вычитанием аукционной комиссии из выручки вместо расходов:
   ```python
   # Turn-in profit vs AH: 500 - 285 = 215
   assert r_raw["turnin_profit"] == 215.0
   ```
   Тест не проверял случай, когда себестоимость превышает награду вендора, но ниже за вычетом 5%.
3. **Тесты перков `test_crafting_margins_bartering_discount`**: тестируют вызовы с явным указанием `has_bartering=True` и `has_bartering=False`, но не проверяют дефолтное поведение вызывающих систем (`run_watcher.py`, `dashboard.py`).
4. **Тест Z-score `test_zscore_anomalies_mean_sigma_and_signals`**: проверяет только математику $Z \le -1.8$, не сопоставляя ее с реальной ценой выхода и безубыточностью после комиссии.

### Команды для запуска тестов:
- Полный запуск: `python -m pytest`
- Подробный запуск с выводом имен тестов: `python -m pytest -v`
- Запуск конкретных модулей:
  - `python -m pytest tests/test_analytics.py`
  - `python -m pytest tests/test_etl.py`
  - `python -m pytest tests/test_database.py`
  - `python -m pytest tests/test_dashboard.py`
  - `python -m pytest tests/test_ai_advisor.py`
- Запуск конкретного теста по маске: `python -m pytest -k "test_waylaid"`
