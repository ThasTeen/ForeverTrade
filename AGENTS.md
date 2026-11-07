<!-- autopilot:start -->
# Forever Trade

Автономный аналитический агент и торговый портал для World of Warcraft: Forever.

## Команды

| Команда | Что делает |
|---------|------------|
| `pip install -r requirements.txt` | Установить зависимости проекта |
| `python run_watcher.py` | Запустить фоновый мониторинг файлов, ETL и генератор дашборда |
| `python run_watcher.py --run-once --realm "Everlook"` | Разовый запуск ETL и генерация дашборда для указанного реалма |
| `python -m pytest` | Запустить полный тестовый набор (229 тестов) |
| `python -m pytest tests/test_database.py` | Запустить тесты схемы БД, каталога и ротации |
| `python -m pytest tests/test_etl.py` | Запустить тесты ETL, парсинга luadata и дедупликации |
| `python -m pytest tests/test_analytics.py` | Запустить тесты арбитража, Z-score снайпинга и ящиков |
| `python -m pytest tests/test_dashboard.py` | Запустить тесты генератора HTML-дашборда |
| `python -m pytest tests/test_ai_advisor.py` | Запустить тесты AI-ассистента и fallback-модели |

## Архитектура

Подробное описание схемы БД, ETL-конвейера, математических моделей перков Forever и AI-клиента находится в [`docs/architecture.md`](file:///d:/ai%20project/Forever%20Trade/docs/architecture.md).

Ключевые архитектурные решения (ADR):
- [`docs/adr/0001-sqlite-storage.md`](file:///d:/ai%20project/Forever%20Trade/docs/adr/0001-sqlite-storage.md) — Хранилище SQLite в режиме WAL с внешними ключами.
- [`docs/adr/0002-luadata-safe-parsing.md`](file:///d:/ai%20project/Forever%20Trade/docs/adr/0002-luadata-safe-parsing.md) — Безопасный парсинг SavedVariables через `luadata` без `eval()`.
- [`docs/adr/0003-antigravity-manager-gemini.md`](file:///d:/ai%20project/Forever%20Trade/docs/adr/0003-antigravity-manager-gemini.md) — Интеграция с Antigravity Manager (`gemini-3.8-flash-high`) и эвристический fallback.
- [`docs/adr/0004-standalone-html-dashboard.md`](file:///d:/ai%20project/Forever%20Trade/docs/adr/0004-standalone-html-dashboard.md) — Автономный статический дашборд `portal.html` без сторонних веб-серверов.
- [`docs/adr/0005-forever-crafting-and-arbitrage-models.md`](file:///d:/ai%20project/Forever%20Trade/docs/adr/0005-forever-crafting-and-arbitrage-models.md) — Математические модели вендорного арбитража и крафта.
- [`docs/adr/0006-catalog-id-collision-fixes.md`](file:///d:/ai%20project/Forever%20Trade/docs/adr/0006-catalog-id-collision-fixes.md) — Устранение коллизий ID каталога Classic Era 1.15.
- [`docs/adr/0007-waylaid-crates-dual-economy.md`](file:///d:/ai%20project/Forever%20Trade/docs/adr/0007-waylaid-crates-dual-economy.md) — Двойная экономика Waylaid Crates: 100% расход на AH и отсечение убытков.
- [`docs/adr/0008-etl-content-hash-deduplication.md`](file:///d:/ai%20project/Forever%20Trade/docs/adr/0008-etl-content-hash-deduplication.md) — Дедупликация снапшотов по SHA-256 хэшу и таблица `_etl_metadata`.
- [`docs/adr/0009-strong-buy-breakeven-exit-validation.md`](file:///d:/ai%20project/Forever%20Trade/docs/adr/0009-strong-buy-breakeven-exit-validation.md) — Валидация выхода для снайпинга по историческому среднему $\mu$, комиссии 5% и депозиту.
- [`docs/adr/0010-disable-character-perks-by-default.md`](file:///d:/ai%20project/Forever%20Trade/docs/adr/0010-disable-character-perks-by-default.md) — Выключение персональных перков Bartering и Master Chef по умолчанию.
- [`docs/adr/0011-unified-portal-crafting-display.md`](file:///d:/ai%20project/Forever%20Trade/docs/adr/0011-unified-portal-crafting-display.md) — Dual-crafting: отображение стандартного крафта наряду с Waylaid Crates на портале.
- [`docs/adr/0012-goblin-terminal-ui-and-vendor-ceiling.md`](file:///d:/ai%20project/Forever%20Trade/docs/adr/0012-goblin-terminal-ui-and-vendor-ceiling.md) — Goblin Terminal UI (100vh), левая sidebar, desktop CSS grid 65/35, DeFiLlama токены, Wowhead Forever tooltips, Vendor Floor/Ceiling, Vendor Shuffle, автоопределение реалма и Shopping List экспорт.
- [`docs/adr/0013-forevertrade-sniper-standalone-stack.md`](file:///d:/ai%20project/Forever%20Trade/docs/adr/0013-forevertrade-sniper-standalone-stack.md) — Автономный стек микро-аддона ForeverTrade_Sniper, Disenchant Shuffle, Hampel filter и рекурсивный DAG Make-vs-Buy.

## Где что

- `run_watcher.py` — Главная входная точка демона: валидация и автоопределение реалма, стартовый импорт существующего `Auctionator.lua`, CLI опции (`--run-once`, `--realm`, `--perk-bartering`, `--perk-master-chef` с дефолтом `False`), запуск мониторинга `watchdog` с дебаунсом 500мс и контролем завершённости записи (`}`).
- `src/database.py` — Схема SQLite (`market.db`, WAL, foreign keys), таблицы `items` (поддержка `vendor_*_copper`), `price_snapshots`, `recipes`, `recipe_reagents`, `_etl_metadata`, загрузка выверенного каталога Era 1.15, ротация старых снимков (`prune_snapshots` с настраиваемым TTL).
- `src/etl.py` — Мониторинг файлов (`watchdog`), безопасный парсер `luadata` без `eval()`, дебаунс 500мс со стабилизацией размера и проверкой символа `}`, автоопределение реалма при отсутствии целевого ключа, отказоустойчивый fallback на `.bak`, нормализация составных ключей (`g:...`, `p:...`), дедупликация снапшотов по SHA-256 и 10-минутному окну.
- `src/analytics.py` — Аналитический движок: вендорный арбитраж ($\ge 50$ меди), Vendor Shuffle (`P_AH < P_vendor_sell`), Vendor Floor/Ceiling (ограничение себестоимости реагентов вендора сверху ценой NPC $\min(P_{\text{AH}}, P_{\text{vendor\_buy}})$), Dual-crafting (стандартные рецепты с $\text{ROI} \ge 25\%$ и сдача Waylaid Crates NPC со 100% закупкой комплекта на AH), Z-score снайпинг со строгой блокировкой ложных сигналов на вендорных товарах и Day-1 hardening ($N < 5$), динамическая перезагрузка `forever_overrides.json` по mtime.
- `src/dashboard.py` — Генератор автономного HTML-дашборда `portal.html` в формате Goblin Terminal UI: одноэкранный десктопный интерфейс с левой sidebar, CSS grid 65/35 (таблица сигналов с внутренней прокруткой и инспектор с единым динамическим графиком Plotly), токенами DeFiLlama, responsive breakpoints (1279/1023/767), клавиатурной сортировкой, тултипами Wowhead Forever (`tooltips.js`), 1-клик копированием названия в буфер обмена с тостом, экспортом сигналов в Shopping List Auctionator (`ForeverTrade^...`), сохранением состояния в `localStorage` (таб, поиск, язык RU/EN, выбранный предмет) при 30с автообновлении.
- `src/ai_advisor.py` — Тактический AI-ассистент: отбор топ-3 возможностей рынка, клиент `AntigravityManagerClient` (`gemini-3.8-flash-high`) и надежный детерминированный эвристический fallback при отсутствии API-ключа или сети.
- `data/items_era_1_15.json` — Базовый каталог предметов Classic Era 1.15 с выверенными идентификаторами и полями `vendor_sell_copper` / `vendor_buy_copper`.
- `forever_overrides.json` — Специфичные данные сервера Forever (контейнеры Waylaid Crates, целевые предметы, награды и кастомные цены).
- `addons/ForeverTrade_Sniper/` — Легковесный микро-аддон (<50 КБ, без Ace3): TOC, Lua-сканер последней страницы с защитой от сдвига, аппаратный выкуп лота по колесу мыши с верификацией слота, сгенерированный `config.lua`.
- `data/disenchant_tables_era.json` — Матрицы матожидания распыления экипировки Classic Era 1.15 и сервера Forever.
- `docs/adr/` — Каталог архитектурных решений проекта (ADR 0001–0013).
- `tests/` — Полный набор из 229 модульных тестов pytest (`test_database.py`, `test_etl.py`, `test_analytics.py`, `test_dashboard.py`, `test_ai_advisor.py`, `test_sniper_addon.py`).

## Подводные камни

1. **Foreign Keys в SQLite:** В БД включен режим `PRAGMA foreign_keys = ON;`. Таблица `price_snapshots` ссылается на `items(item_id)`. При пакетной вставке новых снимков в `src/etl.py` сначала обязательно выполняется `INSERT OR IGNORE INTO items (item_id, name)` для всех встреченных ID, иначе SQLite выбросит ошибку нарушения внешнего ключа.
2. **luadata и .bak fallback:** Игровой клиент WoW перезаписывает `Auctionator.lua` при `/reload` или выходе из игры. В момент записи файл может оказаться заблокирован или частично усечен. Парсер в `src/etl.py` обрабатывает ошибки и автоматически переключается на чтение резервной копии `Auctionator.lua.bak` / `Auctionator.bak` (с проверкой давности не более 2 часов). Не заменяйте вызовы `luadata` на `eval()` или регулярные выражения.
3. **Порт дашборда 10459:** Порт `10459` зарезервирован под фоновый процесс дашборда автопилота (`http://localhost:10459/dashboard.html`). Торговый портал проекта генерируется в виде статичного автономного файла `portal.html` и открывается напрямую в браузере (`file:///...`), не занимая сетевых портов.
4. **Математика Waylaid Crates (100% закупка):** Сдача наполненного ящика NPC приносит фиксированную награду и не облагается аукционной комиссией 5% (выручка берется полностью). Однако закупка недостающего комплекта предметов $K$ всегда рассчитывается по 100% рыночной цены AH без применения вендорной скидки перка Bartering. Отрицательная маржа отсекается: убыточные ящики не попадают в выдачу.
5. **Валидация выхода Z-score снайпинга:** Попадание в зону перепроданности ($Z \le -1.8$) недостаточно для статуса `STRONG_BUY`. Алгоритм обязательно проверяет безубыточный выход по историческому среднему: чистая выручка от перепродажи с учетом комиссии аукциона 5% и стоимости депозита должна строго превышать цену покупки ($\mu \times 0.95 - \text{deposit} > \text{buy\_price}$). Это исключает ложные сигналы на товарах с высокой дисперсией $\sigma$.
6. **Дефолт перков выключен (False):** Перки персонажа (`perk_bartering` — скидка 5% у вендоров, `perk_master_chef` — 1.1x выход при готовке) по умолчанию установлены в `False` как в сигнатурах функций аналитики, так и в CLI аргументах `run_watcher.py`. Это гарантирует консервативную и точную оценку для базовых персонажей.
7. **Дедупликация через _etl_metadata:** Для исключения повторной обработки идентичных снимков `src/etl.py` сохраняет mtime и SHA-256 хэш файла в служебную таблицу `_etl_metadata` базы SQLite. Резервный словарь `_IN_MEMORY_ETL_META` используется лишь в качестве fallback-кэша.
8. **Аппаратные события выкупа лотов (Hardware Events):** Вызов `PlaceAuctionBid` в клиенте WoW защищен и требует прямого действия игрока. Аддон перехватывает колесо мыши (`MOUSEWHEELUP`/`MOUSEWHEELDOWN`) через `SecureActionButtonTemplate` и верифицирует совпадение `actualItemId` и `buyoutPrice` перед покупкой. При закрытии аукциона (`AUCTION_HOUSE_CLOSED`) бинды гарантированно сбрасываются.
9. **Робастная фильтрация выбросов (Hampel Filter):** При расчете рыночной метрики DBMarket тролль-лоты отсекаются по порогу $3 \times \max(\sigma_{\text{MAD}}, 0.10 \times \tilde{P})$, что защищает среднее от поломки при нулевом $\text{MAD}$ на однородных стаках.
10. **Рекурсивный DAG Make-vs-Buy:** Себестоимость полуфабрикатов крафта оптимизируется по минимальной ветке $\min(P_{\text{vendor\_buy}}, P_{\text{AH}}, C_{\text{craft}})$. Рекурсия защищена от циклов через множество посещенных рецептов.

## Окружение

Переменные окружения задаются в файле `.env` (образец в `.env.example`):
- `GEMINI_API_KEY` (или `GOOGLE_API_KEY`) — Ключ Google Generative AI для генерации тактических советов моделью `gemini-3.8-flash-high`. Если ключ не задан, `AntigravityManagerClient` автоматически и прозрачно использует встроенный алгоритмический fallback.
- `WOW_WTF_PATH` — Путь к папке SavedVariables аккаунта WoW (`WTF/Account/<ACCOUNT>/SavedVariables`), откуда читается `Auctionator.lua`. По умолчанию: `data/`.

## Как здесь работает Autopilot

Сборка ведётся навыком `/autopilot`. Требования, спецификация и таски — в `.autopilot/`.
Прогресс — `.autopilot/dashboard.html`. Требование из `manifest.md` может снять
только пользователь. Прерванную сборку продолжает «продолжи автопилот».

### Правка без автопилота

Мелкий баг или доводка после сборки — обычная сессия, без прогона.

Сначала прочитай:
1. `.autopilot/PROGRESS.md` — что не доделано, известные баги, о чём договорились с пользователем.
2. Что правили после последней сборки — первая строка таблицы в «История git».
3. Только если правка их касается: почему сделано так — `docs/adr/`; что значат слова — `CONTEXT.md`;
   что именно просили — `manifest.md` в папке сборки (указана в начале `PROGRESS.md`).

После правки:
- коммит — по правилам из «История git»;
- строка в «Правки без автопилота» в `PROGRESS.md`: дата — что — почему;
- починенный пункт из «Не доделано» или «Известные баги» — удали;
- договорились с пользователем о том, что следующая сборка не должна пересматривать, —
  строка в «Договорились», его словами.

### История git

Целиком не читай — бери срез под вопрос:

| Вопрос | Команда |
|---|---|
| что правили после последней сборки | `git log --oneline "$(git log -1 --format=%h --grep='^Autopilot: сборка сдана')"..HEAD -- . ':(exclude).autopilot'` |
| всё, что делали ради требования | `git log --oneline --grep="R02"` |
| история файла | `git log --oneline -10 -- <файл>` |
| когда появился или исчез код | `git log --oneline -S "<фрагмент>"` |
| почему строка такая | `git blame -L <с>,<по> <файл>`, затем `git show <sha>` |
| что внутри коммита | `git show --stat <sha>` |

Срез длиннее ~30 коммитов — отдай его субагенту и возьми выжимку в десять строк.

Коммит: заголовок — что (`fix: даты в одном формате`), тело — почему, трейлер
`--trailer "Requirements: R02"`, если правка касается требования из `manifest.md`.
Заголовок `Autopilot: сборка сдана` пишет только автопилот — по нему находят последнюю сборку.
<!-- autopilot:end -->