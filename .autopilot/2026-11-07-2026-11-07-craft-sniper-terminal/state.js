window.STATE =
{
  "version": 2,
  "slug": "2026-11-07-craft-sniper-terminal",
  "dir": "2026-11-07-2026-11-07-craft-sniper-terminal",
  "title": "Биржевые графики, интеграция ProfitCrafter и целевой снайп перекрафта",
  "mode": "semi",
  "depth": "normal",
  "tier": null,
  "briefFile": null,
  "memoryFile": "AGENTS.md",
  "memoryOwner": "autopilot",
  "skillDir": null,
  "baseCommit": "a54b172",
  "startedAt": "2026-11-07T01:38:21+03:00",
  "updatedAt": "2026-11-07T02:37:44+03:00",
  "finishedAt": "2026-11-07T02:37:44+03:00",
  "stages": [
    {
      "id": "preflight",
      "status": "done",
      "startedAt": "2026-11-07T01:38:21+03:00",
      "finishedAt": "2026-11-07T01:38:57+03:00"
    },
    {
      "id": "manifest",
      "status": "done",
      "startedAt": "2026-11-07T01:38:57+03:00",
      "finishedAt": "2026-11-07T01:38:57+03:00"
    },
    {
      "id": "briefing",
      "status": "done",
      "startedAt": "2026-11-07T01:38:57+03:00",
      "finishedAt": "2026-11-07T01:45:36+03:00"
    },
    {
      "id": "spec",
      "status": "done",
      "startedAt": "2026-11-07T01:45:36+03:00",
      "finishedAt": "2026-11-07T01:45:36+03:00"
    },
    {
      "id": "plan",
      "status": "done",
      "startedAt": "2026-11-07T01:45:36+03:00",
      "finishedAt": "2026-11-07T01:45:36+03:00"
    },
    {
      "id": "build",
      "status": "done",
      "startedAt": "2026-11-07T01:45:36+03:00",
      "finishedAt": "2026-11-07T02:37:37+03:00"
    },
    {
      "id": "review",
      "status": "skipped",
      "note": "не понадобился",
      "finishedAt": "2026-11-07T02:37:44+03:00"
    },
    {
      "id": "final",
      "status": "done",
      "finishedAt": "2026-11-07T02:37:44+03:00",
      "startedAt": "2026-11-07T02:37:44+03:00"
    }
  ],
  "requirements": {
    "total": 14,
    "done": 14,
    "inTicket": 0,
    "inSpec": 0,
    "placeholder": 0,
    "deferred": 0,
    "dropped": 0
  },
  "tickets": [
    {
      "id": "01",
      "title": "Подготовка и синхронизация дашборда",
      "requirements": [
        "R01",
        "R02",
        "R04",
        "R05",
        "R06"
      ],
      "blockedBy": [],
      "wave": 1,
      "zone": [
        ".autopilot/"
      ],
      "review": null,
      "model": null,
      "status": "done",
      "retries": 0,
      "repairs": 0,
      "startedAt": "2026-11-07T01:45:44+03:00",
      "finishedAt": "2026-11-07T01:45:59+03:00",
      "commit": "a54b172"
    },
    {
      "id": "02",
      "title": "Биржевой график на портале (Candlestick OHLC + Volume, тумблер Свечи/Линия, таймфреймы)",
      "requirements": [
        "R01",
        "R03",
        "R04",
        "R07",
        "R13"
      ],
      "blockedBy": [
        "01"
      ],
      "wave": 2,
      "zone": [
        "src/dashboard.py"
      ],
      "review": null,
      "model": null,
      "status": "done",
      "retries": 0,
      "repairs": 0,
      "startedAt": "2026-11-07T01:46:21+03:00",
      "finishedAt": "2026-11-07T01:58:18+03:00",
      "tests": {
        "passed": 231,
        "failed": 0
      },
      "commit": "c954894"
    },
    {
      "id": "03",
      "title": "Интеграция с ProfitCrafterForever: парсинг профессий и расчет потолка перекрафта",
      "requirements": [
        "R01",
        "R03",
        "R04",
        "R08",
        "R10",
        "R13"
      ],
      "blockedBy": [
        "01"
      ],
      "wave": 2,
      "zone": [
        "src/etl.py",
        "src/analytics.py",
        "run_watcher.py"
      ],
      "review": null,
      "model": null,
      "status": "done",
      "retries": 0,
      "repairs": 0,
      "startedAt": "2026-11-07T01:58:27+03:00",
      "finishedAt": "2026-11-07T02:12:36+03:00",
      "commit": "c5d8a27"
    },
    {
      "id": "04",
      "title": "Интерфейс снайпера внутри Auctionator: выбор предметов и цен",
      "requirements": [
        "R01",
        "R03",
        "R04",
        "R09",
        "R10",
        "R11",
        "R12",
        "R13"
      ],
      "blockedBy": [
        "02",
        "03"
      ],
      "wave": 3,
      "zone": [
        "addons/ForeverTrade_Sniper/ForeverTrade_Sniper.lua"
      ],
      "review": null,
      "model": null,
      "status": "done",
      "retries": 0,
      "repairs": 0,
      "startedAt": "2026-11-07T02:12:49+03:00",
      "finishedAt": "2026-11-07T02:24:25+03:00",
      "commit": "28c791c"
    },
    {
      "id": "05",
      "title": "Обновление тестов и валидация",
      "requirements": [
        "R01",
        "R03",
        "R04",
        "R05",
        "R13",
        "R14"
      ],
      "blockedBy": [
        "02",
        "03",
        "04"
      ],
      "wave": 4,
      "zone": [
        "tests/"
      ],
      "review": null,
      "model": null,
      "status": "done",
      "retries": 0,
      "repairs": 0,
      "startedAt": "2026-11-07T02:24:29+03:00",
      "finishedAt": "2026-11-07T02:36:43+03:00",
      "tests": {
        "passed": 242,
        "failed": 0
      },
      "commit": "8e502b4"
    },
    {
      "id": "06",
      "title": "Финальная приёмка и сдача прогона Autopilot",
      "requirements": [
        "R01",
        "R02",
        "R04",
        "R06",
        "R14"
      ],
      "blockedBy": [
        "05"
      ],
      "wave": 5,
      "zone": [
        ".autopilot/"
      ],
      "review": null,
      "model": null,
      "status": "done",
      "retries": 0,
      "repairs": 0,
      "startedAt": "2026-11-07T02:36:47+03:00",
      "finishedAt": "2026-11-07T02:37:33+03:00",
      "tests": {
        "passed": 242,
        "failed": 0
      },
      "commit": "8e502b4"
    }
  ],
  "tests": {
    "passed": 242,
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
    "2026-11-07T01:38:21+03:00",
    "2026-11-07T01:38:57+03:00",
    "2026-11-07T01:39:01+03:00",
    "2026-11-07T01:39:10+03:00",
    "2026-11-07T01:42:04+03:00",
    "2026-11-07T01:45:25+03:00",
    "2026-11-07T01:45:30+03:00",
    "2026-11-07T01:45:36+03:00",
    "2026-11-07T01:45:44+03:00",
    "2026-11-07T01:45:59+03:00",
    "2026-11-07T01:46:21+03:00",
    "2026-11-07T01:58:18+03:00",
    "2026-11-07T01:58:27+03:00",
    "2026-11-07T02:12:36+03:00",
    "2026-11-07T02:12:49+03:00",
    "2026-11-07T02:24:25+03:00",
    "2026-11-07T02:24:29+03:00",
    "2026-11-07T02:36:43+03:00",
    "2026-11-07T02:36:47+03:00",
    "2026-11-07T02:37:33+03:00",
    "2026-11-07T02:37:37+03:00",
    "2026-11-07T02:37:44+03:00"
  ],
  "rev": 25,
  "waiting": null,
  "waits": [
    {
      "kind": "briefing",
      "from": "2026-11-07T01:39:01+03:00",
      "to": "2026-11-07T01:42:04+03:00"
    }
  ],
  "log": [
    {
      "t": "2026-11-07T01:38:21+03:00",
      "k": "init"
    },
    {
      "t": "2026-11-07T01:38:57+03:00",
      "k": "stage",
      "id": "briefing",
      "act": "start"
    },
    {
      "t": "2026-11-07T01:39:01+03:00",
      "k": "ask",
      "kind": "briefing",
      "note": "уточнение UI снайпера и типа биржевого графика"
    },
    {
      "t": "2026-11-07T01:42:04+03:00",
      "k": "answered",
      "kind": "briefing"
    },
    {
      "t": "2026-11-07T01:45:25+03:00",
      "k": "tickets",
      "n": 6
    },
    {
      "t": "2026-11-07T01:45:30+03:00",
      "k": "gate",
      "g": "G3",
      "ok": true,
      "problems": 0
    },
    {
      "t": "2026-11-07T01:45:36+03:00",
      "k": "stage",
      "id": "build",
      "act": "start"
    },
    {
      "t": "2026-11-07T01:45:44+03:00",
      "k": "ticket",
      "ids": [
        "01"
      ],
      "act": "start"
    },
    {
      "t": "2026-11-07T01:45:59+03:00",
      "k": "ticket",
      "ids": [
        "01"
      ],
      "act": "done"
    },
    {
      "t": "2026-11-07T01:46:21+03:00",
      "k": "ticket",
      "ids": [
        "02"
      ],
      "act": "start"
    },
    {
      "t": "2026-11-07T01:58:18+03:00",
      "k": "ticket",
      "ids": [
        "02"
      ],
      "act": "done",
      "tests": {
        "passed": 231,
        "failed": 0
      }
    },
    {
      "t": "2026-11-07T01:58:27+03:00",
      "k": "ticket",
      "ids": [
        "03"
      ],
      "act": "start"
    },
    {
      "t": "2026-11-07T02:12:36+03:00",
      "k": "ticket",
      "ids": [
        "03"
      ],
      "act": "done"
    },
    {
      "t": "2026-11-07T02:12:49+03:00",
      "k": "ticket",
      "ids": [
        "04"
      ],
      "act": "start"
    },
    {
      "t": "2026-11-07T02:24:25+03:00",
      "k": "ticket",
      "ids": [
        "04"
      ],
      "act": "done"
    },
    {
      "t": "2026-11-07T02:24:29+03:00",
      "k": "ticket",
      "ids": [
        "05"
      ],
      "act": "start"
    },
    {
      "t": "2026-11-07T02:36:43+03:00",
      "k": "ticket",
      "ids": [
        "05"
      ],
      "act": "done",
      "tests": {
        "passed": 242,
        "failed": 0
      }
    },
    {
      "t": "2026-11-07T02:36:47+03:00",
      "k": "ticket",
      "ids": [
        "06"
      ],
      "act": "start"
    },
    {
      "t": "2026-11-07T02:37:33+03:00",
      "k": "ticket",
      "ids": [
        "06"
      ],
      "act": "done",
      "tests": {
        "passed": 242,
        "failed": 0
      }
    },
    {
      "t": "2026-11-07T02:37:37+03:00",
      "k": "stage",
      "id": "build",
      "act": "done"
    },
    {
      "t": "2026-11-07T02:37:44+03:00",
      "k": "finish",
      "result": "Биржевые графики на портале, импорт ProfitCrafter и целевой снайп перекрафта готовы"
    }
  ],
  "reqList": [
    {
      "id": "R01",
      "text": "«Работай строго по скиллу автопилот и /ponytail»",
      "status": "done"
    },
    {
      "id": "R02",
      "text": "«Не забудь поднять дашборт»",
      "status": "done"
    },
    {
      "id": "R03",
      "text": "«Код и тесты должны писать разные агенты»",
      "status": "done"
    },
    {
      "id": "R04",
      "text": "«Останавливаешь работу только после полного закрытия плана»",
      "status": "done"
    },
    {
      "id": "R05",
      "text": "«прочитай .agents/skills/ponytail/SKILL.md и строго следуй правилам из .agents/rules/ponytail.md»",
      "status": "done"
    },
    {
      "id": "R06",
      "text": "«отслеживай состояние через него и не пытайся переспаунить на другие порты»",
      "status": "done"
    },
    {
      "id": "R07",
      "text": "«хочу отслеживать графики цены на портале как на бирже»",
      "status": "done"
    },
    {
      "id": "R08",
      "text": "«В аддоне перфект ккрафтер форева который уже установлен в игре хочу чтобы ты от туда подтянул базу моих профессий и с процентов профита»",
      "status": "done"
    },
    {
      "id": "R09",
      "text": "«Хочу чтобы через снайп аддон наш можно было снайпить только конкретные предметы, с ценой которую я смогу указать и ниже»",
      "status": "done"
    },
    {
      "id": "R10",
      "text": "«А по умолчанию там будет стоять минимальная цены зароботка»",
      "status": "done"
    },
    {
      "id": "R11",
      "text": "«цель у меня такая, хочу зарабоать на перекрафте, ставлю несколько предметов на снайп, и он снайпит только их»",
      "status": "done"
    },
    {
      "id": "R12",
      "text": "«встроить наш аддон снайпа прям в аддон аукционатор как дополнение»",
      "status": "done"
    },
    {
      "id": "R13",
      "text": "«Код и тесты должны писать разные агенты»",
      "status": "done"
    },
    {
      "id": "R14",
      "text": "«Полная верификация тестами без бумажных проверок»",
      "status": "done"
    }
  ],
  "gates": {
    "G3": {
      "ok": true,
      "at": "2026-11-07T01:45:30+03:00",
      "problems": 0
    }
  },
  "result": "Биржевые графики на портале, импорт ProfitCrafter и целевой снайп перекрафта готовы"
}
