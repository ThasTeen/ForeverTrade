window.STATE =
{
  "version": 2,
  "slug": "wow-forever-trade",
  "dir": "2026-10-31-wow-forever-trade--wip",
  "title": "Рыночный аналитический агент WoW: Forever",
  "mode": "full",
  "depth": "normal",
  "tier": "T2",
  "briefFile": "2026-10-31-brief.md",
  "memoryFile": "AGENTS.md",
  "memoryOwner": "autopilot",
  "skillDir": "C:/Users/thast/.gemini/config/skills/autopilot",
  "baseCommit": null,
  "startedAt": "2026-10-31T19:41:42+03:00",
  "updatedAt": "2026-10-31T19:49:07+03:00",
  "finishedAt": null,
  "stages": [
    {
      "id": "preflight",
      "status": "done",
      "startedAt": "2026-10-31T19:41:42+03:00",
      "finishedAt": "2026-10-31T19:42:41+03:00"
    },
    {
      "id": "manifest",
      "status": "done",
      "startedAt": "2026-10-31T19:42:41+03:00",
      "finishedAt": "2026-10-31T19:43:39+03:00"
    },
    {
      "id": "briefing",
      "status": "skipped",
      "startedAt": "2026-10-31T19:43:22+03:00",
      "finishedAt": "2026-10-31T19:43:22+03:00",
      "note": "полный автомат — самобрифинг"
    },
    {
      "id": "spec",
      "status": "done",
      "startedAt": "2026-10-31T19:43:39+03:00",
      "finishedAt": "2026-10-31T19:48:08+03:00"
    },
    {
      "id": "plan",
      "status": "active",
      "startedAt": "2026-10-31T19:48:08+03:00"
    },
    {
      "id": "build",
      "status": "pending"
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
    "total": 21,
    "done": 0,
    "inTicket": 21,
    "inSpec": 0,
    "placeholder": 0,
    "deferred": 0,
    "dropped": 0
  },
  "tickets": [
    {
      "id": "01",
      "title": "База данных, каталоги предметов и граф рецептов",
      "requirements": [
        "R01",
        "R02",
        "R03",
        "R04",
        "R18",
        "R19",
        "R20",
        "R21"
      ],
      "blockedBy": [],
      "wave": 1,
      "zone": [
        "src/database.py",
        "data/",
        "forever_overrides.json",
        "tests/test_database.py"
      ],
      "review": "да — фундамент",
      "model": "сильная",
      "status": "pending",
      "retries": 0,
      "repairs": 0
    },
    {
      "id": "02",
      "title": "Мониторинг файлов и пайплайн ETL",
      "requirements": [
        "R05",
        "R06",
        "R07",
        "R08"
      ],
      "blockedBy": [
        "01"
      ],
      "wave": 2,
      "zone": [
        "src/etl.py",
        "tests/test_etl.py"
      ],
      "review": "нет",
      "model": "обычная",
      "status": "pending",
      "retries": 0,
      "repairs": 0
    },
    {
      "id": "03",
      "title": "Аналитическое ядро и арбитражные расчеты",
      "requirements": [
        "R09",
        "R10",
        "R11"
      ],
      "blockedBy": [
        "01"
      ],
      "wave": 2,
      "zone": [
        "src/analytics.py",
        "tests/test_analytics.py"
      ],
      "review": "нет",
      "model": "сильная",
      "status": "pending",
      "retries": 0,
      "repairs": 0
    },
    {
      "id": "04",
      "title": "Автономный портал и система оповещений",
      "requirements": [
        "R12",
        "R13",
        "R14"
      ],
      "blockedBy": [
        "02",
        "03"
      ],
      "wave": 3,
      "zone": [
        "src/dashboard.py",
        "tests/test_dashboard.py"
      ],
      "review": "нет",
      "model": "обычная",
      "status": "pending",
      "retries": 0,
      "repairs": 0
    },
    {
      "id": "05",
      "title": "Интеллектуальный рыночный AI-ассистент",
      "requirements": [
        "R15",
        "R16",
        "R17"
      ],
      "blockedBy": [
        "03",
        "04"
      ],
      "wave": 4,
      "zone": [
        "src/ai_advisor.py",
        "tests/test_ai_advisor.py"
      ],
      "review": "нет",
      "model": "сильная",
      "status": "pending",
      "retries": 0,
      "repairs": 0
    }
  ],
  "tests": null,
  "debt": {
    "placeholders": [],
    "assumptions": [],
    "emptyEnv": []
  },
  "additions": [],
  "coverage": {
    "found": 6,
    "fixed": 6,
    "deferred": 0,
    "items": [
      "R02: источник данных каталога Era 1.15",
      "R03/R10: связь Waylaid Crates и кулинарные рецепты для Master Chef",
      "R05: конфигурация пути к Auctionator.lua",
      "R08: очистка снапшотов старше 30 дней",
      "R12/R13: детализация графиков сырья и трех секций сигналов",
      "R16: спецификация клиента Antigravity Manager"
    ]
  },
  "concerns": [],
  "blind": null,
  "beats": [
    "2026-10-31T19:41:42+03:00",
    "2026-10-31T19:42:41+03:00",
    "2026-10-31T19:43:08+03:00",
    "2026-10-31T19:43:22+03:00",
    "2026-10-31T19:43:39+03:00",
    "2026-10-31T19:47:28+03:00",
    "2026-10-31T19:48:08+03:00",
    "2026-10-31T19:48:20+03:00",
    "2026-10-31T19:49:01+03:00",
    "2026-10-31T19:49:07+03:00"
  ],
  "rev": 12,
  "waiting": null,
  "waits": [],
  "log": [
    {
      "t": "2026-10-31T19:41:42+03:00",
      "k": "init"
    },
    {
      "t": "2026-10-31T19:42:41+03:00",
      "k": "stage",
      "id": "manifest",
      "act": "start"
    },
    {
      "t": "2026-10-31T19:43:22+03:00",
      "k": "stage",
      "id": "briefing",
      "act": "skip",
      "note": "полный автомат — самобрифинг"
    },
    {
      "t": "2026-10-31T19:43:39+03:00",
      "k": "gate",
      "g": "G1",
      "ok": false,
      "open": 21,
      "total": 21
    },
    {
      "t": "2026-10-31T19:43:39+03:00",
      "k": "stage",
      "id": "spec",
      "act": "start"
    },
    {
      "t": "2026-10-31T19:47:28+03:00",
      "k": "gate",
      "g": "G2",
      "ok": true,
      "found": 6,
      "fixed": 6,
      "deferred": 0
    },
    {
      "t": "2026-10-31T19:48:08+03:00",
      "k": "stage",
      "id": "plan",
      "act": "start"
    },
    {
      "t": "2026-10-31T19:48:20+03:00",
      "k": "tier",
      "v": "T2"
    },
    {
      "t": "2026-10-31T19:49:01+03:00",
      "k": "tickets",
      "n": 5
    },
    {
      "t": "2026-10-31T19:49:07+03:00",
      "k": "gate",
      "g": "G3",
      "ok": true,
      "problems": 0
    }
  ],
  "reqList": [
    {
      "id": "R01",
      "text": "«Развертывание DDL схемы БД: таблицы items, price_snapshots, recipes, recipe_reagents, составные индексы»",
      "status": "in-ticket"
    },
    {
      "id": "R02",
      "text": "«Сбор базового каталога Classic Era 1.15 (item_id, name, quality, item_class, item_subclass, vendor_sell_price, vendor_buy_price)»",
      "status": "in-ticket"
    },
    {
      "id": "R03",
      "text": "«Создание слоя изменений Forever (forever_overrides.json): ящики Waylaid Crates, бивачные предметы, грамоты»",
      "status": "in-ticket"
    },
    {
      "id": "R04",
      "text": "«Формирование графа ремесел: Инженерия, Портняжное дело, Кожевничество, Алхимия»",
      "status": "in-ticket"
    },
    {
      "id": "R05",
      "text": "«Монитор файловой системы (Watcher Engine на watchdog) для WTF/Account/<ACCOUNT>/SavedVariables/Auctionator.lua»",
      "status": "in-ticket"
    },
    {
      "id": "R06",
      "text": "«Безопасный синтаксический анализ AUCTIONATOR_PRICE_DATABASE через luadata с fallback на .bak»",
      "status": "in-ticket"
    },
    {
      "id": "R07",
      "text": "«Дедупликация (окно 10 минут) и нормализация itemId из сигнатуры itemId:enchantId:...»",
      "status": "in-ticket"
    },
    {
      "id": "R08",
      "text": "«Пакетная вставка (Batch Ingestion) в таблицу price_snapshots с логированием в watcher.log»",
      "status": "in-ticket"
    },
    {
      "id": "R09",
      "text": "«Алгоритм вендорного арбитража: P_buyout < V_sell и чистая прибыль >= 50 меди»",
      "status": "in-ticket"
    },
    {
      "id": "R10",
      "text": "«Калькулятор ремесленной маржи: себестоимость, скидка 5% Bartering, множитель 1.1 Master Chef, комиссия аукциона 5%, ROI >= 25%»",
      "status": "in-ticket"
    },
    {
      "id": "R11",
      "text": "«Модуль статистического снайпинга (Z-score) по окну 10-20 снимков: триггеры Z <= -1.8 и Z >= 1.6»",
      "status": "in-ticket"
    },
    {
      "id": "R12",
      "text": "«Генератор статического портала dashboard.html с интерактивными графиками Plotly.js»",
      "status": "in-ticket"
    },
    {
      "id": "R13",
      "text": "«Интерактивные таблицы сигналов (Вендорный арбитраж, Waylaid Crates, Аномальные сбросы) с цветовой индикацией валют»",
      "status": "in-ticket"
    },
    {
      "id": "R14",
      "text": "«Система локальных системных уведомлений при вендорной прибыли более 1 золотого»",
      "status": "in-ticket"
    },
    {
      "id": "R15",
      "text": "«Формирование компактного JSON-контекста (топ-3 вендор, топ-3 крафт ящиков, топ-3 сырьевых просадок)»",
      "status": "in-ticket"
    },
    {
      "id": "R16",
      "text": "«AI-ассистент рыночной аналитики через Antigravity менеджер (Gemini 3.8) вместо Ollama»",
      "status": "in-ticket"
    },
    {
      "id": "R17",
      "text": "«Интеграция тактических рекомендаций AI-ассистента в верхний блок автономного дашборда»",
      "status": "in-ticket"
    },
    {
      "id": "R18",
      "text": "«Дашборд автопилота поднят на http://localhost:10459/dashboard.html без переспауна на другие порты»",
      "status": "in-ticket"
    },
    {
      "id": "R19",
      "text": "«Файлы пока не закидывать в WoW Interface/AddOns, держать все в папке проекта»",
      "status": "in-ticket"
    },
    {
      "id": "R20",
      "text": "«Код и тесты должны писать разные агенты по правилам Ponytail»",
      "status": "in-ticket"
    },
    {
      "id": "R21",
      "text": "«Останавливаешь работу только после полного закрытия плана»",
      "status": "in-ticket"
    }
  ],
  "gates": {
    "G1": {
      "ok": false,
      "at": "2026-10-31T19:43:39+03:00",
      "open": 21,
      "total": 21
    },
    "G2": {
      "ok": true,
      "at": "2026-10-31T19:47:28+03:00",
      "found": 6,
      "fixed": 6,
      "deferred": 0
    },
    "G3": {
      "ok": true,
      "at": "2026-10-31T19:49:07+03:00",
      "problems": 0
    }
  },
  "result": null
}
