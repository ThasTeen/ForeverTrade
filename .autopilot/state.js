window.STATE =
{
  "version": 2,
  "slug": "wow-forever-trade",
  "dir": "2026-11-03-wow-forever-trade--wip",
  "title": "ForeverTrade Sniper v2.1",
  "mode": "full",
  "depth": "normal",
  "tier": null,
  "briefFile": "2026-11-03-brief.md",
  "memoryFile": "AGENTS.md",
  "memoryOwner": "autopilot",
  "skillDir": "C:/Users/thast/.gemini/config/skills/autopilot",
  "baseCommit": null,
  "startedAt": "2026-11-03T00:12:46+03:00",
  "updatedAt": "2026-11-03T00:33:17+03:00",
  "finishedAt": null,
  "stages": [
    {
      "id": "preflight",
      "status": "done",
      "startedAt": "2026-11-03T00:12:46+03:00",
      "finishedAt": "2026-11-03T00:13:47+03:00"
    },
    {
      "id": "manifest",
      "status": "done",
      "startedAt": "2026-11-03T00:13:47+03:00",
      "finishedAt": "2026-11-03T00:13:47+03:00"
    },
    {
      "id": "briefing",
      "status": "skipped",
      "startedAt": "2026-11-03T00:13:47+03:00",
      "finishedAt": "2026-11-03T00:13:51+03:00",
      "note": "полный автомат — самобрифинг"
    },
    {
      "id": "spec",
      "status": "done",
      "startedAt": "2026-11-03T00:13:54+03:00",
      "finishedAt": "2026-11-03T00:14:59+03:00"
    },
    {
      "id": "plan",
      "status": "done",
      "startedAt": "2026-11-03T00:14:59+03:00",
      "finishedAt": "2026-11-03T00:17:23+03:00"
    },
    {
      "id": "build",
      "status": "active",
      "startedAt": "2026-11-03T00:17:23+03:00"
    },
    {
      "id": "review",
      "status": "pending"
    },
    {
      "id": "final",
      "status": "pending"
    }
  ],
  "requirements": {
    "total": 27,
    "done": 3,
    "inTicket": 24,
    "inSpec": 0,
    "placeholder": 0,
    "deferred": 0,
    "dropped": 0
  },
  "tickets": [
    {
      "id": "01",
      "title": "Портирование таблиц распыления и Liquidity Shield",
      "requirements": [
        "R15",
        "R21",
        "R22",
        "R26",
        "R27"
      ],
      "blockedBy": [],
      "wave": 1,
      "zone": [
        "data/disenchant_tables_era.json",
        "src/analytics.py",
        "tests/test_analytics.py"
      ],
      "review": "да",
      "model": "сильная",
      "status": "done",
      "retries": 0,
      "repairs": 0,
      "startedAt": "2026-11-03T00:17:30+03:00",
      "finishedAt": "2026-11-03T00:33:13+03:00",
      "tests": {
        "passed": 184,
        "failed": 0
      },
      "commit": "8393e2a"
    },
    {
      "id": "02",
      "title": "Микро-аддон ForeverTrade_Sniper и экспорт config.lua",
      "requirements": [
        "R01",
        "R02",
        "R03",
        "R04",
        "R05",
        "R06",
        "R07",
        "R08",
        "R09",
        "R10",
        "R11",
        "R12",
        "R13",
        "R14",
        "R25",
        "R26",
        "R27"
      ],
      "blockedBy": [
        "01"
      ],
      "wave": 2,
      "zone": [
        "addons/ForeverTrade_Sniper/",
        "src/analytics.py",
        "run_watcher.py",
        "tests/test_analytics.py"
      ],
      "review": "да",
      "model": "сильная",
      "status": "in-progress",
      "retries": 0,
      "repairs": 0,
      "startedAt": "2026-11-03T00:33:17+03:00"
    },
    {
      "id": "03",
      "title": "Disenchant Shuffle в ядре и UI Goblin Terminal",
      "requirements": [
        "R16",
        "R17",
        "R26",
        "R27"
      ],
      "blockedBy": [
        "02"
      ],
      "wave": 3,
      "zone": [
        "src/analytics.py",
        "src/dashboard.py",
        "tests/test_analytics.py",
        "tests/test_dashboard.py"
      ],
      "review": "да",
      "model": "сильная",
      "status": "pending",
      "retries": 0,
      "repairs": 0
    },
    {
      "id": "04",
      "title": "Робастный DBMarket (Hampel Filter + EWMA) и таблица метрик",
      "requirements": [
        "R18",
        "R19",
        "R20",
        "R26",
        "R27"
      ],
      "blockedBy": [
        "03"
      ],
      "wave": 4,
      "zone": [
        "src/database.py",
        "src/analytics.py",
        "tests/test_database.py",
        "tests/test_analytics.py"
      ],
      "review": "да",
      "model": "сильная",
      "status": "pending",
      "retries": 0,
      "repairs": 0
    },
    {
      "id": "05",
      "title": "Рекурсивный DAG Crafting Engine",
      "requirements": [
        "R23",
        "R24",
        "R26",
        "R27"
      ],
      "blockedBy": [
        "04"
      ],
      "wave": 5,
      "zone": [
        "src/analytics.py",
        "src/dashboard.py",
        "tests/test_analytics.py",
        "tests/test_dashboard.py"
      ],
      "review": "да",
      "model": "сильная",
      "status": "pending",
      "retries": 0,
      "repairs": 0
    }
  ],
  "tests": {
    "passed": 184,
    "failed": 0
  },
  "debt": {
    "placeholders": [],
    "assumptions": [],
    "emptyEnv": []
  },
  "additions": [],
  "coverage": null,
  "concerns": [],
  "blind": null,
  "beats": [
    "2026-11-03T00:12:46+03:00",
    "2026-11-03T00:13:23+03:00",
    "2026-11-03T00:13:47+03:00",
    "2026-11-03T00:13:51+03:00",
    "2026-11-03T00:13:54+03:00",
    "2026-11-03T00:14:59+03:00",
    "2026-11-03T00:15:52+03:00",
    "2026-11-03T00:15:55+03:00",
    "2026-11-03T00:16:41+03:00",
    "2026-11-03T00:16:44+03:00",
    "2026-11-03T00:17:16+03:00",
    "2026-11-03T00:17:19+03:00",
    "2026-11-03T00:17:23+03:00",
    "2026-11-03T00:17:30+03:00",
    "2026-11-03T00:33:13+03:00",
    "2026-11-03T00:33:17+03:00"
  ],
  "rev": 17,
  "waiting": null,
  "waits": [],
  "log": [
    {
      "t": "2026-11-03T00:12:46+03:00",
      "k": "init"
    },
    {
      "t": "2026-11-03T00:13:47+03:00",
      "k": "stage",
      "id": "briefing",
      "act": "start"
    },
    {
      "t": "2026-11-03T00:13:51+03:00",
      "k": "stage",
      "id": "briefing",
      "act": "skip",
      "note": "полный автомат — самобрифинг"
    },
    {
      "t": "2026-11-03T00:13:54+03:00",
      "k": "gate",
      "g": "G1",
      "ok": false,
      "open": 27,
      "total": 27
    },
    {
      "t": "2026-11-03T00:13:54+03:00",
      "k": "stage",
      "id": "spec",
      "act": "start"
    },
    {
      "t": "2026-11-03T00:14:59+03:00",
      "k": "stage",
      "id": "plan",
      "act": "start"
    },
    {
      "t": "2026-11-03T00:15:52+03:00",
      "k": "tickets",
      "n": 5
    },
    {
      "t": "2026-11-03T00:15:55+03:00",
      "k": "gate",
      "g": "G3",
      "ok": false,
      "problems": 42
    },
    {
      "t": "2026-11-03T00:16:41+03:00",
      "k": "tickets",
      "n": 5
    },
    {
      "t": "2026-11-03T00:16:44+03:00",
      "k": "gate",
      "g": "G3",
      "ok": false,
      "problems": 4
    },
    {
      "t": "2026-11-03T00:17:16+03:00",
      "k": "tickets",
      "n": 5
    },
    {
      "t": "2026-11-03T00:17:19+03:00",
      "k": "gate",
      "g": "G3",
      "ok": true,
      "problems": 0
    },
    {
      "t": "2026-11-03T00:17:23+03:00",
      "k": "stage",
      "id": "build",
      "act": "start"
    },
    {
      "t": "2026-11-03T00:17:30+03:00",
      "k": "ticket",
      "ids": [
        "01"
      ],
      "act": "start"
    },
    {
      "t": "2026-11-03T00:33:13+03:00",
      "k": "ticket",
      "ids": [
        "01"
      ],
      "act": "done",
      "tests": {
        "passed": 184,
        "failed": 0
      }
    },
    {
      "t": "2026-11-03T00:33:17+03:00",
      "k": "ticket",
      "ids": [
        "02"
      ],
      "act": "start"
    }
  ],
  "reqList": [
    {
      "id": "R01",
      "text": "«Отказ от установки и зависимости от внутриигрового аддона TSM»",
      "status": "in-ticket"
    },
    {
      "id": "R02",
      "text": "«Собственный микро-аддон ForeverTrade_Sniper (250–300 строк Lua), использующий портированные и защищённые низкоуровневые алгоритмы сканирования TSM»",
      "status": "in-ticket"
    },
    {
      "id": "R03",
      "text": "«Индексация последней страницы и компенсация сдвига (Page Shift): FT_TargetPage, FT_CheckPrevPageNext»",
      "status": "in-ticket"
    },
    {
      "id": "R04",
      "text": "«Событийный Token-Bucket рейт-лимит и пауза при захвате цели: задержка 1.2–1.4 сек, таймаут 3.0 сек»",
      "status": "in-ticket"
    },
    {
      "id": "R05",
      "text": "«Автоматическая пауза сканера при захвате цели (защита от сдвига строк), звуковой алерт»",
      "status": "in-ticket"
    },
    {
      "id": "R06",
      "text": "«Поштучная проверка цены, фильтр минимального профита со стака min_profit_copper»",
      "status": "in-ticket"
    },
    {
      "id": "R07",
      "text": "«LRU-кэш из последних 200 записей (owner и timeLeft исключены из ключа хеша), выбор лота с максимальным totalSaved»",
      "status": "in-ticket"
    },
    {
      "id": "R08",
      "text": "«Безопасный выкуп через колесо мыши (Hardware Event + верификация слота): SecureActionButtonTemplate, SetOverrideBindingClick, FT_ExecuteVerifiedBuy»",
      "status": "in-ticket"
    },
    {
      "id": "R09",
      "text": "«При закрытии окна аукциона (AUCTION_HOUSE_CLOSED) автоматически вызывается FT_DisarmSniperBuy()»",
      "status": "in-ticket"
    },
    {
      "id": "R10",
      "text": "«При входе в мир или /reload аддон выводит в системный чат время генерации конфига (generated_at) и общее число загруженных ID»",
      "status": "in-ticket"
    },
    {
      "id": "R11",
      "text": "«В src/analytics.py реализуется функция export_sniper_addon_config(): генерирует плоскую хеш-таблицу таргетов config.lua»",
      "status": "in-ticket"
    },
    {
      "id": "R12",
      "text": "«Интеграция пути в run_watcher.py (CLI флаг --wow-addon-path)»",
      "status": "in-ticket"
    },
    {
      "id": "R13",
      "text": "«Вычисляет потолок выкупа за 1 единицу предмета по единой формуле MaxBuyout = max(I(L>=0.50)floor(0.65dbmarket), floor(0.80E[DE Value]), P_vendor_sell - Delta_vendor)»",
      "status": "in-ticket"
    },
    {
      "id": "R14",
      "text": "«Delta_vendor = max(50c, floor(1500c / max_stack)) — защита от засорения инвентаря и почты одиночными предметами»",
      "status": "in-ticket"
    },
    {
      "id": "R15",
      "text": "«Из TradeSkillMaster/Data/Disenchanting.lua извлекаются матрицы матожидания E[mats] и сохраняются в data/disenchant_tables_era.json»",
      "status": "done"
    },
    {
      "id": "R16",
      "text": "«Метод get_disenchant_arbitrage() находит на аукционе экипировку, выставленную дешевле стоимости распыла минус 5% комиссии»",
      "status": "in-ticket"
    },
    {
      "id": "R17",
      "text": "«Новый таб в Goblin Terminal: ✨ Дизэнчант и раскладка дропа в инспекторе»",
      "status": "in-ticket"
    },
    {
      "id": "R18",
      "text": "«Робастный DBMarket и Hampel Filter: замена классической sigma на робастную sigma_MAD = 1.4826  MAD с отсечением выбросов»",
      "status": "in-ticket"
    },
    {
      "id": "R19",
      "text": "«Экспоненциальное затухание (EWMA) с полураспадом 3.5 дня: w_i = exp(-lambda  delta_t)»",
      "status": "in-ticket"
    },
    {
      "id": "R20",
      "text": "«Таблица кэша item_market_metrics в src/database.py»",
      "status": "in-ticket"
    },
    {
      "id": "R21",
      "text": "«Liquidity Score (L) — расчет коэффициента L in [0.01, 1.0], предметы с L < 0.20 помечаются как ILLIQUID_TRAP»",
      "status": "done"
    },
    {
      "id": "R22",
      "text": "«Учет ожидаемых потерь залога при повторных выставлениях ((1-L)/L) и заморозки капитала при оценке чистой прибыли Z-score»",
      "status": "done"
    },
    {
      "id": "R23",
      "text": "«Рекурсивный DAG-крафт (Make-vs-Buy): MatPrice(r) = min(P_vendor_buy, P_ah, C_craft) и расчет полуфабрикатов из базового сырья»",
      "status": "in-ticket"
    },
    {
      "id": "R24",
      "text": "«Дерево промежуточных крафтов в инспекторе Goblin Terminal»",
      "status": "in-ticket"
    },
    {
      "id": "R25",
      "text": "«Найди способ сделать аддон максимально рабочим без тестирования на реальной игре. Говорят можно добыть точные сигнатуры апи без входа в игровой мир. Сделай это клиент wow forever ты знаешь где лежит, либо скопируй у тсм.»",
      "status": "in-ticket"
    },
    {
      "id": "R26",
      "text": "«Код и тесты должны писать разные агенты»",
      "status": "in-ticket"
    },
    {
      "id": "R27",
      "text": "«Активируй скилл Ponytail по-настоящему: прочитай .agents/skills/ponytail/SKILL.md и строго следуй правилам из .agents/rules/ponytail.md»",
      "status": "in-ticket"
    }
  ],
  "gates": {
    "G1": {
      "ok": false,
      "at": "2026-11-03T00:13:54+03:00",
      "open": 27,
      "total": 27
    },
    "G3": {
      "ok": true,
      "at": "2026-11-03T00:17:19+03:00",
      "problems": 0
    }
  },
  "result": null
}
