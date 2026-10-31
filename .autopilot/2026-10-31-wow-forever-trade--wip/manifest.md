# Манифест требований

Источник: `2026-10-31-brief.md`. Строку из этого списка может снять **только пользователь**.

| ID | Из брифа (дословно) | Статус | Основание | Где |
|----|---------------------|--------|-----------|-----|
| R01 | «Развертывание DDL схемы БД: таблицы items, price_snapshots, recipes, recipe_reagents, составные индексы» | done | — | spec §Истории 1 → T01 → 817b05c |
| R02 | «Сбор базового каталога Classic Era 1.15 (item_id, name, quality, item_class, item_subclass, vendor_sell_price, vendor_buy_price)» | done | — | spec §Истории 2 → T01 → 817b05c |
| R03 | «Создание слоя изменений Forever (forever_overrides.json): ящики Waylaid Crates, бивачные предметы, грамоты» | done | — | spec §Истории 3 → T01 → 817b05c |
| R04 | «Формирование графа ремесел: Инженерия, Портняжное дело, Кожевничество, Алхимия» | done | — | spec §Истории 4 → T01 → 817b05c |
| R05 | «Монитор файловой системы (Watcher Engine на watchdog) для WTF/Account/<ACCOUNT>/SavedVariables/Auctionator.lua» | done | — | spec §Истории 5 → T02 → a3bbeb5 |
| R06 | «Безопасный синтаксический анализ AUCTIONATOR_PRICE_DATABASE через luadata с fallback на .bak» | done | — | spec §Истории 6 → T02 → a3bbeb5 |
| R07 | «Дедупликация (окно 10 минут) и нормализация itemId из сигнатуры itemId:enchantId:...» | done | — | spec §Истории 7 → T02 → a3bbeb5 |
| R08 | «Пакетная вставка (Batch Ingestion) в таблицу price_snapshots с логированием в watcher.log» | done | — | spec §Истории 8 → T02 → a3bbeb5 |
| R09 | «Алгоритм вендорного арбитража: P_buyout < V_sell и чистая прибыль >= 50 меди» | done | — | spec §Истории 9 → T03 → b6e1727 |
| R10 | «Калькулятор ремесленной маржи: себестоимость, скидка 5% Bartering, множитель 1.1 Master Chef, комиссия аукциона 5%, ROI >= 25%» | done | — | spec §Истории 10 → T03 → b6e1727 |
| R11 | «Модуль статистического снайпинга (Z-score) по окну 10-20 снимков: триггеры Z <= -1.8 и Z >= 1.6» | done | — | spec §Истории 11 → T03 → b6e1727 |
| R12 | «Генератор статического портала dashboard.html с интерактивными графиками Plotly.js» | done | — | spec §Истории 12 → T04 → 83c9955 |
| R13 | «Интерактивные таблицы сигналов (Вендорный арбитраж, Waylaid Crates, Аномальные сбросы) с цветовой индикацией валют» | done | — | spec §Истории 13 → T04 → 83c9955 |
| R14 | «Система локальных системных уведомлений при вендорной прибыли более 1 золотого» | done | — | spec §Истории 14 → T04 → 83c9955 |
| R15 | «Формирование компактного JSON-контекста (топ-3 вендор, топ-3 крафт ящиков, топ-3 сырьевых просадок)» | done | — | spec §Истории 15 → T05 → 6eada3a |
| R16 | «AI-ассистент рыночной аналитики через Antigravity менеджер (Gemini 3.8) вместо Ollama» | done | — | spec §Истории 16 → T05 → 6eada3a |
| R17 | «Интеграция тактических рекомендаций AI-ассистента в верхний блок автономного дашборда» | done | — | spec §Истории 17 → T05 → 6eada3a |
| R18 | «Дашборд автопилота поднят на http://localhost:10459/dashboard.html без переспауна на другие порты» | done | — | spec §Истории 18 → T01 → 817b05c |
| R19 | «Файлы пока не закидывать в WoW Interface/AddOns, держать все в папке проекта» | done | — | spec §Истории 19 → T01 → 817b05c |
| R20 | «Код и тесты должны писать разные агенты по правилам Ponytail» | done | — | spec §Истории 20 → T01 → 817b05c |
| R21 | «Останавливаешь работу только после полного закрытия плана» | done | — | spec §Истории 21 → T01 → 817b05c |
| R22 | «1.1 Заменить INSERT OR REPLACE на честный SQLite UPSERT (ON CONFLICT ... DO UPDATE) для предотвращения нарушения Foreign Keys» | in-ticket | audit_report.md §1.1, §11 | spec §Истории 22 → T06 |
| R23 | «1.2 Разделить первичную инициализацию схемы init_schema() и фабрику соединений get_connection(), задать busy_timeout = 30000, synchronous = NORMAL и WAL» | in-ticket | audit_report.md §1.2, §11 | spec §Истории 23 → T06 |
| R24 | «1.3 Оптимизировать prune_snapshots: индекс idx_snapshots_timestamp, INTEGER epoch timestamp, удаление батчами по 5000 строк» | in-ticket | audit_report.md §1.3, §11 | spec §Истории 24 → T06 |
| R25 | «2.1 Внедрить trailing-edge дебаунс (500 мс) с проверкой стабилизации размера файла Auctionator.lua вместо 50мс паузы» | in-ticket | audit_report.md §2.1, §11 | spec §Истории 25 → T07 |
| R26 | «2.2 Использовать mtime файла бэкапа при fallback вместо текущего time.time() и запретить инжест бэкапов старше 2 часов» | in-ticket | audit_report.md §2.2, §11 | spec §Истории 26 → T07 |
| R27 | «2.3 Корректно создавать директорию watch_path через os.makedirs и сохранять target_filename = Auctionator.lua» | in-ticket | audit_report.md §2.3, §11 | spec §Истории 27 → T07 |
| R28 | «2.4 Вынести тяжелый пересчет аналитики, обращение к AI и обновление дашборда в фоновую очередь задач queue.Queue, не блокируя watchdog» | in-ticket | audit_report.md §2.4, §10.1, §11 | spec §Истории 28 → T07 |
| R29 | «3.1 Устранить нарушение неравенства Самуэльсона в Z-score: установить min_samples >= 6 и использовать несмещенную дисперсию (n - 1)» | in-ticket | audit_report.md §3.1, §11 | spec §Истории 29 → T08 |
| R30 | «3.2 Заменить эвристику v_buy > 0 на строгий белый список вендорных реагентов, исключив дикорастущую траву и рыбу» | in-ticket | audit_report.md §3.2, §11 | spec §Истории 30 → T08 |
| R31 | «3.3 Учесть безвозвратный залог аукциона AH Deposit Fee: вычитать max(15, int(v_sell * 0.05)) из расчетной прибыли крафта» | in-ticket | audit_report.md §3.3, §11 | spec §Истории 31 → T08 |
| R32 | «4.1, 4.2 Поддержать формат Auctionator v341: извлекать цену из val['m'], поддержать префикс gr: и вложенные таблицы h/l/a» | in-ticket | audit_report.md §4.1, §4.2, §11 | spec §Истории 32 → T07 |
| R33 | «4.3 Поддержать разделитель имени реалма через пробел: ForeverRealm Alliance» | in-ticket | audit_report.md §4.3, §11 | spec §Истории 33 → T07 |
| R34 | «5.1, 5.2 Корректная модель Waylaid Crates: учесть target_item_count и reward_copper, разделить AH-комплекты и сдачу ящиков, поддержать сырьевые ящики 211501-211507» | in-ticket | audit_report.md §5.1, §5.2, §11 | spec §Истории 34 → T08 |
| R35 | «6.1 Устранить Stored XSS в Plotly Chart: экранировать html.escape() и экранировать подстроки </script> при сериализации» | in-ticket | audit_report.md §6.1, §11 | spec §Истории 35 → T09 |
| R36 | «6.2 Предотвратить зависание браузера: ограничить выборку истории графиков топ-8 предметами и даунсэмплить точки до <= 300» | in-ticket | audit_report.md §6.2, §11 | spec §Истории 36 → T09 |
| R37 | «6.3 Исправить инверсию логики шага в AI-баннере: step_text = item.get('action') or item.get('text') or str(item.get('step') or item)» | in-ticket | audit_report.md §6.3, §11 | spec §Истории 37 → T09 |
| R38 | «7.1-7.4 Защитить AI-модуль: санитизация имен предметов, теги <market_data>, передача промпта в agy через stdin, безопасный парсинг None, ключ в x-goog-api-key» | in-ticket | audit_report.md §7.1-7.4, §11 | spec §Истории 38 → T10 |
| R39 | «8.1-8.3 Обработка фаззинга и пограничных случаев: OverflowError в format_currency при inf, фильтрация bool как цены, безопасный escape при name is None» | in-ticket | audit_report.md §8.1-8.3, §11 | spec §Истории 39 → T09 |
