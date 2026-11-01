# Изначальная задача (2026-11-01)

> Записано 2026-11-01. Текст задачи не редактируется — он эталон, с которым
> сверяется готовый результат. Всё, что сказано позже, дописывается
> в «Дополнения» ниже.

/autopilot изучии @[d:\ai project\Forever Trade\audit\forevertrade.md] Работай строго по скиллу автопилот и /ponytail. Не забудь поднять дашборт. Код и тесты должны писать разные агенты. Останавливаешь работу только после полного закрытия плана. Активируй скилл Ponytail по-настоящему: прочитай .agents/skills/ponytail/SKILL.md и строго следуй правилам из .agents/rules/ponytail.md http://localhost:10459/dashboard.html, отслеживай состояние через него и не пытайся переспаунить на другие порты.

Адверсариальное ревью и план развития проекта ForeverTrade (под старт WoW Forever 5 ноября):
Ticket 18: Auctionator Watcher Debounce & Realm Auto-Discovery (run_watcher.py, src/etl.py, tests/test_etl.py)
Ticket 19: Vendor Floor/Ceiling & Day-1 Analytics Hardening (data/items_era_1_15.json, src/analytics.py, tests/test_analytics.py)
Ticket 20: Single-Screen Terminal UI, Lazy Plotly Inspector & Auctionator Shopping List Export (src/dashboard.py, tests/test_dashboard.py)

## Дополнения
