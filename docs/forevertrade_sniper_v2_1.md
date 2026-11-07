# Спецификация автономного торгового ядра и микро-аддона ForeverTrade_Sniper (v2.1)

Документ определяет архитектуру перехода Forever Trade на **100% автономный стек без внешнего TSM**:
- Отказ от установки и зависимости от внутриигрового аддона TSM.
- Собственный микро-аддон `ForeverTrade_Sniper` ($250\text{–}300$ строк Lua), использующий портированные и защищённые низкоуровневые алгоритмы сканирования TSM.
- Автоматическая генерация конфигурации цен и таргетов из Python-демона прямо в папку аддонов WoW.
- Портирование математических ядер и таблиц данных TSM (Hampel Filter, EWMA, Disenchanting, DAG-крафт) в `src/analytics.py`.

---

## 1. Концепция: Разделение мозга и исполнителя (Двухконтурная модель)

В условиях мега-сервера Forever с сотнями тысяч игроков время жизни ошибочного лота составляет **$2\text{–}8$ секунд**. Чистая оффлайн-модель через постоянный `/reload` не успевает за живым рынком, а тяжелый TSM перегружает клиент.

Решение строится на разделении **медленного аналитического контура** и **быстрого контура исполнения**:

1. **Аналитический мозг — Медленный контур (Python-демон Forever Trade):**
   - При каждом `/reload` или выходе в меню клиент WoW сбрасывает свежий дамп `Auctionator.lua` на диск (раз в $30\text{–}60$ минут после полного скана).
   - Python-демон отслеживает изменение файла, рассчитывает справедливые рыночные цены через робастный Hampel Filter и EWMA, оценивает коэффициент ликвидности $L$ и матожидание распыления $\mathbb{E}[\text{DE Value}]$.
   - Генерирует плоскую хеш-таблицу таргетов с максимальными ценами выкупа **за $1$ единицу предмета (unit price)** и автоматически записывает `config.lua` в папку `Interface/AddOns/ForeverTrade_Sniper/` (подхватывается клиентом при следующем `/reload`).
2. **Микро-исполнитель — Быстрый контур (`ForeverTrade_Sniper.lua`):**
   - Ультралегкий Lua-аддон ($< 50\text{ КБ}$ памяти, $0$ сторонних библиотек, без Ace3), работающий в реальном времени без `/reload`.
   - Непрерывный опрос последней страницы `FT_TargetPage` через `QueryAuctionItems` каждые $1.2\text{–}1.4$ секунды по событию получения ответа от сервера.
   - Моментальная сверка цены за единицу и суммарной выгоды со стака за $O(1)$ с приоритизацией самого выгодного лота на странице.
   - Автоматическая пауза сканера при захвате цели (защита от сдвига строк), звуковой алерт и привязка защищённого выкупа к колесу мыши ($1$ скролл $= 1$ аппаратное действие выкупа с верификацией лота).

---

## 2. Архитектура микро-аддона `ForeverTrade_Sniper`

### 2.1. Файловая структура аддона
```
addons/ForeverTrade_Sniper/
├── ForeverTrade_Sniper.toc
├── ForeverTrade_Sniper.lua
└── config.lua               <-- генерируется/обновляется Python-демоном
```

### 2.2. Портированные и защищённые механизмы из TSM Sniper

Из исходников TSM (`AuctionScan/Core/Sniper.lua` и `AuctionUI/Sniper.lua`) заимствуются и адаптируются $4$ боевых механизма с учётом защиты от спама и сетевых задержек:

#### 1. Индексация последней страницы и компенсация сдвига (Page Shift):
Сервер сортирует базу аукциона хронологически. Новые лоты всегда помещаются в конец. Переменная `FT_TargetPage` хранится на уровне модуля и пересчитывается строго в обработчике события `AUCTION_ITEM_LIST_UPDATE`. При первом открытии окна аукциона (`totalCount == 0`) запрашивается страница `0` для инициализации общего числа лотов, после чего сканер опрашивает хвост:
```lua
FT_TargetPage = 0
FT_CheckPrevPageNext = false

local function FT_UpdateTargetPage()
    local batchCount, totalCount = GetNumAuctionItems("list")
    if totalCount and totalCount > 0 then
        local lastPage = math.max(0, math.ceil(totalCount / 50) - 1)
        -- Если количество лотов на границе переключения страницы (1-5 лотов),
        -- чередуем проверку lastPage и lastPage - 1, чтобы не потерять лоты при сдвиге:
        if (totalCount % 50) > 0 and (totalCount % 50) <= 5 and lastPage > 0 and FT_CheckPrevPageNext then
            FT_TargetPage = lastPage - 1
            FT_CheckPrevPageNext = false
        else
            FT_TargetPage = lastPage
            FT_CheckPrevPageNext = true
        end
    else
        FT_TargetPage = 0
    end
end
```

#### 2. Событийный Token-Bucket рейт-лимит и пауза при захвате цели:
Сервер WoW блокирует запросы чаще $\sim 1.2$ секунды. Чтобы избежать наложения запросов при вечернем пинге, вводится флаг ожидания ответа `FT_AwaitingResults`, который сбрасывается только при приходе `AUCTION_ITEM_LIST_UPDATE` (или по таймауту $3.0\text{ с}$). Когда снайпер находит выгодный лот и взводит колесо мыши (`FT_IsArmed == true`), отправка новых поисковых запросов приостанавливается (с авто-сбросом через $4$ секунды), чтобы страница не обновилась прямо перед кликом игрока:
```lua
local frame = CreateFrame("Frame")
local timeSinceLastQuery = 0
local armedTimer = 0
FT_IsArmed = false
FT_AwaitingResults = false

frame:SetScript("OnUpdate", function(self, elapsed)
    if not AuctionFrame or not AuctionFrame:IsShown() then return end

    if FT_IsArmed then
        armedTimer = armedTimer + elapsed
        if armedTimer >= 4.0 then
            FT_DisarmSniperBuy() -- Таймаут ожидания: разблокируем сканер и колесо мыши
        end
        return
    end

    timeSinceLastQuery = timeSinceLastQuery + elapsed
    -- Защита от зависания флага при потере пакета (таймаут 3.0 сек)
    if FT_AwaitingResults and timeSinceLastQuery < 3.0 then return end

    if timeSinceLastQuery >= 1.2 and CanSendAuctionQuery() then
        timeSinceLastQuery = 0
        armedTimer = 0
        FT_AwaitingResults = true
        QueryAuctionItems("", nil, nil, FT_TargetPage, 0, 0, false, false, nil)
    end
end)
```

#### 3. Поштучная проверка цены, фильтр минимального профита со стака и LRU-кэш:
Поскольку `GetAuctionItemInfo("list", i)` возвращает `buyoutPrice` за **весь стак** (а при отсутствии выкупа возвращает `0`), аддон проверяет **два условия**:
1. Цена за $1$ штуку не превышает потолок `unitTarget`.
2. Суммарная выгода со всего стака `totalSaved = (unitTarget - unitPrice) * count` не меньше порога `min_profit_copper` (защита от «паралича сканера» на единичных кусках дешёвой ткани/руды с выгодой в $10\text{–}20$ медяков).

Если на странице из $50$ лотов найдено сразу несколько выгодных позиций, выбирается лот с **максимальным `totalSaved`**.

Для фильтрации уже просмотренных лотов ведётся кольцевой кэш из последних $200$ записей. Поля `owner` и `timeLeft` **исключены из ключа хеша**, так как имя владельца подгружается клиентом асинхронно и вызывает ложные повторные срабатывания:
```lua
local minProfit = (FT_SNIPER_CONFIG and FT_SNIPER_CONFIG.min_profit_copper) or 1000
local bestCandidate = nil

for i = 1, batchCount do
    local _, _, count, _, _, _, minBid, _, buyout = GetAuctionItemInfo("list", i) -- Примечание: адаптировать индекс buyout под 1.14 (10-й) или 3.3.5a (9-й)
    local link = GetAuctionItemLink("list", i)
    local itemId = link and tonumber(string.match(link, "item:(%d+)"))
    local unitTarget = itemId and FT_SNIPER_CONFIG and FT_SNIPER_CONFIG.targets[itemId]

    if unitTarget and buyout and buyout > 0 and count and count > 0 then
        local unitPrice = buyout / count
        local totalSaved = (unitTarget - unitPrice) * count
        if unitPrice <= unitTarget and totalSaved >= minProfit then
            local hash = string.format("%d_%d_%d_%d", itemId, count, minBid or 0, buyout)
            if not FT_SeenCache[hash] then
                if not bestCandidate or totalSaved > bestCandidate.totalSaved then
                    bestCandidate = { index = i, itemId = itemId, buyout = buyout, hash = hash, totalSaved = totalSaved }
                end
            end
        end
    end
end
```
При успешном выкупе лота его `hash` удаляется из кэша, чтобы аддон мог сразу же предложить к выкупу следующий идентичный стак по той же цене.

#### 4. Безопасный выкуп через колесо мыши (Hardware Event + верификация слота):
Вызов `PlaceAuctionBid` защищён движком игры от вызова вне аппаратного события. Создаётся невидимая кнопка `SecureActionButtonTemplate` с проверкой совпадения `itemId` и `buyout` перед выкупом и гарантированным сбросом перехвата колеса мыши:
```lua
local buyBtn = CreateFrame("Button", "FTSniperBuyBtn", UIParent, "SecureActionButtonTemplate")
buyBtn:SetAttribute("type", "macro")

function FT_DisarmSniperBuy()
    FT_IsArmed = false
    if not InCombatLockdown() then
        ClearOverrideBindings(buyBtn)
    end
end

function FT_ExecuteVerifiedBuy(index, expectedItemId, expectedBuyout, hash)
    local _, _, count, _, _, _, _, _, _, buyoutPrice = GetAuctionItemInfo("list", index)
    local link = GetAuctionItemLink("list", index)
    local actualItemId = link and tonumber(string.match(link, "item:(%d+)"))

    if actualItemId == expectedItemId and buyoutPrice == expectedBuyout and buyoutPrice > 0 then
        PlaceAuctionBid("list", index, expectedBuyout)
        if FT_SeenCache and hash then
            FT_SeenCache[hash] = nil -- Разрешаем покупку следующего такого же стака
        end
    end
    FT_DisarmSniperBuy()
end

function FT_ArmSniperBuy(index, itemId, buyout, hash)
    FT_IsArmed = true
    PlaySoundFile("Sound\\Interface\\AuctionWindowOpen.ogg")
    buyBtn:SetAttribute("macrotext", string.format("/run FT_ExecuteVerifiedBuy(%d, %d, %d, %q)", index, itemId, buyout, hash or ""))
    SetOverrideBindingClick(buyBtn, true, "MOUSEWHEELUP", "FTSniperBuyBtn")
    SetOverrideBindingClick(buyBtn, true, "MOUSEWHEELDOWN", "FTSniperBuyBtn")
end
```
При событии закрытия окна аукциона (`AUCTION_HOUSE_CLOSED`) автоматически вызывается `FT_DisarmSniperBuy()`, чтобы колесо мыши всегда возвращалось в штатный режим.

---

## 3. Мост «Python-демон $\leftrightarrow$ Микро-аддон»

### 3.1. Генерация `config.lua`
В `src/analytics.py` реализуется функция `export_sniper_addon_config()`:
- Анализирует текущую базу цен и объединяет **три потока арбитража** (прямой перепродажный флип ликвидных товаров, распыление экипировки в ликвидные реагенты и прямую сдачу NPC-торговцу).
- Вычисляет потолок выкупа **за $1$ единицу предмета** по единой формуле:
  $$\text{MaxBuyout}(i) = \max\Big(\mathbb{I}(L_i \ge 0.50) \cdot \lfloor 0.65 \times \text{dbmarket}(i) \rfloor, \quad \lfloor 0.80 \times \mathbb{E}[\text{DE Value}(i)] \rfloor, \quad P_{\text{vendor\_sell}}(i) - \Delta_{\text{vendor}}(i)\Big)$$
  где:
  - $\mathbb{I}(L_i \ge 0.50)$ — индикатор ликвидности (отсекает неликвид от прямой перепродажи, но позволяет выкупать дешёвую экипировку с $L_i < 0.50$ строго под распыл по ветке $\mathbb{E}[\text{DE Value}(i)]$).
  - $\Delta_{\text{vendor}}(i) = \max\big(50c, \lfloor 1500c / \text{max\_stack}(i) \rfloor\big)$ — защита от засорения инвентаря и почты одиночными предметами с мизерной маржой (требует минимум $15\text{ серебра}$ потенциальной выгоды на $1$ полный слот сумки).
- Отфильтровывает позиции с $\text{MaxBuyout}(i) \le 0$.
- Записывает `config.lua` по пути `$WOW_PATH/Interface/AddOns/ForeverTrade_Sniper/config.lua`:
  ```lua
  -- Generated by Forever Trade Engine. DO NOT EDIT MANUALLY.
  FT_SNIPER_CONFIG = {
      version = 2,
      generated_at = 1730580000,
      min_profit_copper = 1000, -- Минимум 10 серебра чистой выгоды со всего стака
      targets = {
          [2589]   = 120,  -- Linen Cloth (за 1 шт.): сработает только на стаках с суммарной выгодой >= 10s
          [2770]   = 350,  -- Copper Ore (за 1 шт.): выкуп <= 3s 50c
          [118]    = 4500, -- Minor Healing Potion (за 1 шт.)
          [211501] = 8500, -- Waylaid Supplies target (за 1 шт.)
      }
  }
  ```
- При входе в мир или `/reload` аддон выводит в системный чат время генерации конфига (`generated_at`) и общее число загруженных ID, чтобы игрок видел актуальность базы таргетов.

---

## 4. Портирование алгоритмов и данных TSM в Python-бэкенд

Всё математическое превосходство TSM переносится прямо в `src/analytics.py` и `data/`:

### 4.1. Робастный DBMarket и Hampel Filter (отсечение выбросов)
- **Замена классической $\sigma$ на робастную $\sigma_{\text{MAD}}$:**
  $$\text{MAD} = \text{median}(|P_i - \tilde{P}|), \quad \sigma_{\text{MAD}} = 1.4826 \times \text{MAD}$$
- Точки с $|P_i - \tilde{P}| > 3 \times \max(\sigma_{\text{MAD}}, 0.10 \tilde{P})$ исключаются из ядра (тролль-лоты за $999\text{g}$ больше не ломают среднее, а нижний порог $0.10 \tilde{P}$ защищает от нулевого $\text{MAD}$ на однородных стаках).
- Экспоненциальное затухание (EWMA) с полураспадом $3.5$ дня: $w_i = \exp(-\lambda \Delta t)$.

### 4.2. Liquidity Score ($L$) — защита от неликвидных ловушек
- Расчет коэффициента $L \in [0.01, 1.0]$:
  $$L = \alpha L_{\text{obs}} + (1 - \alpha) L_{\text{intrinsic}}$$
- Расходники/сырье/ящики получают $L \ge 0.90$. Мусорная синька и редкие выкройки получают $L \le 0.25$.
- Предметы с $L < 0.20$ помечаются как `ILLIQUID_TRAP` для прямой перепродажи (оцениваются только по стоимости распыла или вендора).
- Учет ожидаемых потерь залога при повторных выставлениях ($\frac{1 - L}{L}$) и стоимости заморозки капитала при оценке чистой прибыли:
  $$\Pi_{\text{adj}} = \mu \times 0.95 - P_{\text{curr}} - \text{deposit} \times \frac{1 - L}{L} - (1 - L) \cdot 0.10 P_{\text{curr}}$$

### 4.3. Disenchant Shuffle (арбитраж распыления)
- Из `TradeSkillMaster/Data/Disenchanting.lua` извлекаются матрицы матожидания $\mathbb{E}[\text{mats}]$ и сохраняются в `data/disenchant_tables_era.json`.
- Метод `get_disenchant_arbitrage()` находит на аукционе экипировку, выставленную дешевле стоимости выпадающих пыли, эссенций и осколков минус $5\%$ комиссии:
  $$\text{Profit} = \mathbb{E}[\text{DE Value}] \times 0.95 - P_{\text{buyout}} \ge \text{min\_profit}$$
- Поскольку пыль и эссенции обладают высокой ликвидностью ($L \ge 0.90$), порог $\lfloor 0.80 \times \mathbb{E}[\text{DE Value}(i)] \rfloor$ напрямую экспортируется в `config.lua` для мгновенного перехвата дешёвой экипировки снайпером.
- Новый таб в Goblin Terminal: `✨ Дизэнчант`.

### 4.4. Рекурсивный DAG-крафт (Make-vs-Buy)
- Расчет себестоимости полуфабрикатов по минимальной ветке:
  $$\text{MatPrice}(r) = \min\big(P_{\text{vendor\_buy}}(r), \quad P_{\text{ah}}(r), \quad C_{\text{craft}}(r)\big)$$
- Если на аукционе нет рулонов ткани или медных трубок, крафт не бракуется, а считается из базовой ткани и руды.

---

## 5. Дорожная карта реализации

```
Этап 1: Портирование данных и Liquidity Shield
├── data/disenchant_tables_era.json (извлечение из TSM)
├── src/analytics.py: compute_liquidity_score() и защита Z-score
└── tests/test_analytics.py: тесты на отсечение неликвида

Этап 2: Микро-аддон ForeverTrade_Sniper
├── addons/ForeverTrade_Sniper/ (TOC + защищённое Lua-ядро сканера FT_TargetPage)
├── src/analytics.py: export_sniper_addon_config() (генерация config.lua с учётом DE и стак-профита)
└── Интеграция пути в run_watcher.py (CLI флаг --wow-addon-path)

Этап 3: Disenchant Shuffle в ядре и UI
├── src/analytics.py: get_disenchant_arbitrage()
├── src/dashboard.py: вкладка [✨ Дизэнчант] и раскладка дропа в инспекторе
└── tests/test_disenchant.py

Этап 4: Робастный DBMarket (Hampel Filter + EWMA)
├── src/analytics.py: робастная фильтрация выбросов
└── src/database.py: таблица кэша item_market_metrics

Этап 5: Рекурсивный DAG Crafting Engine
├── src/analytics.py: resolve_effective_material_cost()
└── src/dashboard.py: дерево промежуточных крафтов в инспекторе
```