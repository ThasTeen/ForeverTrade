-- ForeverTrade_Sniper v2.1
-- Standalone AH sniper with hardware-protected buying and ring-buffer deduplication

local ADDON_NAME = "ForeverTrade_Sniper"
local VERSION = "2.1"

-- Global state variables
FT_TargetPage = 0
FT_CheckPrevPageNext = false
FT_IsArmed = false
FT_AwaitingResults = false
FT_SeenCache = {}
FT_SeenOrder = {}
local MAX_SEEN_CACHE = 200

local timeSinceLastQuery = 0
local armedTimer = 0

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

-- Mechanism 1: Page Indexing & Page Shift Compensation
function FT_UpdateTargetPage()
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
buyBtn:SetAttribute("type", "macro")

function FT_DisarmSniperBuy()
    FT_IsArmed = false
    armedTimer = 0
    if not InCombatLockdown() then
        ClearOverrideBindings(buyBtn)
    end
end

function FT_ExecuteVerifiedBuy(index, expectedItemId, expectedBuyout, hash)
    local info = { GetAuctionItemInfo("list", index) }
    local count = info[3]
    local buyoutPrice = info[10] or 0
    local link = GetAuctionItemLink("list", index)
    local actualItemId = (info[17] and info[17] > 0 and info[17]) or (link and tonumber(string.match(link, "item:(%d+)")))

    if actualItemId == expectedItemId and buyoutPrice == expectedBuyout and buyoutPrice > 0 then
        PlaceAuctionBid("list", index, expectedBuyout)
        FT_Print(string.format("Buying item %d (x%d) for %d copper!", expectedItemId, count or 1, expectedBuyout))
        if hash then
            FT_SeenCache[hash] = nil -- Allow buying next identical stack if available
        end
    end
    FT_DisarmSniperBuy()
end

function FT_ArmSniperBuy(index, itemId, buyout, hash)
    FT_IsArmed = true
    armedTimer = 0
    pcall(PlaySoundFile, "Sound\\Interface\\AuctionWindowOpen.ogg")
    buyBtn:SetAttribute("macrotext", string.format("/run FT_ExecuteVerifiedBuy(%d, %d, %d, %q)", index, itemId, buyout, hash or ""))
    SetOverrideBindingClick(buyBtn, true, "MOUSEWHEELUP", "FTSniperBuyBtn")
    SetOverrideBindingClick(buyBtn, true, "MOUSEWHEELDOWN", "FTSniperBuyBtn")
end

-- Mechanism 3: Unit-price check, profit filter, candidate selection
local function FT_ScanCurrentPage()
    local batchCount, totalCount = GetNumAuctionItems("list")
    if not batchCount or batchCount <= 0 then return end

    local minProfit = (FT_SNIPER_CONFIG and FT_SNIPER_CONFIG.min_profit_copper) or 1000
    local targets = (FT_SNIPER_CONFIG and FT_SNIPER_CONFIG.targets) or {}
    local bestCandidate = nil

    for i = 1, batchCount do
        local info = { GetAuctionItemInfo("list", i) }
        local count = info[3]
        local minBid = info[8] or 0
        local buyout = info[10] or 0
        local link = GetAuctionItemLink("list", i)
        local itemId = (info[17] and info[17] > 0 and info[17]) or (link and tonumber(string.match(link, "item:(%d+)")))
        local unitTarget = itemId and targets[itemId]

        if unitTarget and buyout and buyout > 0 and count and count > 0 then
            local unitPrice = buyout / count
            local totalSaved = (unitTarget - unitPrice) * count
            if unitPrice <= unitTarget and totalSaved >= minProfit then
                -- Hash excludes owner and timeLeft to avoid false re-triggers on async loads
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

    if bestCandidate then
        FT_ArmSniperBuy(bestCandidate.index, bestCandidate.itemId, bestCandidate.buyout, bestCandidate.hash)
        FT_AddSeen(bestCandidate.hash)
        local itemName = bestCandidate.link or string.format("item:%d", bestCandidate.itemId)
        FT_Print(string.format(
            "|cffffd100[SNIPE ALERT]|r %s x%d: Buyout=%dc (Unit: %dc <= Target: %dc). Saved=%dc! |cffff0000[SCROLL MOUSE WHEEL TO BUY]|r",
            itemName, bestCandidate.count, bestCandidate.buyout, math.floor(bestCandidate.unitPrice),
            bestCandidate.unitTarget, math.floor(bestCandidate.totalSaved)
        ))
    end
end

-- Mechanism 2: Token-bucket OnUpdate rate limiter and event listener
local frame = CreateFrame("Frame", "ForeverTradeSniperFrame")

frame:SetScript("OnUpdate", function(self, elapsed)
    if not AuctionFrame or not AuctionFrame:IsShown() then return end

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

    if timeSinceLastQuery >= 1.2 and CanSendAuctionQuery() then
        timeSinceLastQuery = 0
        armedTimer = 0
        FT_AwaitingResults = true
        QueryAuctionItems("", nil, nil, FT_TargetPage, 0, 0, false, false, nil)
    end
end)

local function FT_OnEvent(self, event, ...)
    if event == "PLAYER_LOGIN" then
        local count = 0
        if FT_SNIPER_CONFIG and FT_SNIPER_CONFIG.targets then
            for _ in pairs(FT_SNIPER_CONFIG.targets) do
                count = count + 1
            end
        end
        local genAt = (FT_SNIPER_CONFIG and FT_SNIPER_CONFIG.generated_at) or 0
        local dateStr = (genAt > 0 and date("%Y-%m-%d %H:%M:%S", genAt)) or "default"
        FT_Print(string.format("Loaded v%s with %d targets (generated: %s).", VERSION, count, dateStr))

    elseif event == "AUCTION_HOUSE_SHOW" then
        FT_TargetPage = 0
        FT_CheckPrevPageNext = false
        FT_IsArmed = false
        FT_AwaitingResults = false
        timeSinceLastQuery = 1.2 -- ready to query

    elseif event == "AUCTION_HOUSE_CLOSED" then
        FT_DisarmSniperBuy()
        FT_AwaitingResults = false

    elseif event == "AUCTION_ITEM_LIST_UPDATE" then
        FT_AwaitingResults = false
        FT_UpdateTargetPage()
        if not FT_IsArmed then
            FT_ScanCurrentPage()
        end
    end
end

frame:RegisterEvent("PLAYER_LOGIN")
frame:RegisterEvent("AUCTION_HOUSE_SHOW")
frame:RegisterEvent("AUCTION_HOUSE_CLOSED")
frame:RegisterEvent("AUCTION_ITEM_LIST_UPDATE")
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
    local dateStr = (genAt > 0 and date("%Y-%m-%d %H:%M:%S", genAt)) or "default"
    FT_Print(string.format("Loaded v%s with %d targets (generated: %s).", VERSION, count, dateStr))
end
