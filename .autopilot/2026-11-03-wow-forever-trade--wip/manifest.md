# Манифест требований

Источник: `2026-11-03-brief.md`. Строку из этого списка может снять **только пользователь**.

| ID | Из брифа (дословно) | Статус | Основание | Где |
|----|---------------------|--------|-----------|-----|
| R01 | «Отказ от установки и зависимости от внутриигрового аддона TSM» | in-ticket | — | T02 |
| R02 | «Собственный микро-аддон ForeverTrade_Sniper (250–300 строк Lua), использующий портированные и защищённые низкоуровневые алгоритмы сканирования TSM» | in-ticket | — | T02 |
| R03 | «Индексация последней страницы и компенсация сдвига (Page Shift): FT_TargetPage, FT_CheckPrevPageNext» | in-ticket | — | T02 |
| R04 | «Событийный Token-Bucket рейт-лимит и пауза при захвате цели: задержка 1.2–1.4 сек, таймаут 3.0 сек» | in-ticket | — | T02 |
| R05 | «Автоматическая пауза сканера при захвате цели (защита от сдвига строк), звуковой алерт» | in-ticket | — | T02 |
| R06 | «Поштучная проверка цены, фильтр минимального профита со стака min_profit_copper» | in-ticket | — | T02 |
| R07 | «LRU-кэш из последних 200 записей (owner и timeLeft исключены из ключа хеша), выбор лота с максимальным totalSaved» | in-ticket | — | T02 |
| R08 | «Безопасный выкуп через колесо мыши (Hardware Event + верификация слота): SecureActionButtonTemplate, SetOverrideBindingClick, FT_ExecuteVerifiedBuy» | in-ticket | — | T02 |
| R09 | «При закрытии окна аукциона (AUCTION_HOUSE_CLOSED) автоматически вызывается FT_DisarmSniperBuy()» | in-ticket | — | T02 |
| R10 | «При входе в мир или /reload аддон выводит в системный чат время генерации конфига (generated_at) и общее число загруженных ID» | in-ticket | — | T02 |
| R11 | «В src/analytics.py реализуется функция export_sniper_addon_config(): генерирует плоскую хеш-таблицу таргетов config.lua» | in-ticket | — | T02 |
| R12 | «Интеграция пути в run_watcher.py (CLI флаг --wow-addon-path)» | in-ticket | — | T02 |
| R13 | «Вычисляет потолок выкупа за 1 единицу предмета по единой формуле MaxBuyout = max(I(L>=0.50)*floor(0.65*dbmarket), floor(0.80*E[DE Value]), P_vendor_sell - Delta_vendor)» | in-ticket | — | T02 |
| R14 | «Delta_vendor = max(50c, floor(1500c / max_stack)) — защита от засорения инвентаря и почты одиночными предметами» | in-ticket | — | T02 |
| R15 | «Из TradeSkillMaster/Data/Disenchanting.lua извлекаются матрицы матожидания E[mats] и сохраняются в data/disenchant_tables_era.json» | in-ticket | — | T01 |
| R16 | «Метод get_disenchant_arbitrage() находит на аукционе экипировку, выставленную дешевле стоимости распыла минус 5% комиссии» | in-ticket | — | T03 |
| R17 | «Новый таб в Goblin Terminal: ✨ Дизэнчант и раскладка дропа в инспекторе» | in-ticket | — | T03 |
| R18 | «Робастный DBMarket и Hampel Filter: замена классической sigma на робастную sigma_MAD = 1.4826 * MAD с отсечением выбросов» | in-ticket | — | T04 |
| R19 | «Экспоненциальное затухание (EWMA) с полураспадом 3.5 дня: w_i = exp(-lambda * delta_t)» | in-ticket | — | T04 |
| R20 | «Таблица кэша item_market_metrics в src/database.py» | in-ticket | — | T04 |
| R21 | «Liquidity Score (L) — расчет коэффициента L in [0.01, 1.0], предметы с L < 0.20 помечаются как ILLIQUID_TRAP» | in-ticket | — | T01 |
| R22 | «Учет ожидаемых потерь залога при повторных выставлениях ((1-L)/L) и заморозки капитала при оценке чистой прибыли Z-score» | in-ticket | — | T01 |
| R23 | «Рекурсивный DAG-крафт (Make-vs-Buy): MatPrice(r) = min(P_vendor_buy, P_ah, C_craft) и расчет полуфабрикатов из базового сырья» | in-ticket | — | T05 |
| R24 | «Дерево промежуточных крафтов в инспекторе Goblin Terminal» | in-ticket | — | T05 |
| R25 | «Найди способ сделать аддон максимально рабочим без тестирования на реальной игре. Говорят можно добыть точные сигнатуры апи без входа в игровой мир. Сделай это клиент wow forever ты знаешь где лежит, либо скопируй у тсм.» | in-ticket | — | T02 |
| R26 | «Код и тесты должны писать разные агенты» | in-ticket | — | T01–T05 |
| R27 | «Активируй скилл Ponytail по-настоящему: прочитай .agents/skills/ponytail/SKILL.md и строго следуй правилам из .agents/rules/ponytail.md» | in-ticket | — | T01–T05 |
