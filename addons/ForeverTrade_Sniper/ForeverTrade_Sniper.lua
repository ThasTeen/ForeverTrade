-- ForeverTrade_Sniper v2.1
-- Standalone AH sniper with hardware-protected buying and ring-buffer deduplication
-- Supports both Modern AH (C_AuctionHouse / Retail 1.60.x) and Legacy AH (Classic Era)

local ADDON_NAME = "ForeverTrade_Sniper"
local VERSION = "2.1"

-- Global state variables
FT_IsEnabled = false
FT_TargetPage = 0
FT_CheckPrevPageNext = false
FT_IsArmed = false
FT_AwaitingResults = false
FT_SeenCache = {}
FT_SeenOrder = {}
local MAX_SEEN_CACHE = 200

local timeSinceLastQuery = 0
local armedTimer = 0
FT_QueriesCount = 0
FT_LotsScannedCount = 0
FT_CustomMinProfit = nil

-- Target customizations (SavedVariables: FT_CustomTargets)
FT_CustomTargets = FT_CustomTargets or {}

function FT_HasCustomSelection()
    if not FT_CustomTargets then return false end
    for _, cfg in pairs(FT_CustomTargets) do
        if type(cfg) == "table" and (cfg.enabled ~= nil or (cfg.customPrice and cfg.customPrice > 0)) then
            return true
        end
    end
    return false
end

function FT_IsItemActive(itemId)
    if not itemId then return false end
    local numId = tonumber(itemId)
    local cfg = FT_CustomTargets and (FT_CustomTargets[itemId] or (numId and FT_CustomTargets[numId]))
    if cfg and cfg.enabled ~= nil then
        return cfg.enabled and true or false
    end
    if FT_HasCustomSelection() then
        return false
    end
    return true
end

function FT_GetEffectiveTargetPrice(itemId, defaultTarget)
    if type(defaultTarget) == "table" then
        defaultTarget = defaultTarget.target or 0
    end
    if not itemId then return defaultTarget end
    local numId = tonumber(itemId)
    local cfg = FT_CustomTargets and (FT_CustomTargets[itemId] or (numId and FT_CustomTargets[numId]))
    if cfg and cfg.customPrice and type(cfg.customPrice) == "number" and cfg.customPrice > 0 then
        return cfg.customPrice
    end
    return defaultTarget
end

-- Helper: Format copper into 'Xз Yс Zм'
local function FT_FormatMoneyText(copper)
    copper = tonumber(copper) or 0
    if copper <= 0 then return "0м" end
    local g = math.floor(copper / 10000)
    local s = math.floor((copper % 10000) / 100)
    local c = math.floor(copper % 100)
    local parts = {}
    if g > 0 then table.insert(parts, g .. "з") end
    if s > 0 then table.insert(parts, s .. "с") end
    if c > 0 or #parts == 0 then table.insert(parts, c .. "м") end
    return table.concat(parts, " ")
end

-- Helper: Parse input string (e.g. '500', '1g 20s', '1з 20с 50м') into copper
local function FT_ParseMoneyInput(text)
    if not text then return 0 end
    text = tostring(text):lower():gsub("%s+", ""):gsub(",", ".")
    if text == "" then return 0 end

    if text:find("[gзsсмc]") then
        local gold = tonumber(text:match("([%d%.]+)[gз]")) or 0
        local silver = tonumber(text:match("([%d%.]+)[sс]")) or 0
        local copper = tonumber(text:match("([%d%.]+)[cм]")) or 0
        return math.floor((gold * 10000) + (silver * 100) + copper + 0.5)
    end

    local num = tonumber(text)
    return num and math.max(0, math.floor(num)) or 0
end

-- Helper: Title bar character & profession info
local function FT_GetTitleBarText()
    local charName = (FT_SNIPER_CONFIG and FT_SNIPER_CONFIG.character and FT_SNIPER_CONFIG.character ~= "") and FT_SNIPER_CONFIG.character or (UnitName and UnitName("player")) or "Игрок"
    local profStrings = {}
    if FT_SNIPER_CONFIG and FT_SNIPER_CONFIG.professions then
        for _, p in pairs(FT_SNIPER_CONFIG.professions) do
            if type(p) == "table" and p.name and p.name ~= "" then
                local rank = p.rank or 0
                local maxRank = p.max or 0
                if maxRank > 0 then
                    table.insert(profStrings, string.format("%s %d/%d", p.name, rank, maxRank))
                else
                    table.insert(profStrings, p.name)
                end
            end
        end
    end
    if #profStrings > 0 then
        return string.format("%s | %s", charName, table.concat(profStrings, ", "))
    else
        return string.format("%s | Снайпер FT", charName)
    end
end

-- Engine detection
local function FT_IsModernAH()
    return (C_AuctionHouse and (C_AuctionHouse.IsThrottledMessageSystemReady ~= nil or C_AuctionHouse.SendBrowseQuery ~= nil)) and true or false
end

-- Helper: System Chat output
local function FT_Print(msg)
    if DEFAULT_CHAT_FRAME then
        DEFAULT_CHAT_FRAME:AddMessage("|cff00ff00[" .. ADDON_NAME .. "]|r " .. msg)
    elseif print then
        print("[" .. ADDON_NAME .. "] " .. msg)
    end
end

-- Helper: Add hash to ring buffer cache (max 200 entries)
local function FT_AddSeen(hash)
    if not FT_SeenCache[hash] then
        FT_SeenCache[hash] = true
        table.insert(FT_SeenOrder, hash)
        if #FT_SeenOrder > MAX_SEEN_CACHE then
            local oldest = table.remove(FT_SeenOrder, 1)
            if oldest then
                FT_SeenCache[oldest] = nil
            end
        end
    end
end

-- Mechanism 1: Page Indexing & Page Shift Compensation (Legacy AH)
function FT_UpdateTargetPage()
    if type(GetNumAuctionItems) ~= "function" then
        FT_TargetPage = 0
        return
    end
    local batchCount, totalCount = GetNumAuctionItems("list")
    if totalCount and totalCount > 0 then
        local lastPage = math.max(0, math.ceil(totalCount / 50) - 1)
        -- Compensate for boundary shifts (1-5 items on page boundary)
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

-- Mechanism 4: Secure Action Button & Hardware Binding
local buyBtn = CreateFrame("Button", "FTSniperBuyBtn", UIParent, "SecureActionButtonTemplate")
buyBtn._target = nil
buyBtn:SetScript("OnClick", function(self)
    local target = self._target
    if target then
        self._target = nil
        FT_ExecuteVerifiedBuy(target.index, target.itemId, target.buyout, target.count, target.unitTarget, target.hash)
    end
end)

local pendingClearBindings = false
local FT_PendingCommodity = nil

-- Helper: Loud alert sound with multi-engine fallback on Master audio channel
local function FT_PlayAlertSound()
    local played = false
    if type(PlaySound) == "function" then
        -- 8959 = Raid Warning horn / gong (loud unmistakable snipe alert)
        if SOUNDKIT and SOUNDKIT.RAID_WARNING then
            played = pcall(PlaySound, SOUNDKIT.RAID_WARNING, "Master")
        end
        if not played then
            played = pcall(PlaySound, 8959, "Master")
        end
        if not played and SOUNDKIT and SOUNDKIT.AUCTION_WINDOW_OPEN then
            played = pcall(PlaySound, SOUNDKIT.AUCTION_WINDOW_OPEN, "Master")
        end
        if not played then
            played = pcall(PlaySound, 5274, "Master")
        end
        if not played then
            played = pcall(PlaySound, "RaidWarning", "Master")
        end
        if not played then
            played = pcall(PlaySound, "AuctionWindowOpen", "Master")
        end
    end
    if not played and type(PlaySoundFile) == "function" then
        played = pcall(PlaySoundFile, "Sound\\Interface\\RaidWarning.ogg", "Master")
        if not played then
            played = pcall(PlaySoundFile, "Sound\\Interface\\AuctionWindowOpen.wav", "Master")
        end
        if not played then
            pcall(PlaySoundFile, "Sound\\Interface\\AuctionWindowOpen.ogg", "Master")
        end
    end
end

function FT_DisarmSniperBuy()
    FT_IsArmed = false
    armedTimer = 0
    if buyBtn then
        buyBtn._target = nil
    end
    if InCombatLockdown and InCombatLockdown() then
        pendingClearBindings = true
    else
        ClearOverrideBindings(buyBtn)
        pendingClearBindings = false
    end
end

function FT_GetItemDisplayName(itemId, fallbackLink)
    if fallbackLink and fallbackLink:find("|h%[.+%]|h") then
        return fallbackLink
    end
    if itemId and type(GetItemInfo) == "function" then
        local itemName, itemLink, itemQuality = GetItemInfo(itemId)
        if itemLink and itemLink ~= "" then
            return itemLink
        end
        if itemName and itemName ~= "" then
            local color = "ffffffff"
            if itemQuality and ITEM_QUALITY_COLORS and ITEM_QUALITY_COLORS[itemQuality] then
                color = ITEM_QUALITY_COLORS[itemQuality].hex or "ffffffff"
            end
            return string.format("|c%s|Hitem:%d:0:0:0:0:0:0:0:0:0:0:0:0|h[%s]|h|r", color, itemId, itemName)
        end
    end
    if itemId and FT_SNIPER_CONFIG and FT_SNIPER_CONFIG.targets and type(FT_SNIPER_CONFIG.targets[itemId]) == "table" and FT_SNIPER_CONFIG.targets[itemId].name then
        local cfgName = FT_SNIPER_CONFIG.targets[itemId].name
        return string.format("|cffffffff|Hitem:%d:0:0:0:0:0:0:0:0:0:0:0:0|h[%s]|h|r", itemId, cfgName)
    end
    if itemId and FT_SNIPER_CONFIG and FT_SNIPER_CONFIG.names and FT_SNIPER_CONFIG.names[itemId] then
        local cfgName = FT_SNIPER_CONFIG.names[itemId]
        return string.format("|cffffffff|Hitem:%d:0:0:0:0:0:0:0:0:0:0:0:0|h[%s]|h|r", itemId, cfgName)
    end
    return string.format("|cffffffff[Предмет #%s]|r", tostring(itemId or "?"))
end

function FT_ExecuteVerifiedBuy(index, expectedItemId, expectedBuyout, expectedCount, unitTarget, hash)
    -- Backward compatibility if called with 4 arguments: (index, expectedItemId, expectedBuyout, hash)
    if type(expectedCount) == "string" and unitTarget == nil and hash == nil then
        hash = expectedCount
        expectedCount = 1
        unitTarget = expectedBuyout
    end
    expectedCount = expectedCount or 1
    unitTarget = unitTarget or expectedBuyout

    -- Test mode simulation check
    if hash == "test_hash" or hash == "test_simulation_hash" then
        FT_Print("|cff00ff00[TEST УСПЕШЕН]|r Hardware-клик перехвачен! Бинды колеса мыши и выкуп работают штатно.")
        FT_DisarmSniperBuy()
        return
    end

    if FT_IsModernAH() then
        -- Modern AH Purchase: auctionID or itemID execution
        local auctionID = tonumber(index)
        if auctionID and auctionID > 0 and auctionID ~= expectedItemId and C_AuctionHouse and C_AuctionHouse.PlaceBid then
            local ok, err = pcall(C_AuctionHouse.PlaceBid, auctionID, expectedBuyout)
            if ok then
                local itemDisplay = FT_GetItemDisplayName(expectedItemId)
                FT_Print(string.format("Выкуплен лот %s (аукцион %d) за %d мед.!", itemDisplay, auctionID, expectedBuyout))
                if hash then
                    FT_SeenCache[hash] = nil
                end
            end
        elseif expectedItemId and C_AuctionHouse and C_AuctionHouse.StartCommoditiesPurchase then
            FT_PendingCommodity = {
                itemId = expectedItemId,
                count = expectedCount,
                expectedBuyout = expectedBuyout,
                unitTarget = unitTarget,
                hash = hash,
            }
            local ok, err = pcall(C_AuctionHouse.StartCommoditiesPurchase, expectedItemId, expectedCount)
            if ok then
                local itemDisplay = FT_GetItemDisplayName(expectedItemId)
                FT_Print(string.format("Запрос котировки на биржевой товар %s (x%d)...", itemDisplay, expectedCount))
            else
                FT_PendingCommodity = nil
                FT_Print("|cffff4444[ОШИБКА]|r Не удалось запросить котировку товара.")
            end
        end
    else
        -- Legacy AH Purchase: index verification
        if type(GetAuctionItemInfo) == "function" and type(PlaceAuctionBid) == "function" then
            local name, texture, count, quality, canUse, level, levelColHeader, minBid, minIncrement, buyoutPrice, bidAmount, highBidder, bidderFullName, owner, ownerFullName, saleStatus, rawItemId = GetAuctionItemInfo("list", index)
            buyoutPrice = buyoutPrice or 0
            count = count or 1
            local link = (type(GetAuctionItemLink) == "function") and GetAuctionItemLink("list", index)
            local actualItemId = (rawItemId and rawItemId > 0 and rawItemId) or (link and tonumber(string.match(link, "item:(%d+)")))

            if actualItemId == expectedItemId and buyoutPrice == expectedBuyout and count == expectedCount and (buyoutPrice / count) <= unitTarget and buyoutPrice > 0 then
                PlaceAuctionBid("list", index, expectedBuyout)
                local itemDisplay = FT_GetItemDisplayName(expectedItemId, link)
                FT_Print(string.format("Выкуплен лот %s (x%d) за %d мед.!", itemDisplay, count, expectedBuyout))
                if hash then
                    FT_SeenCache[hash] = nil -- Allow buying next identical stack if available
                end
            else
                local actualDisplay = FT_GetItemDisplayName(actualItemId, link)
                local expectedDisplay = FT_GetItemDisplayName(expectedItemId)
                FT_Print(string.format("|cffff4444[ВЕРИФИКАЦИЯ НЕ ПРОЙДЕНА]|r Лот изменён или сдвинут (Товар: %s vs %s, Buyout: %s vs %s, Count: %s vs %s). Выкуп отменён.",
                    tostring(actualDisplay), tostring(expectedDisplay), tostring(buyoutPrice), tostring(expectedBuyout), tostring(count), tostring(expectedCount)))
                FT_DisarmSniperBuy()
                return
            end
        end
    end
    FT_DisarmSniperBuy()
end

function FT_ArmSniperBuy(index, itemId, buyout, count, unitTarget, hash)
    -- Backward compatibility if called with 4 arguments: (index, itemId, buyout, hash)
    if type(count) == "string" and unitTarget == nil and hash == nil then
        hash = count
        count = 1
        unitTarget = buyout
    end
    count = count or 1
    unitTarget = unitTarget or buyout

    FT_IsArmed = true
    armedTimer = 0
    FT_PlayAlertSound()
    if buyBtn then
        buyBtn._target = {
            index = index,
            itemId = itemId,
            buyout = buyout,
            count = count,
            unitTarget = unitTarget,
            hash = hash,
        }
    end
    if not InCombatLockdown() then
        SetOverrideBindingClick(buyBtn, true, "MOUSEWHEELUP", "FTSniperBuyBtn")
        SetOverrideBindingClick(buyBtn, true, "MOUSEWHEELDOWN", "FTSniperBuyBtn")
    end
end

local function FT_GetEffectiveMinProfit()
    if FT_CustomMinProfit ~= nil then
        return FT_CustomMinProfit
    end
    return (FT_SNIPER_CONFIG and FT_SNIPER_CONFIG.min_profit_copper) or 1000
end

-- Mechanism 3: Unit-price check, profit filter, candidate selection
local function FT_ScanCurrentPage(addedResults)
    local minProfit = FT_GetEffectiveMinProfit()
    local targets = (FT_SNIPER_CONFIG and FT_SNIPER_CONFIG.targets) or {}
    local bestCandidate = nil

    if FT_IsModernAH() and C_AuctionHouse and C_AuctionHouse.GetBrowseResults then
        local results = addedResults
        if type(results) ~= "table" or #results == 0 then
            results = C_AuctionHouse.GetBrowseResults()
        end
        if results and #results > 0 then
            FT_LotsScannedCount = FT_LotsScannedCount + #results

            for _, res in ipairs(results) do
                local itemId = res.itemKey and res.itemKey.itemID
                if itemId and FT_IsItemActive(itemId) then
                    local count = res.totalQuantity or 1
                    local minPrice = res.minPrice or 0
                    local rawTarget = targets[itemId]
                    local defaultTarget = type(rawTarget) == "table" and rawTarget.target or rawTarget
                    local unitTarget = defaultTarget and FT_GetEffectiveTargetPrice(itemId, defaultTarget)

                    if unitTarget and minPrice > 0 and not res.containsOwnerItem then
                        local unitPrice = minPrice
                        local totalSaved = (unitTarget - unitPrice) * count
                        if unitPrice <= unitTarget and (minProfit <= 0 or totalSaved >= minProfit) then
                        local hash = string.format("m_%d_%d_%d", itemId, count, minPrice)
                        if not FT_SeenCache[hash] then
                            if not bestCandidate or totalSaved > bestCandidate.totalSaved then
                                local itemLink = nil
                                if type(GetItemInfo) == "function" then
                                    local _, l = GetItemInfo(itemId)
                                    itemLink = l
                                    if not itemLink and C_Item and C_Item.RequestLoadItemDataByID then
                                        C_Item.RequestLoadItemDataByID(itemId)
                                    end
                                end
                                bestCandidate = {
                                    index = res.auctionID or 0,
                                    itemId = itemId,
                                    buyout = minPrice,
                                    hash = hash,
                                    totalSaved = totalSaved,
                                    count = count,
                                    unitPrice = unitPrice,
                                    unitTarget = unitTarget,
                                    link = itemLink,
                                }
                            end
                        end
                    end
                end
                end
            end
        end

        -- Request more pages if full results not reached
        if C_AuctionHouse.HasFullBrowseResults and not C_AuctionHouse.HasFullBrowseResults() then
            if C_AuctionHouse.RequestMoreBrowseResults then
                pcall(C_AuctionHouse.RequestMoreBrowseResults)
            end
        end

    elseif type(GetNumAuctionItems) == "function" and type(GetAuctionItemInfo) == "function" then
        local batchCount, totalCount = GetNumAuctionItems("list")
        if not batchCount or batchCount <= 0 then return end
        FT_LotsScannedCount = FT_LotsScannedCount + batchCount

        for i = 1, batchCount do
            local name, texture, count, quality, canUse, level, levelColHeader, minBid, minIncrement, buyout, bidAmount, highBidder, bidderFullName, owner, ownerFullName, saleStatus, rawItemId = GetAuctionItemInfo("list", i)
            count = count or 1
            minBid = minBid or 0
            buyout = buyout or 0
            local link = (type(GetAuctionItemLink) == "function") and GetAuctionItemLink("list", i)
            local itemId = (rawItemId and rawItemId > 0 and rawItemId) or (link and tonumber(string.match(link, "item:(%d+)")))

            if itemId and FT_IsItemActive(itemId) then
                local rawTarget = targets[itemId]
                local defaultTarget = type(rawTarget) == "table" and rawTarget.target or rawTarget
                local unitTarget = defaultTarget and FT_GetEffectiveTargetPrice(itemId, defaultTarget)

                if unitTarget and buyout and buyout > 0 and count > 0 then
                    local unitPrice = buyout / count
                    local totalSaved = (unitTarget - unitPrice) * count
                    if unitPrice <= unitTarget and (minProfit <= 0 or totalSaved >= minProfit) then
                    local hash = string.format("%d_%d_%d_%d", itemId, count, minBid, buyout)
                    if not FT_SeenCache[hash] then
                        if not bestCandidate or totalSaved > bestCandidate.totalSaved then
                            bestCandidate = {
                                index = i,
                                itemId = itemId,
                                buyout = buyout,
                                hash = hash,
                                totalSaved = totalSaved,
                                count = count,
                                unitPrice = unitPrice,
                                unitTarget = unitTarget,
                                link = link,
                            }
                        end
                    end
                end
            end
            end
        end
    end

    if bestCandidate then
        FT_ArmSniperBuy(bestCandidate.index, bestCandidate.itemId, bestCandidate.buyout, bestCandidate.count, bestCandidate.unitTarget, bestCandidate.hash)
        FT_AddSeen(bestCandidate.hash)
        local itemDisplay = FT_GetItemDisplayName(bestCandidate.itemId, bestCandidate.link)
        local targetEntry = targets[bestCandidate.itemId]
        local craftNote = (type(targetEntry) == "table" and targetEntry.craft_target and targetEntry.recipe_name) and string.format(" [Перекрафт: %s]", targetEntry.recipe_name) or ""
        FT_Print(string.format(
            "|cffffd100[SNIPE ALERT]|r %s%s x%d: Buyout=%dc (Unit: %dc <= Target: %dc). Saved=%dc! |cffff0000[SCROLL MOUSE WHEEL TO BUY]|r",
            itemDisplay, craftNote, bestCandidate.count, bestCandidate.buyout, math.floor(bestCandidate.unitPrice),
            bestCandidate.unitTarget, math.floor(bestCandidate.totalSaved)
        ))
    end
end

-- Mechanism 2: Token-bucket OnUpdate rate limiter, UI toggle and event listener
local toggleBtn = nil

local function FT_GetAuctionFrame()
    return AuctionHouseFrame or AuctionFrame
end

local function FT_PrintStatus()
    local targetCount = 0
    if FT_SNIPER_CONFIG and FT_SNIPER_CONFIG.targets then
        for _ in pairs(FT_SNIPER_CONFIG.targets) do targetCount = targetCount + 1 end
    end
    local customCount = 0
    if FT_CustomTargets then
        for _, cfg in pairs(FT_CustomTargets) do
            if type(cfg) == "table" and (cfg.enabled ~= nil or (cfg.customPrice and cfg.customPrice > 0)) then
                customCount = customCount + 1
            end
        end
    end
    local minProf = FT_GetEffectiveMinProfit()
    local engineName = FT_IsModernAH() and "Modern AH (C_AuctionHouse)" or "Legacy AH"
    local stateStr = FT_IsEnabled and "|cff00ff00АКТИВЕН|r" or "|cffff4444ВЫКЛЮЧЕН|r"
    FT_Print(string.format(
        "Статус: %s | Движок: |cffffd100%s|r | Запросов: %d | Проверено лотов: %d | Целей в базе: %d (настроено: %d) | Мин. профит: %d мед. (%.2fs)",
        stateStr, engineName, FT_QueriesCount, FT_LotsScannedCount, targetCount, customCount, minProf, minProf / 100
    ))
end

-- Mechanism 5: In-game Target Configuration UI Panel (FTSniperMainFrame)
local FTSniperMainFrame = nil
local FTSniperAuctionTab = nil
local FTSniperAuctionTabIndex = nil
local FT_CachedItemList = {}
local FT_FilteredItemList = {}
local FT_SearchQuery = ""
local NUM_ROWS = 12
local ROW_HEIGHT = 24

local function FT_FilterItemList()
    FT_FilteredItemList = {}
    local query = FT_SearchQuery and FT_SearchQuery:lower():gsub("^%s*(.-)%s*$", "%1") or ""

    for _, it in ipairs(FT_CachedItemList) do
        if query == "" then
            table.insert(FT_FilteredItemList, it)
        else
            local matchName = it.name and it.name:lower():find(query, 1, true)
            local matchRecipe = it.recipeName and it.recipeName:lower():find(query, 1, true)
            local matchId = tostring(it.id):find(query, 1, true)
            if matchName or matchRecipe or matchId then
                table.insert(FT_FilteredItemList, it)
            end
        end
    end
end

local function FT_RebuildItemList()
    FT_CachedItemList = {}
    local targets = (FT_SNIPER_CONFIG and FT_SNIPER_CONFIG.targets) or {}

    for rawId, tVal in pairs(targets) do
        local itemId = tonumber(rawId)
        if itemId then
            local defTarget = type(tVal) == "table" and (tVal.target or 0) or tonumber(tVal) or 0
            local name = (type(tVal) == "table" and tVal.name) or (FT_SNIPER_CONFIG and FT_SNIPER_CONFIG.names and FT_SNIPER_CONFIG.names[itemId]) or ("Item " .. itemId)
            local craftTarget = type(tVal) == "table" and tVal.craft_target
            local recipeName = type(tVal) == "table" and tVal.recipe_name

            table.insert(FT_CachedItemList, {
                id = itemId,
                defaultTarget = defTarget,
                name = name,
                craftTarget = craftTarget,
                recipeName = recipeName,
            })
        end
    end

    table.sort(FT_CachedItemList, function(a, b)
        if a.name == b.name then
            return a.id < b.id
        end
        return a.name < b.name
    end)

    FT_FilterItemList()
end

function FT_RefreshUIPanel(lightOnly)
    if not FTSniperMainFrame or not FTSniperMainFrame:IsShown() then return end

    if not lightOnly then
        if #FT_CachedItemList == 0 then
            FT_RebuildItemList()
        else
            FT_FilterItemList()
        end
    end

    if FTSniperMainFrame.titleText then
        FTSniperMainFrame.titleText:SetText(FT_GetTitleBarText())
    end

    local scrollFrame = FTSniperMainFrame.scrollFrame
    if not scrollFrame then return end

    local totalItems = #FT_FilteredItemList
    local offset = (FauxScrollFrame_GetOffset and FauxScrollFrame_GetOffset(scrollFrame)) or 0
    if offset > math.max(0, totalItems - NUM_ROWS) then
        offset = math.max(0, totalItems - NUM_ROWS)
        if FauxScrollFrame_SetOffset then
            FauxScrollFrame_SetOffset(scrollFrame, offset)
        end
    end

    if FauxScrollFrame_Update then
        FauxScrollFrame_Update(scrollFrame, totalItems, NUM_ROWS, ROW_HEIGHT)
    end

    for i = 1, NUM_ROWS do
        local row = FTSniperMainFrame.rows[i]
        local itemIdx = offset + i
        if itemIdx <= totalItems then
            local data = FT_FilteredItemList[itemIdx]
            row.itemId = data.id
            row.defaultTarget = data.defaultTarget

            -- Checkbox
            row.cb:SetChecked(FT_IsItemActive(data.id))

            -- Icon & Name
            local itemIcon = (type(GetItemIcon) == "function" and GetItemIcon(data.id)) or "Interface\\Icons\\INV_Misc_QuestionMark"
            row.icon:SetTexture(itemIcon)

            local itemDisplay = FT_GetItemDisplayName(data.id)
            if data.craftTarget and data.recipeName and data.recipeName ~= "" then
                row.nameText:SetText(string.format("%s |cff888888(%s)|r", itemDisplay, data.recipeName))
            else
                row.nameText:SetText(itemDisplay)
            end

            -- Default ceiling label: Дефолт: Xз Yс Zм
            row.defLabel:SetText(string.format("Дефолт: %s", FT_FormatMoneyText(data.defaultTarget)))

            -- Custom price EditBox
            if not row.eb:HasFocus() then
                local cp = FT_CustomTargets and FT_CustomTargets[data.id] and FT_CustomTargets[data.id].customPrice
                if cp and cp > 0 then
                    row.eb:SetText(FT_FormatMoneyText(cp))
                else
                    row.eb:SetText("")
                end
            end

            row:Show()
        else
            row.itemId = nil
            row:Hide()
        end
    end
end

local function FT_CreateMainUI()
    if FTSniperMainFrame then return FTSniperMainFrame end

    local frame = CreateFrame("Frame", "FTSniperMainFrame", UIParent, BackdropTemplateMixin and "BackdropTemplate" or nil)
    frame:SetSize(770, 420)
    frame:SetPoint("CENTER", UIParent, "CENTER", 0, 0)
    frame:SetFrameStrata("HIGH")
    frame:SetClampedToScreen(true)
    frame:EnableMouse(true)
    frame:SetMovable(true)
    frame:RegisterForDrag("LeftButton")
    frame:SetScript("OnDragStart", function(self)
        if self:GetParent() == UIParent then
            self:StartMoving()
        end
    end)
    frame:SetScript("OnDragStop", function(self)
        self:StopMovingOrSizing()
    end)

    if frame.SetBackdrop then
        frame:SetBackdrop({
            bgFile = "Interface\\DialogFrame\\UI-DialogBox-Background-Dark",
            edgeFile = "Interface\\DialogFrame\\UI-DialogBox-Border",
            tile = true, tileSize = 32, edgeSize = 16,
            insets = { left = 4, right = 4, top = 4, bottom = 4 }
        })
        frame:SetBackdropColor(0.05, 0.05, 0.08, 0.95)
    end

    -- Title bar with character & profession info from FT_SNIPER_CONFIG
    local titleText = frame:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")
    titleText:SetPoint("TOPLEFT", frame, "TOPLEFT", 16, -14)
    titleText:SetText(FT_GetTitleBarText())
    frame.titleText = titleText

    -- Close button
    local closeBtn = CreateFrame("Button", nil, frame, "UIPanelCloseButton")
    closeBtn:SetPoint("TOPRIGHT", frame, "TOPRIGHT", -4, -4)
    closeBtn:SetScript("OnClick", function()
        FT_ToggleUI()
    end)
    frame.closeBtn = closeBtn

    -- Top controls: [Включить все] [Отключить все] [Сброс к дефолту]
    local btnEnableAll = CreateFrame("Button", "FTSniperBtnEnableAll", frame, "UIPanelButtonTemplate")
    btnEnableAll:SetSize(115, 22)
    btnEnableAll:SetPoint("TOPLEFT", frame, "TOPLEFT", 16, -40)
    btnEnableAll:SetText("Включить все")
    btnEnableAll:SetScript("OnClick", function()
        FT_CustomTargets = FT_CustomTargets or {}
        if FT_SNIPER_CONFIG and FT_SNIPER_CONFIG.targets then
            for id in pairs(FT_SNIPER_CONFIG.targets) do
                local numId = tonumber(id)
                if numId then
                    FT_CustomTargets[numId] = FT_CustomTargets[numId] or {}
                    FT_CustomTargets[numId].enabled = true
                end
            end
        end
        FT_RefreshUIPanel(true)
        FT_Print("Все цели включены.")
    end)

    local btnDisableAll = CreateFrame("Button", "FTSniperBtnDisableAll", frame, "UIPanelButtonTemplate")
    btnDisableAll:SetSize(115, 22)
    btnDisableAll:SetPoint("LEFT", btnEnableAll, "RIGHT", 8, 0)
    btnDisableAll:SetText("Отключить все")
    btnDisableAll:SetScript("OnClick", function()
        FT_CustomTargets = FT_CustomTargets or {}
        if FT_SNIPER_CONFIG and FT_SNIPER_CONFIG.targets then
            for id in pairs(FT_SNIPER_CONFIG.targets) do
                local numId = tonumber(id)
                if numId then
                    FT_CustomTargets[numId] = FT_CustomTargets[numId] or {}
                    FT_CustomTargets[numId].enabled = false
                end
            end
        end
        FT_RefreshUIPanel(true)
        FT_Print("Все цели отключены.")
    end)

    local btnResetDefault = CreateFrame("Button", "FTSniperBtnResetDefault", frame, "UIPanelButtonTemplate")
    btnResetDefault:SetSize(135, 22)
    btnResetDefault:SetPoint("LEFT", btnDisableAll, "RIGHT", 8, 0)
    btnResetDefault:SetText("Сброс к дефолту")
    btnResetDefault:SetScript("OnClick", function()
        FT_CustomTargets = {}
        FT_RefreshUIPanel(true)
        FT_Print("Настройки сброшены к значениям по умолчанию.")
    end)

    -- Search box
    local searchBox = CreateFrame("EditBox", "FTSniperSearchBox", frame, "InputBoxTemplate")
    searchBox:SetSize(140, 20)
    searchBox:SetPoint("TOPRIGHT", frame, "TOPRIGHT", -32, -40)
    searchBox:SetAutoFocus(false)
    searchBox:SetMaxLetters(40)

    local searchLabel = frame:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    searchLabel:SetPoint("RIGHT", searchBox, "LEFT", -6, 0)
    searchLabel:SetText("Поиск:")

    searchBox:SetScript("OnTextChanged", function(self)
        FT_SearchQuery = self:GetText() or ""
        local scrollFrame = frame.scrollFrame
        if scrollFrame and FauxScrollFrame_SetOffset then
            FauxScrollFrame_SetOffset(scrollFrame, 0)
            local sb = _G[scrollFrame:GetName() .. "ScrollBar"]
            if sb then sb:SetValue(0) end
        end
        FT_FilterItemList()
        FT_RefreshUIPanel(true)
    end)

    searchBox:SetScript("OnEscapePressed", function(self)
        self:SetText("")
        self:ClearFocus()
    end)

    -- Column Headers
    local headerEn = frame:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    headerEn:SetPoint("TOPLEFT", frame, "TOPLEFT", 16, -70)
    headerEn:SetText("Вкл")

    local headerItem = frame:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    headerItem:SetPoint("LEFT", headerEn, "LEFT", 30, 0)
    headerItem:SetText("Предмет")

    local headerDef = frame:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    headerDef:SetPoint("TOPLEFT", frame, "TOPLEFT", 350, -70)
    headerDef:SetText("Дефолт. потолок")

    local headerCust = frame:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    headerCust:SetPoint("TOPLEFT", frame, "TOPLEFT", 520, -70)
    headerCust:SetText("Своя цена (медь / 1з 20с)")

    -- ScrollFrame
    local scrollFrame = CreateFrame("ScrollFrame", "FTSniperScrollFrame", frame, "FauxScrollFrameTemplate")
    scrollFrame:SetPoint("TOPLEFT", frame, "TOPLEFT", 16, -88)
    scrollFrame:SetPoint("BOTTOMRIGHT", frame, "BOTTOMRIGHT", -34, 16)

    scrollFrame:SetScript("OnVerticalScroll", function(self, offset)
        if FauxScrollFrame_OnVerticalScroll then
            FauxScrollFrame_OnVerticalScroll(self, offset, ROW_HEIGHT, function()
                FT_RefreshUIPanel(true)
            end)
        end
    end)

    scrollFrame:EnableMouseWheel(true)
    scrollFrame:SetScript("OnMouseWheel", function(self, delta)
        local sb = _G[self:GetName() .. "ScrollBar"]
        if sb then
            local current = sb:GetValue() or 0
            local step = ROW_HEIGHT * (delta > 0 and -2 or 2)
            sb:SetValue(current + step)
        end
    end)

    frame.scrollFrame = scrollFrame
    frame.rows = {}

    local function Row_OnEnter(self)
        local parent = self:GetParent()
        local itemId = parent and parent.itemId
        if not itemId or not GameTooltip then return end

        GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
        local _, link = GetItemInfo(itemId)
        if link then
            GameTooltip:SetHyperlink(link)
        else
            GameTooltip:SetHyperlink("item:" .. itemId)
        end

        local rawTarget = FT_SNIPER_CONFIG and FT_SNIPER_CONFIG.targets and FT_SNIPER_CONFIG.targets[itemId]
        if type(rawTarget) == "table" and rawTarget.craft_target and rawTarget.recipe_name then
            GameTooltip:AddLine(" ")
            GameTooltip:AddLine("|cff00ff00Перекрафт:|r " .. rawTarget.recipe_name, 1, 1, 1)
            if rawTarget.output_name then
                GameTooltip:AddLine("|cffffd100Выход:|r " .. rawTarget.output_name, 0.8, 0.8, 0.8)
            end
        end

        local defaultTarget = type(rawTarget) == "table" and rawTarget.target or rawTarget or 0
        local effective = FT_GetEffectiveTargetPrice(itemId, defaultTarget)
        GameTooltip:AddLine(" ")
        GameTooltip:AddLine("Дефолтный потолок: " .. FT_FormatMoneyText(defaultTarget), 0.7, 0.7, 0.7)
        if effective ~= defaultTarget then
            GameTooltip:AddLine("Кастомный потолок: " .. FT_FormatMoneyText(effective), 0.2, 1, 0.2)
        end
        GameTooltip:Show()
    end

    local function Row_OnLeave()
        if GameTooltip then
            GameTooltip:Hide()
        end
    end

    local function SaveEditBoxPrice(eb)
        local row = eb:GetParent()
        local itemId = row and row.itemId
        if not itemId then return end

        local text = eb:GetText()
        local val = FT_ParseMoneyInput(text)
        FT_CustomTargets = FT_CustomTargets or {}
        FT_CustomTargets[itemId] = FT_CustomTargets[itemId] or {}
        if val and val > 0 then
            FT_CustomTargets[itemId].customPrice = val
            if FT_CustomTargets[itemId].enabled == nil then
                FT_CustomTargets[itemId].enabled = true
            end
            eb:SetText(FT_FormatMoneyText(val))
        else
            FT_CustomTargets[itemId].customPrice = nil
            eb:SetText("")
        end
    end

    for i = 1, NUM_ROWS do
        local row = CreateFrame("Frame", "FTSniperRow" .. i, frame)
        row:SetHeight(ROW_HEIGHT)
        row:SetPoint("TOPLEFT", frame, "TOPLEFT", 16, -88 - (i - 1) * ROW_HEIGHT)
        row:SetPoint("RIGHT", frame, "RIGHT", -36, 0)
        row:EnableMouseWheel(true)
        row:SetScript("OnMouseWheel", function(_, delta)
            scrollFrame:GetScript("OnMouseWheel")(scrollFrame, delta)
        end)

        -- Checkbox
        local cb = CreateFrame("CheckButton", "FTSniperRow" .. i .. "CB", row, "UICheckButtonTemplate")
        cb:SetSize(20, 20)
        cb:SetPoint("LEFT", row, "LEFT", 0, 0)
        cb:SetScript("OnClick", function(self)
            local r = self:GetParent()
            local itemId = r and r.itemId
            if not itemId then return end
            FT_CustomTargets = FT_CustomTargets or {}
            FT_CustomTargets[itemId] = FT_CustomTargets[itemId] or {}
            FT_CustomTargets[itemId].enabled = self:GetChecked() and true or false
            FT_RefreshUIPanel(true)
        end)
        row.cb = cb

        -- Icon
        local icon = row:CreateTexture(nil, "ARTWORK")
        icon:SetSize(18, 18)
        icon:SetPoint("LEFT", cb, "RIGHT", 4, 0)
        icon:SetTexture("Interface\\Icons\\INV_Misc_QuestionMark")
        row.icon = icon

        local iconBtn = CreateFrame("Button", nil, row)
        iconBtn:SetAllPoints(icon)
        iconBtn:SetScript("OnEnter", Row_OnEnter)
        iconBtn:SetScript("OnLeave", Row_OnLeave)

        -- Item name button
        local itemBtn = CreateFrame("Button", nil, row)
        itemBtn:SetHeight(20)
        itemBtn:SetPoint("LEFT", icon, "RIGHT", 6, 0)
        itemBtn:SetPoint("RIGHT", row, "LEFT", 340, 0)
        itemBtn:SetScript("OnEnter", Row_OnEnter)
        itemBtn:SetScript("OnLeave", Row_OnLeave)

        local nameText = itemBtn:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
        nameText:SetJustifyH("LEFT")
        nameText:SetAllPoints(itemBtn)
        row.nameText = nameText

        -- Default ceiling label: Дефолт: Xз Yс Zм
        local defLabel = row:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
        defLabel:SetPoint("LEFT", row, "LEFT", 350, 0)
        defLabel:SetPoint("RIGHT", row, "LEFT", 510, 0)
        defLabel:SetJustifyH("LEFT")
        row.defLabel = defLabel

        -- Custom price EditBox
        local eb = CreateFrame("EditBox", "FTSniperRow" .. i .. "EB", row, "InputBoxTemplate")
        eb:SetSize(130, 20)
        eb:SetPoint("LEFT", row, "LEFT", 520, 0)
        eb:SetAutoFocus(false)
        eb:SetMaxLetters(25)

        eb:SetScript("OnEnterPressed", function(self)
            SaveEditBoxPrice(self)
            self:ClearFocus()
            FT_RefreshUIPanel(true)
        end)

        eb:SetScript("OnEditFocusLost", function(self)
            SaveEditBoxPrice(self)
            FT_RefreshUIPanel(true)
        end)

        eb:SetScript("OnEscapePressed", function(self)
            self:ClearFocus()
            FT_RefreshUIPanel(true)
        end)

        eb:SetScript("OnTextChanged", function(self, isUserInput)
            if not isUserInput then return end
            local r = self:GetParent()
            local itemId = r and r.itemId
            if not itemId then return end

            local text = self:GetText()
            local val = FT_ParseMoneyInput(text)
            FT_CustomTargets = FT_CustomTargets or {}
            FT_CustomTargets[itemId] = FT_CustomTargets[itemId] or {}
            if val and val > 0 then
                FT_CustomTargets[itemId].customPrice = val
                if FT_CustomTargets[itemId].enabled == nil then
                    FT_CustomTargets[itemId].enabled = true
                end
            else
                FT_CustomTargets[itemId].customPrice = nil
            end
        end)

        row.eb = eb

        frame.rows[i] = row
    end

    FTSniperMainFrame = frame
    return frame
end

function FT_ShowUIPanel(attachToAH)
    if not FTSniperMainFrame then
        FT_CreateMainUI()
    end

    if attachToAH and AuctionFrame and AuctionFrame:IsShown() then
        FTSniperMainFrame:SetParent(AuctionFrame)
        FTSniperMainFrame:ClearAllPoints()
        FTSniperMainFrame:SetPoint("TOPLEFT", AuctionFrame, "TOPLEFT", 18, -68)
        FTSniperMainFrame:SetPoint("BOTTOMRIGHT", AuctionFrame, "BOTTOMRIGHT", -18, 36)
        if FTSniperMainFrame.closeBtn then
            FTSniperMainFrame.closeBtn:Hide()
        end
    else
        FTSniperMainFrame:SetParent(UIParent)
        FTSniperMainFrame:ClearAllPoints()
        FTSniperMainFrame:SetPoint("CENTER", UIParent, "CENTER", 0, 0)
        FTSniperMainFrame:SetSize(770, 420)
        if FTSniperMainFrame.closeBtn then
            FTSniperMainFrame.closeBtn:Show()
        end
    end

    FTSniperMainFrame:Show()
    FT_RebuildItemList()
    FT_RefreshUIPanel()
end

function FT_SelectSniperTab(tabIndex)
    tabIndex = tabIndex or FTSniperAuctionTabIndex
    if not AuctionFrame then return end

    if tabIndex and PanelTemplates_SetTab then
        PanelTemplates_SetTab(AuctionFrame, tabIndex)
    end

    if AuctionFrameBrowse then AuctionFrameBrowse:Hide() end
    if AuctionFrameBid then AuctionFrameBid:Hide() end
    if AuctionFrameAuctions then AuctionFrameAuctions:Hide() end

    if AuctionatorShoppingFrame then AuctionatorShoppingFrame:Hide() end
    if AuctionatorSellingFrame then AuctionatorSellingFrame:Hide() end
    if AuctionatorCancellingFrame then AuctionatorCancellingFrame:Hide() end

    FT_ShowUIPanel(true)
end

function FT_ToggleUI()
    if FTSniperMainFrame and FTSniperMainFrame:IsShown() then
        FTSniperMainFrame:Hide()
        if AuctionFrame and AuctionFrame:IsShown() and FTSniperAuctionTabIndex and PanelTemplates_GetSelectedTab and PanelTemplates_GetSelectedTab(AuctionFrame) == FTSniperAuctionTabIndex then
            local tab1 = _G["AuctionFrameTab1"]
            if tab1 then
                tab1:Click()
            end
        end
    else
        if AuctionFrame and AuctionFrame:IsShown() and FTSniperAuctionTabIndex then
            FT_SelectSniperTab(FTSniperAuctionTabIndex)
        else
            FT_ShowUIPanel(false)
        end
    end
end

local function FT_RegisterAuctionTab()
    if not AuctionFrame then return end
    if FTSniperAuctionTabIndex then return end
    if type(AuctionFrame.numTabs) ~= "number" then return end

    local tabIndex = AuctionFrame.numTabs + 1
    local tabName = "AuctionFrameTab" .. tabIndex
    local tab = CreateFrame("Button", tabName, AuctionFrame, "CharacterFrameTabButtonTemplate")
    if not tab then return end

    tab:SetID(tabIndex)
    tab:SetText("Снайпер FT")
    if PanelTemplates_SetNumTabs then
        PanelTemplates_SetNumTabs(AuctionFrame, tabIndex)
    end
    if PanelTemplates_EnableTab then
        PanelTemplates_EnableTab(AuctionFrame, tabIndex)
    end

    local prevTab = _G["AuctionFrameTab" .. (tabIndex - 1)]
    if prevTab then
        tab:SetPoint("LEFT", prevTab, "RIGHT", -15, 0)
    else
        tab:SetPoint("BOTTOMLEFT", AuctionFrame, "BOTTOMLEFT", 15, 12)
    end

    tab:SetScript("OnClick", function(self)
        FT_SelectSniperTab(tabIndex)
    end)

    FTSniperAuctionTab = tab
    FTSniperAuctionTabIndex = tabIndex

    for i = 1, (AuctionFrame.numTabs or 3) do
        local otherTab = _G["AuctionFrameTab" .. i]
        if otherTab and otherTab ~= tab and not otherTab._ftHooked then
            otherTab._ftHooked = true
            otherTab:HookScript("OnClick", function(self)
                if FTSniperMainFrame and FTSniperMainFrame:IsShown() and self:GetID() ~= FTSniperAuctionTabIndex then
                    FTSniperMainFrame:Hide()
                end
            end)
        end
    end

    if type(AuctionFrameTab_OnClick) == "function" and not AuctionFrameTab_OnClick._ftHooked then
        hooksecurefunc("AuctionFrameTab_OnClick", function(self, button)
            if self:GetID() ~= FTSniperAuctionTabIndex then
                if FTSniperMainFrame and FTSniperMainFrame:IsShown() and FTSniperMainFrame:GetParent() == AuctionFrame then
                    FTSniperMainFrame:Hide()
                end
            end
        end)
        AuctionFrameTab_OnClick._ftHooked = true
    end
end

local function FT_UpdateToggleButton()
    if not toggleBtn then return end
    if FT_IsEnabled then
        toggleBtn:SetText("|cff00ff00[Sniper: ON]|r")
    else
        toggleBtn:SetText("|cffff4444[Sniper: OFF]|r")
    end
end

function FT_SetEnabled(enable)
    FT_IsEnabled = enable
    FT_UpdateToggleButton()
    if not enable then
        FT_DisarmSniperBuy()
        FT_AwaitingResults = false
        FT_Print("Sniper PAUSED. Safe manual AH browsing.")
    else
        FT_TargetPage = 0
        FT_CheckPrevPageNext = false
        timeSinceLastQuery = 1.2
        FT_Print(string.format("Sniper ACTIVE. Watching %s (min profit: %dc).", FT_IsModernAH() and "Modern AH feed" or "last AH page", FT_GetEffectiveMinProfit()))
    end
end

function FT_Toggle()
    FT_SetEnabled(not FT_IsEnabled)
end

local function FT_CreateToggleButton()
    local ah = FT_GetAuctionFrame()
    if not ah then return end
    if toggleBtn then
        toggleBtn:Show()
        return
    end

    toggleBtn = CreateFrame("Button", "FTSniperToggleBtn", UIParent, "UIPanelButtonTemplate")
    toggleBtn:SetSize(110, 22)
    -- High strata and level so Auctionator/custom frames never occlude it
    toggleBtn:SetFrameStrata("DIALOG")
    toggleBtn:SetFrameLevel(9999)

    -- Anchor to top title bar of AuctionHouseFrame or AuctionFrame in open visible space
    toggleBtn:ClearAllPoints()
    toggleBtn:SetPoint("TOP", ah, "TOP", 70, -14)

    -- Draggable with Shift+LeftClick
    toggleBtn:SetMovable(true)
    toggleBtn:EnableMouse(true)
    toggleBtn:RegisterForDrag("LeftButton")
    toggleBtn:SetClampedToScreen(true)
    toggleBtn:SetScript("OnDragStart", function(self)
        if IsShiftKeyDown() then
            self.isDragging = true
            self:StartMoving()
        end
    end)
    toggleBtn:SetScript("OnDragStop", function(self)
        self:StopMovingOrSizing()
    end)

    toggleBtn:RegisterForClicks("LeftButtonUp", "RightButtonUp")
    toggleBtn:SetScript("OnClick", function(self, button)
        if self.isDragging or IsShiftKeyDown() then
            self.isDragging = false
            return
        end
        if button == "RightButton" then
            FT_ToggleUI()
        else
            FT_Toggle()
        end
    end)

    toggleBtn:SetScript("OnEnter", function(self)
        if GameTooltip then
            GameTooltip:SetOwner(self, "ANCHOR_TOP")
            GameTooltip:AddLine("ForeverTrade Sniper v" .. VERSION, 0, 1, 0)
            GameTooltip:AddLine("ЛКМ: переключить Sniper ВКЛ / ВЫКЛ.", 1, 1, 1)
            GameTooltip:AddLine("ПКМ: открыть панель настроек целей (/fts ui).", 0.8, 0.8, 0.8)
            GameTooltip:AddLine("Shift+Перетаскивание: переместить кнопку.", 0.7, 0.7, 0.7)
            if FT_IsEnabled then
                GameTooltip:AddLine("Статус: |cff00ff00АКТИВЕН|r", 0.2, 1, 0.2)
            else
                GameTooltip:AddLine("Статус: |cffff4444ВЫКЛЮЧЕН|r", 1, 0.3, 0.3)
            end
            local engineName = FT_IsModernAH() and "Modern AH (C_AuctionHouse)" or "Legacy AH"
            GameTooltip:AddLine("Движок: |cffffd100" .. engineName .. "|r", 0.8, 0.8, 0.8)
            GameTooltip:AddLine(string.format("Запросов отправлено: %d | Лотов проверено: %d", FT_QueriesCount, FT_LotsScannedCount), 0.9, 0.9, 0.9)
            local targetCount = 0
            if FT_SNIPER_CONFIG and FT_SNIPER_CONFIG.targets then
                for _ in pairs(FT_SNIPER_CONFIG.targets) do targetCount = targetCount + 1 end
            end
            local minProf = FT_GetEffectiveMinProfit()
            GameTooltip:AddLine(string.format("Целей в базе: %d | Мин. профит: %d мед. (%.2fs)", targetCount, minProf, minProf / 100), 0.7, 0.7, 0.7)
            GameTooltip:Show()
        end
    end)
    toggleBtn:SetScript("OnLeave", function(self)
        if GameTooltip then
            GameTooltip:Hide()
        end
    end)

    FT_UpdateToggleButton()
    toggleBtn:Show()
end

local function FT_HookAuctionFrame()
    local ah = FT_GetAuctionFrame()
    if ah and not ah._ftHooked then
        ah._ftHooked = true
        ah:HookScript("OnShow", function()
            FT_RegisterAuctionTab()
            FT_CreateToggleButton()
            if toggleBtn then
                toggleBtn:Show()
            end
        end)
        ah:HookScript("OnHide", function()
            if toggleBtn then
                toggleBtn:Hide()
            end
            if FTSniperMainFrame and FTSniperMainFrame:GetParent() == AuctionFrame then
                FTSniperMainFrame:Hide()
            end
        end)
    end
    FT_RegisterAuctionTab()
end

-- Slash commands for quick manual control
SLASH_FOREVERTRADESNIPER1 = "/fts"
SLASH_FOREVERTRADESNIPER2 = "/forevertrade"
SlashCmdList["FOREVERTRADESNIPER"] = function(msg)
    msg = (msg or ""):lower():match("^%s*(.-)%s*$")
    local profitArg = msg:match("^profit%s+(%d+)") or msg:match("^min%s+(%d+)")
    if profitArg then
        FT_CustomMinProfit = tonumber(profitArg)
        FT_Print(string.format("Минимальный профит изменён на: |cffffd100%d|r мед. (%.2fs)", FT_CustomMinProfit, FT_CustomMinProfit / 100))
    elseif msg == "ui" or msg == "config" or msg == "menu" then
        FT_ToggleUI()
    elseif msg == "on" or msg == "start" or msg == "1" then
        FT_SetEnabled(true)
    elseif msg == "off" or msg == "stop" or msg == "0" then
        FT_SetEnabled(false)
    elseif msg == "status" or msg == "stat" then
        FT_PrintStatus()
    elseif msg == "test" then
        FT_Print("|cffffd100[ТЕСТ СНАЙПЕРА]|r Запуск эмуляции перехвата лота (Превосходное лечебное зелье x5 за 500c).")
        FT_ArmSniperBuy(0, 3928, 500, 5, 200, "test_hash")
        FT_Print("|cffff0000>>> ПОКРУТИ КОЛЕСО МЫШИ ДЛЯ ТЕСТОВОГО ВЫКУПА <<<|r")
    elseif msg == "sound" then
        FT_Print("Воспроизведение тестового сигнала тревоги (Master канал)...")
        FT_PlayAlertSound()
    elseif msg == "help" or msg == "?" then
        FT_Print("Команды ForeverTrade Sniper:")
        FT_Print("  /fts ui (или /fts config) - открыть панель настроек целей")
        FT_Print("  /fts on|off - включить / выключить снайпер")
        FT_Print("  /fts status - текущий статус и статистика")
        FT_Print("  /fts sound - проверка звука тревоги на Master канале")
        FT_Print("  /fts profit <мед> - задать мин. порог выгоды в меди")
        FT_Print("  /fts test - симуляция перехвата лота и проверка биндов")
        FT_Print("  /fts reset - сбросить позицию кнопки на панель аукциона")
        FT_Print("  /fts (без параметров) - переключить вкл/выкл")
    elseif msg == "reset" then
        local ah = FT_GetAuctionFrame()
        if toggleBtn and ah then
            toggleBtn:ClearAllPoints()
            toggleBtn:SetPoint("TOP", ah, "TOP", 70, -14)
            toggleBtn:Show()
            FT_Print("Button position reset to title bar.")
        end
    else
        FT_Toggle()
    end
end

local frame = CreateFrame("Frame", "ForeverTradeSniperFrame")

frame:SetScript("OnUpdate", function(self, elapsed)
    local ah = FT_GetAuctionFrame()
    if ah and ah:IsShown() then
        if not toggleBtn then
            FT_CreateToggleButton()
        elseif not toggleBtn:IsShown() then
            toggleBtn:Show()
        end
        if not FTSniperAuctionTabIndex then
            FT_RegisterAuctionTab()
        end
    elseif toggleBtn and toggleBtn:IsShown() then
        toggleBtn:Hide()
    end
    if not ah or not ah:IsShown() then return end
    if not FT_IsEnabled then return end

    if FT_IsArmed then
        armedTimer = armedTimer + elapsed
        if armedTimer >= 4.0 then
            FT_DisarmSniperBuy() -- Timeout: disarm and resume scanning
        end
        return
    end

    timeSinceLastQuery = timeSinceLastQuery + elapsed
    -- Awaiting results timeout protection (3.0s)
    if FT_AwaitingResults and timeSinceLastQuery < 3.0 then return end

    if timeSinceLastQuery >= 1.5 then
        local canSend = false
        if FT_IsModernAH() then
            canSend = (C_AuctionHouse.IsThrottledMessageSystemReady == nil) or C_AuctionHouse.IsThrottledMessageSystemReady()
        elseif type(CanSendAuctionQuery) == "function" then
            canSend = CanSendAuctionQuery()
        end

        if canSend then
            timeSinceLastQuery = 0
            armedTimer = 0
            FT_AwaitingResults = true
            FT_QueriesCount = FT_QueriesCount + 1

            if FT_IsModernAH() and C_AuctionHouse and C_AuctionHouse.SendBrowseQuery then
                local sorts = {}
                if Enum and Enum.AuctionHouseSortOrder and Enum.AuctionHouseSortOrder.Price then
                    sorts = { { sortOrder = Enum.AuctionHouseSortOrder.Price, reverseSort = false } }
                end
                pcall(C_AuctionHouse.SendBrowseQuery, { searchString = "", minLevel = nil, maxLevel = nil, filters = {}, sorts = sorts, itemClassFilters = {} })
            elseif type(QueryAuctionItems) == "function" then
                QueryAuctionItems("", nil, nil, FT_TargetPage, false, nil, false, false, nil)
            end
        end
    end
end)

local function FT_OnEvent(self, event, ...)
    local arg1 = ...
    if event == "PLAYER_LOGIN" then
        FT_CustomTargets = FT_CustomTargets or {}
        FT_HookAuctionFrame()
        local count = 0
        if FT_SNIPER_CONFIG and FT_SNIPER_CONFIG.targets then
            for _ in pairs(FT_SNIPER_CONFIG.targets) do
                count = count + 1
            end
        end
        local genAt = (FT_SNIPER_CONFIG and FT_SNIPER_CONFIG.generated_at) or 0
        local dateStr = "default"
        if genAt > 0 and type(date) == "function" then
            pcall(function() dateStr = date("%Y-%m-%d %H:%M:%S", genAt) end)
        end
        FT_Print(string.format("Loaded v%s with %d targets (generated: %s).", VERSION, count, dateStr))

    elseif event == "VARIABLES_LOADED" then
        FT_CustomTargets = FT_CustomTargets or {}

    elseif event == "ADDON_LOADED" then
        if arg1 == ADDON_NAME then
            FT_CustomTargets = FT_CustomTargets or {}
        end
        if arg1 == "Blizzard_AuctionHouseUI" or arg1 == "Blizzard_AuctionUI" or arg1 == "Auctionator" or arg1 == ADDON_NAME then
            FT_HookAuctionFrame()
            local ah = FT_GetAuctionFrame()
            if ah and ah:IsShown() then
                FT_CreateToggleButton()
                FT_RegisterAuctionTab()
            end
        end

    elseif event == "AUCTION_HOUSE_SHOW" then
        FT_HookAuctionFrame()
        FT_RegisterAuctionTab()
        FT_CreateToggleButton()
        FT_SetEnabled(false) -- Default OFF: never interferes with manual buying/selling
        FT_DisarmSniperBuy()

    elseif event == "AUCTION_HOUSE_CLOSED" then
        FT_SetEnabled(false)
        FT_DisarmSniperBuy()
        FT_AwaitingResults = false
        if toggleBtn then
            toggleBtn:Hide()
        end
        if FTSniperMainFrame and FTSniperMainFrame:GetParent() == AuctionFrame then
            FTSniperMainFrame:Hide()
        end

    elseif event == "AUCTION_HOUSE_BROWSE_FAILURE" then
        FT_AwaitingResults = false

    elseif event == "AUCTION_ITEM_LIST_UPDATE" or event == "AUCTION_HOUSE_BROWSE_RESULTS_UPDATED" or event == "AUCTION_HOUSE_BROWSE_RESULTS_ADDED" then
        if not FT_IsEnabled then return end
        FT_AwaitingResults = false
        FT_UpdateTargetPage()
        if not FT_IsArmed then
            FT_ScanCurrentPage(arg1)
        end

    elseif event == "GET_ITEM_INFO_RECEIVED" then
        if FTSniperMainFrame and FTSniperMainFrame:IsShown() then
            FT_RefreshUIPanel(true)
        end

    elseif event == "PLAYER_REGEN_ENABLED" then
        if pendingClearBindings or not FT_IsArmed then
            ClearOverrideBindings(buyBtn)
            pendingClearBindings = false
        end

    elseif event == "PLAYER_REGEN_DISABLED" then
        if FT_IsArmed then
            FT_DisarmSniperBuy()
        end

    elseif event == "COMMODITY_PRICE_UPDATED" then
        local newUnitPrice, newTotalPrice = ...
        if FT_PendingCommodity then
            local p = FT_PendingCommodity
            FT_PendingCommodity = nil
            if newUnitPrice and newUnitPrice <= p.unitTarget then
                local ok = pcall(C_AuctionHouse.ConfirmCommoditiesPurchase, p.itemId, p.count)
                if ok then
                    local itemDisplay = FT_GetItemDisplayName(p.itemId)
                    FT_Print(string.format("Выкуплен биржевой товар %s (x%d) за %d мед.!", itemDisplay, p.count, newTotalPrice or (newUnitPrice * p.count)))
                    if p.hash then
                        FT_SeenCache[p.hash] = nil
                    end
                else
                    FT_Print("|cffff4444[СНАЙП ОШИБКА]|r Подтверждение покупки отклонено клиентом.")
                end
            else
                if C_AuctionHouse and C_AuctionHouse.CancelCommoditiesPurchase then
                    pcall(C_AuctionHouse.CancelCommoditiesPurchase)
                end
                FT_Print("|cffff0000[СНАЙП ОТМЕНЁН]|r Цена изменилась при подтверждении котировки!")
            end
        end

    elseif event == "COMMODITY_PRICE_UNAVAILABLE" or event == "COMMODITY_PURCHASE_FAILED" then
        if FT_PendingCommodity then
            FT_PendingCommodity = nil
            FT_Print("|cffff0000[СНАЙП ОТМЕНЁН]|r Товар более недоступен на аукционе.")
        end
    end
end

-- Safely register events with pcall to prevent crashes on engine-specific missing events
local function FT_SafeRegister(ev)
    pcall(frame.RegisterEvent, frame, ev)
end

FT_SafeRegister("PLAYER_LOGIN")
FT_SafeRegister("VARIABLES_LOADED")
FT_SafeRegister("ADDON_LOADED")
FT_SafeRegister("AUCTION_HOUSE_SHOW")
FT_SafeRegister("AUCTION_HOUSE_CLOSED")
FT_SafeRegister("AUCTION_HOUSE_BROWSE_FAILURE")
FT_SafeRegister("AUCTION_ITEM_LIST_UPDATE")
FT_SafeRegister("AUCTION_HOUSE_BROWSE_RESULTS_UPDATED")
FT_SafeRegister("AUCTION_HOUSE_BROWSE_RESULTS_ADDED")
FT_SafeRegister("GET_ITEM_INFO_RECEIVED")
FT_SafeRegister("PLAYER_REGEN_ENABLED")
FT_SafeRegister("PLAYER_REGEN_DISABLED")
FT_SafeRegister("COMMODITY_PRICE_UPDATED")
FT_SafeRegister("COMMODITY_PRICE_UNAVAILABLE")
FT_SafeRegister("COMMODITY_PURCHASE_FAILED")
frame:SetScript("OnEvent", FT_OnEvent)

-- Also handle if loaded after PLAYER_LOGIN
if IsLoggedIn and IsLoggedIn() then
    local count = 0
    if FT_SNIPER_CONFIG and FT_SNIPER_CONFIG.targets then
        for _ in pairs(FT_SNIPER_CONFIG.targets) do
            count = count + 1
        end
    end
    local genAt = (FT_SNIPER_CONFIG and FT_SNIPER_CONFIG.generated_at) or 0
    local dateStr = "default"
    if genAt > 0 and type(date) == "function" then
        pcall(function() dateStr = date("%Y-%m-%d %H:%M:%S", genAt) end)
    end
    FT_Print(string.format("Loaded v%s with %d targets (generated: %s).", VERSION, count, dateStr))
end
