# Границы и правила проекта

## Границы, решённые в спецификации

- `src/dashboard.py`: `build_standalone_dashboard(conn, output_path="portal.html", ai_advice=None, realm=None, has_bartering=False, has_master_chef=False, max_age_hours=None)` остаётся единственной точкой генерации. Внутри неё меняются CSS/HTML/JS и `_build_price_history_chart(conn, realm=None)`; сигнатуры и сериализация `ITEM_STORE`/`STRONG_BUY_ITEM_IDS` не меняются.
- `tests/test_dashboard.py`: существующая fixture `populated_dashboard_db` и реальные вызовы генератора; новые проверки добавляются в этот файл, без чтения исходников вместо вызова.
- `portal.html`: только результат генерации, никогда не редактируется вручную.
- Внешние границы `analytics.py`, ETL, SQLite и addon config не затрагиваются.

## Команды и ограничения исполнения

- Python 3, SQLite, standalone HTML/CSS/JavaScript, Plotly, pytest; зависимости уже установлены, новые не добавлять.
- Полная проверка: `python -m pytest`; один файл: `python -m pytest tests/test_dashboard.py`.
- Штатная генерация: `python run_watcher.py --run-once --realm "Everlook"`.
- Tasks 2–6: сначала test agent изменяет только `tests/test_dashboard.py` и показывает ожидаемый FAIL; затем отдельный coder изменяет только `src/dashboard.py` до PASS. Оркестратор не редактирует эти файлы.
- Никаких git commit, новых веток/worktree, stash/reset/clean, изменений аналитики, ETL, SQLite или торговых формул. `portal.html` не редактировать вручную.
- Существующий пользовательский WIP вне зоны задачи не трогать. Временные screenshots только в `tmp/`; dashboard остаётся на `http://localhost:10459/dashboard.html`.

## Правила проекта из прошлой сборки

- Файлы проекта не копировать в `Interface/AddOns`, держать все внутри репозитория — требование пользователя
- Использовать клиент Antigravity Manager (модель Gemini 3.8 Flash High) вместо Ollama — требование пользователя
- Дашборд автопилота обслуживается строго на порту 10459 без переспауна на другие порты — требование пользователя
- Разделение обязанностей: продакшен-код пишется в `src/`, тесты — в `tests/` без бумажных проверок (доктрина Ponytail)
- Считать Waylaid Crates по двум раздельным стратегиям: продажа комплекта $K$ предметов на AH и чистый арбитраж сдачи ящика NPC
- Использовать строгий белый список вендорных реагентов (`VENDOR_REAGENTS_WHITELIST`), не включая дикую траву и рыбу в закупки у NPC
- Локализация предметов (RU/EN) и мгновенный переключатель языка на клиенте без перезагрузки страницы
- Индивидуальные графики цен и метрик предметов через всплывающий инспектор (`#inspector-chart`) вместо перегруженного общего графика
- 1-click копирование названия предмета (`.copy-btn`) в буфер обмена для быстрой вставки на игровом аукционе
- Выверенные ID предметов и рецептов Era 1.15: Briarthorn (2450), Swiftthistle (2452), Medium Armor Kit (2313), Toughened Leather Gloves (2316) — не подменять ID
- Экономика Waylaid Crates: 100% расход на закупку комплекта на AH без вендорных скидок перка Bartering, выручка сдачи NPC без комиссии AH, отсечение убыточных ящиков (только чистая прибыль > 0)
- Статистический Z-score снайпинг: рекомендация STRONG_BUY только при доказанной чистой прибыли выхода по историческому среднему ($\mu \times 0.95 - \text{deposit} > \text{buy\_price}$)
- Дефолт персональных перков: perk_bartering и perk_master_chef по умолчанию False в CLI и аналитике
- Dual-crafting: отображение стандартного прибыльного крафта наряду с Waylaid Crates в дашборде
- Watcher debounce 500мс со стабилизацией размера и закрывающей скобкой `}` перед парсингом luadata
- Автоопределение реалма в ETL при отсутствии целевого реалма в Auctionator.lua
- Vendor floor/ceiling в крафте: себестоимость реагента вендора ограничена $\min(P_{\text{AH}}, P_{\text{vendor\_buy}})$
- Блокировка STRONG_BUY для предметов, продаваемых NPC по фиксированной цене, при $P_{\text{AH}} \ge P_{\text{vendor\_buy}}$
- Goblin Terminal UI: одноэкранный интерфейс (100vh) с тултипами Wowhead Forever, экспортом Shopping List Auctionator и сохранением состояния в `localStorage`
- API-ключ Gemini локальный, кража исключена (риск перенаправления из аудита §9 снят) — требование пользователя
- Двухконтурная модель Forever Trade: медленный аналитический контур (Python-демон) и сверхбыстрый микро-исполнитель (ForeverTrade_Sniper.lua без сторонних библиотек)
- Аппаратный выкуп лотов по колесу мыши через SecureActionButtonTemplate со строгой верификацией слота перед PlaceAuctionBid
- Защита от сдвига страниц (Page Shift Compensation) через чередование целевой страницы на границе 1–5 лотов
- Робастная фильтрация тролль-выбросов Hampel Filter через MAD с нижним порогом 0.10*P для защиты однородных цен
- Рекурсивный DAG-крафт Make-vs-Buy с выбором минимальной ветки закупки сырья

## G01 — дополнение пользователя 2026-11-04
Весь production-код и тесты пишет только Gemini через Antigravity; оркестратор сам код не пишет. Раздельные coder/tester остаются обязательными.

## T02 — выполнено
Gemini coder: tokens/grid/sidebar; API и торговые данные без изменений. Dashboard suite: 40 passed; полный pytest: 196 passed. Отдельное review: clean, BLOCKING none. Коммиты не создавались по ограничению плана.

## T03 — выполнено
Gemini coder: семь inline SVG, sidebar/topbar/controls, initial/dynamic `aria-pressed` для RU/EN. Все DOM/ID/handlers/localStorage сохранены. Dashboard suite: 41 passed; watcher успешно сгенерировал `portal.html`. Review: clean, BLOCKING none.

## T04 — выполнено
Отдельные Gemini tester/coder: RED на `.data-table`, затем panels/data-table, compact summary, inspector и deal-calculator-grid. API, DOM IDs, торговые данные и поведение сохранены. Dashboard suite: 42 passed; watcher успешно сгенерировал `portal.html`. Коммиты запрещены планом; ticket review не назначен.

## T05 — выполнено
Gemini tester/coder раздельно: RED на старой palette, затем единая dark theme Python overview и JS inspector. Traces, формулы, SQL, dimensions и `Plotly.react` сохранены. Dashboard suite: 43 passed; watcher exit 0. Финальное review назначено на whole-branch stage.

## T06 — выполнено
Gemini tester/coder раздельно: RED на media queries, затем breakpoints 1279/1023/767, reduced motion, `.table-scroll` и keyboard sorting через существующий click handler. Desktop 100vh, DOM/API/данные сохранены. Focused test: 1 passed; dashboard suite: 44 passed; watcher exit 0. Point review не назначен.

## T07 — выполнено
Gemini обновил `docs/architecture.md`, ADR 0012, `AGENTS.md` и `.autopilot/PROGRESS.md`: sidebar, desktop grid 65/35, DeFiLlama tokens, внутренние scroll areas и responsive/a11y соответствуют реализации; торговые модели не менялись. Оркестратор подтвердил полный `python -m pytest`: 200 passed, 0 failed. Итоговый `portal.html` получен штатным watcher с exit 0; git status проверен, коммитов и новых зависимостей нет.
