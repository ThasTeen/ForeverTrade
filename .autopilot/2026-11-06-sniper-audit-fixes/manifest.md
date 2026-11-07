# Манифест требований

Источник: `2026-11-06-brief.md`. Строку из этого списка может снять **только пользователь**.

| ID | Из брифа (дословно) | Статус | Основание | Где |
|----|---------------------|--------|-----------|-----|
| R01 | «Работай строго по скиллу автопилот и /ponytail» | done | Доктрина Ponytail, минимальный рабочий дифф | spec §1 → T01–T05 |
| R02 | «Не забудь поднять дашборт» | done | Порт 10459 поднят и отвечает 200 OK | spec §1 → T01 |
| R03 | «Код и тесты должны писать разные агенты» | done | Агент-Кодер изменил только src/addons/run_watcher, Агент-Тестер — tests | spec §1, §4 → T02, T03, T04 |
| R04 | «Останавливаешь работу только после полного закрытия плана» | done | Все тикеты T01–T05 закрыты | spec §1, §5 → T01, T05 |
| R05 | «прочитай .agents/skills/ponytail/SKILL.md и строго следуй правилам из .agents/rules/ponytail.md» | done | Ponytail full, честные тесты без бумаги | spec §1 → T01, T04 |
| R06 | «отслеживай состояние через него и не пытайся переспаунить на другие порты» | done | Порт 10459 | spec §1 → T01, T05 |
| R07 | *(аудит)* Защита от сдвига стака: верификация count == expectedCount и unitPrice <= unitTarget в выкупе | done | Добавлена строгая 5-компонентная проверка в FT_ExecuteVerifiedBuy | spec §2 → T02 |
| R08 | *(аудит)* Устранение двойной фильтрации прибыли: триггер по unitPrice <= unitTarget и дефолт min_profit_copper = 0 | done | unitPrice <= unitTarget and (minProfit <= 0 or totalSaved >= minProfit) | spec §2 → T02, T03 |
| R09 | *(аудит)* Очистка/защита ветки выкупа: устранение коллизии auctionID/itemId и стабильный Legacy AH выкуп | done | PlaceAuctionBid по индексу, удалена коллизия auctionID/itemId | spec §2 → T02 |
| R10 | *(аудит)* Обработка PLAYER_REGEN_ENABLED/DISABLED: очистка биндов колеса мыши после выхода из боя | done | Слушатели комбата + pendingClearBindings флаг | spec §2 → T02 |
| R11 | *(аудит)* Безопасный выкуп без макро-инъекций: перевод buyBtn на OnClick и _target, инкапсуляция в local | done | buyBtn._target + OnClick, отказ от macrotext и /run | spec §2 → T02 |
| R12 | *(аудит)* UI Fix: предотвращение самопроизвольного переключения при Shift+Drag кнопки | done | self.isDragging флаг в OnDragStart / OnClick | spec §2 → T02 |
| R13 | *(аудит)* Атомарная запись config.lua через временный файл и os.replace | done | Запись в .tmp + os.replace | spec §3 → T03 |
| R14 | *(аудит)* Устранение двойного тяжелого перерасчёта базы при --wow-addon-path через shutil.copyfile | done | shutil.copyfile готового конфига | spec §3 → T03 |
| R15 | *(аудит)* Оптимизация GC в OnUpdate: прямое присвоение переменных вместо аллокаций { GetAuctionItemInfo(...) } | done | Прямая распаковка возвращаемых значений | spec §2 → T02 |
| R16 | *(аудит)* Поддержка справки /fts help и /fts ? вместо ложного переключения FT_Toggle() | done | Ветка help/? со списком команд | spec §2 → T02 |
| R17 | *(аудит)* Обновление тестов в test_sniper_addon.py и test_analytics.py, прогон полного набора pytest | done | 229 тестов успешно пройдены | spec §4 → T04 |
