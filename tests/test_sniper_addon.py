"""Unit and integration tests for ForeverTrade_Sniper WoW addon.

Under Ponytail doctrine:
Verifies static Lua code integrity, global invariants, slash command parsing,
UI toggle button state, rate limiting, and hardware-protected buy verification.
"""

import os
import re
import pytest

ADDON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "addons", "ForeverTrade_Sniper")
LUA_FILE = os.path.join(ADDON_DIR, "ForeverTrade_Sniper.lua")
TOC_FILE = os.path.join(ADDON_DIR, "ForeverTrade_Sniper.toc")


# ===========================================================================
# 1. Static Lua & TOC Invariant Tests
# ===========================================================================

def test_addon_files_exist_and_metadata():
    """Verify addon directory, TOC file, and Lua file exist with proper versioning."""
    assert os.path.isdir(ADDON_DIR), f"Addon directory missing: {ADDON_DIR}"
    assert os.path.isfile(TOC_FILE), f"TOC file missing: {TOC_FILE}"
    assert os.path.isfile(LUA_FILE), f"Lua file missing: {LUA_FILE}"

    with open(TOC_FILE, "r", encoding="utf-8") as f:
        toc = f.read()
    assert "## Title: ForeverTrade_Sniper" in toc
    assert "## Version: 2.1" in toc
    assert "ForeverTrade_Sniper.lua" in toc
    assert "OptionalDeps: Blizzard_AuctionHouseUI, Blizzard_AuctionUI, Auctionator" in toc


def test_lua_default_disabled_invariant():
    """Verify FT_IsEnabled is initialized to false at file root."""
    with open(LUA_FILE, "r", encoding="utf-8") as f:
        lua = f.read()

    match = re.search(r"^FT_IsEnabled\s*=\s*(false|true)", lua, re.MULTILINE)
    assert match is not None, "FT_IsEnabled global declaration not found"
    assert match.group(1) == "false", "FT_IsEnabled must be false by default"


def test_lua_toggle_button_implementation():
    """Verify FTSniperToggleBtn creation, anchoring, and dynamic color texts."""
    with open(LUA_FILE, "r", encoding="utf-8") as f:
        lua = f.read()

    assert "FTSniperToggleBtn" in lua
    assert "|cff00ff00[Sniper: ON]|r" in lua
    assert "|cffff4444[Sniper: OFF]|r" in lua
    assert "FT_UpdateToggleButton" in lua
    assert "FT_CreateToggleButton" in lua
    assert 'SetFrameStrata("DIALOG")' in lua
    assert "SetFrameLevel(9999)" in lua
    assert "SetMovable(true)" in lua
    assert 'RegisterForDrag("LeftButton")' in lua


def test_lua_control_functions():
    """Verify FT_SetEnabled and FT_Toggle functions exist with correct mechanics."""
    with open(LUA_FILE, "r", encoding="utf-8") as f:
        lua = f.read()

    assert "function FT_SetEnabled(enable)" in lua
    assert "function FT_Toggle()" in lua
    assert "FT_DisarmSniperBuy()" in lua


def test_lua_slash_commands():
    """Verify /fts and /forevertrade slash commands with argument parsing."""
    with open(LUA_FILE, "r", encoding="utf-8") as f:
        lua = f.read()

    assert 'SLASH_FOREVERTRADESNIPER1 = "/fts"' in lua
    assert 'SLASH_FOREVERTRADESNIPER2 = "/forevertrade"' in lua
    assert 'SlashCmdList["FOREVERTRADESNIPER"]' in lua
    assert 'msg == "on" or msg == "start" or msg == "1"' in lua
    assert 'msg == "off" or msg == "stop" or msg == "0"' in lua


def test_lua_event_and_update_guards():
    """Verify OnUpdate and AUCTION_ITEM_LIST_UPDATE guard against running when disabled."""
    with open(LUA_FILE, "r", encoding="utf-8") as f:
        lua = f.read()

    assert "if not FT_IsEnabled then return end" in lua
    assert 'event == "AUCTION_HOUSE_SHOW"' in lua
    assert 'event == "AUCTION_HOUSE_CLOSED"' in lua
    assert 'event == "AUCTION_ITEM_LIST_UPDATE"' in lua


# ===========================================================================
# 2. Addon State Machine Simulation Harness
# ===========================================================================

class WoWSniperHarness:
    """High-fidelity emulator of ForeverTrade_Sniper Lua runtime state machine."""

    def __init__(self, targets=None, min_profit=1000):
        self.ADDON_NAME = "ForeverTrade_Sniper"
        self.VERSION = "2.1"
        self.FT_IsEnabled = False
        self.FT_TargetPage = 0
        self.FT_CheckPrevPageNext = False
        self.FT_IsArmed = False
        self.FT_AwaitingResults = False
        self.FT_SeenCache = {}
        self.FT_SeenOrder = []
        self.MAX_SEEN_CACHE = 200

        self.timeSinceLastQuery = 0.0
        self.armedTimer = 0.0

        self.in_combat = False
        self.pending_clear_bindings = False
        self.auction_frame_shown = True
        self.can_send_query = True

        self.btn_text = ""
        self.has_toggle_btn = False
        self.override_bindings = {}

        self.chat_log = []
        self.queries_sent = []
        self.bids_placed = []

        self.config_targets = targets or {}
        self.config_min_profit = min_profit
        self.custom_targets = {}
        self.auction_lots = []

    def has_custom_selection(self):
        if not self.custom_targets:
            return False
        for cfg in self.custom_targets.values():
            if isinstance(cfg, dict) and (cfg.get("enabled") is not None or (cfg.get("customPrice") and cfg.get("customPrice") > 0)):
                return True
        return False

    def is_item_active(self, item_id):
        if not item_id:
            return False
        num_id = int(item_id) if str(item_id).isdigit() else item_id
        cfg = self.custom_targets.get(item_id) or self.custom_targets.get(num_id)
        if cfg and isinstance(cfg, dict):
            if cfg.get("enabled") is not None:
                return bool(cfg["enabled"])
            if cfg.get("customPrice") and cfg["customPrice"] > 0:
                return True
        if self.has_custom_selection():
            return False
        return True

    def get_effective_target_price(self, item_id, default_target):
        if isinstance(default_target, dict):
            default_target = default_target.get("target", 0)
        if not item_id:
            return default_target
        num_id = int(item_id) if str(item_id).isdigit() else item_id
        cfg = self.custom_targets.get(item_id) or self.custom_targets.get(num_id)
        if cfg and isinstance(cfg, dict) and cfg.get("customPrice") and cfg["customPrice"] > 0:
            return cfg["customPrice"]
        return default_target

    def print_msg(self, msg):
        self.chat_log.append(msg)

    def add_seen(self, hash_val):
        if hash_val not in self.FT_SeenCache:
            self.FT_SeenCache[hash_val] = True
            self.FT_SeenOrder.append(hash_val)
            if len(self.FT_SeenOrder) > self.MAX_SEEN_CACHE:
                oldest = self.FT_SeenOrder.pop(0)
                if oldest in self.FT_SeenCache:
                    del self.FT_SeenCache[oldest]

    def update_toggle_button(self):
        if not self.has_toggle_btn:
            return
        if self.FT_IsEnabled:
            self.btn_text = "|cff00ff00[Sniper: ON]|r"
        else:
            self.btn_text = "|cffff4444[Sniper: OFF]|r"

    def disarm_sniper_buy(self):
        self.FT_IsArmed = False
        self.armedTimer = 0.0
        if self.in_combat:
            self.pending_clear_bindings = True
        else:
            self.override_bindings.clear()
            self.pending_clear_bindings = False

    def arm_sniper_buy(self, index, item_id, buyout, count=1, unit_target=None, hash_val=None):
        if isinstance(count, str) and unit_target is None and hash_val is None:
            hash_val = count
            count = 1
            unit_target = buyout
        count = count or 1
        unit_target = unit_target if unit_target is not None else buyout

        self.FT_IsArmed = True
        self.armedTimer = 0.0
        target_tuple = ("FTSniperBuyBtn", index, item_id, buyout, count, unit_target, hash_val)
        self.override_bindings["MOUSEWHEELUP"] = target_tuple
        self.override_bindings["MOUSEWHEELDOWN"] = target_tuple

    def execute_verified_buy(self, index, expected_item_id, expected_buyout, expected_count=1, unit_target=None, hash_val=None):
        if isinstance(expected_count, str) and unit_target is None and hash_val is None:
            hash_val = expected_count
            expected_count = 1
            unit_target = expected_buyout
        expected_count = expected_count or 1
        unit_target = unit_target if unit_target is not None else expected_buyout

        lot = next((l for l in self.auction_lots if l.get("index") == index), None)
        actual_item_id = lot.get("item_id") if lot else None
        actual_buyout = lot.get("buyout", 0) if lot else 0
        count = lot.get("count", 1) if lot else 1

        if (
            actual_item_id == expected_item_id
            and actual_buyout == expected_buyout
            and count == expected_count
            and (actual_buyout / count) <= unit_target
            and actual_buyout > 0
        ):
            self.bids_placed.append({
                "index": index,
                "item_id": expected_item_id,
                "buyout": expected_buyout,
                "count": count,
            })
            self.print_msg(f"Buying item {expected_item_id} (x{count}) for {expected_buyout} copper!")
            if hash_val and hash_val in self.FT_SeenCache:
                del self.FT_SeenCache[hash_val]
        else:
            self.print_msg(
                f"Verification failed: lot altered or shifted (ID: {actual_item_id} vs {expected_item_id}, "
                f"Buyout: {actual_buyout} vs {expected_buyout}, Count: {count} vs {expected_count}). Buy cancelled."
            )
            self.disarm_sniper_buy()
            return

        self.disarm_sniper_buy()

    def scan_current_page(self):
        batch_count = len(self.auction_lots)
        if batch_count <= 0:
            return

        min_profit = self.config_min_profit
        targets = self.config_targets
        best_candidate = None

        for lot in self.auction_lots:
            idx = lot["index"]
            count = lot.get("count", 1)
            min_bid = lot.get("min_bid", 0)
            buyout = lot.get("buyout", 0)
            item_id = lot.get("item_id")

            if not self.is_item_active(item_id):
                continue

            raw_target = targets.get(item_id) if item_id else None
            default_target = raw_target.get("target", 0) if isinstance(raw_target, dict) else raw_target
            unit_target = self.get_effective_target_price(item_id, default_target)

            if unit_target and buyout > 0 and count > 0:
                unit_price = buyout / count
                total_saved = (unit_target - unit_price) * count
                if unit_price <= unit_target and (min_profit <= 0 or total_saved >= min_profit):
                    hash_val = f"{item_id}_{count}_{min_bid}_{buyout}"
                    if hash_val not in self.FT_SeenCache:
                        if not best_candidate or total_saved > best_candidate["total_saved"]:
                            best_candidate = {
                                "index": idx,
                                "item_id": item_id,
                                "buyout": buyout,
                                "hash": hash_val,
                                "total_saved": total_saved,
                                "count": count,
                                "unit_price": unit_price,
                                "unit_target": unit_target,
                            }

        if best_candidate:
            self.arm_sniper_buy(
                best_candidate["index"],
                best_candidate["item_id"],
                best_candidate["buyout"],
                best_candidate["count"],
                best_candidate["unit_target"],
                best_candidate["hash"],
            )
            self.add_seen(best_candidate["hash"])
            self.print_msg(f"SNIPE ALERT: item {best_candidate['item_id']} saved {best_candidate['total_saved']}")

    def set_enabled(self, enable):
        self.FT_IsEnabled = bool(enable)
        self.update_toggle_button()
        if not enable:
            self.disarm_sniper_buy()
            self.FT_AwaitingResults = False
            self.print_msg("Sniper PAUSED. Safe manual AH browsing.")
        else:
            self.FT_TargetPage = 0
            self.FT_CheckPrevPageNext = False
            self.timeSinceLastQuery = 1.2
            self.print_msg("Sniper ACTIVE. Watching last AH page.")

    def toggle(self):
        self.set_enabled(not self.FT_IsEnabled)

    def create_toggle_button(self):
        if not self.has_toggle_btn and self.auction_frame_shown:
            self.has_toggle_btn = True
            self.update_toggle_button()

    def slash_cmd(self, msg):
        msg = (msg or "").lower().strip()
        if msg in ("on", "start", "1"):
            self.set_enabled(True)
        elif msg in ("off", "stop", "0"):
            self.set_enabled(False)
        elif msg in ("help", "?"):
            self.print_msg("Commands: /fts on|off, status, profit <copper>, test, reset, help")
        else:
            self.toggle()

    def on_update(self, elapsed):
        if not self.auction_frame_shown:
            return

        if not self.FT_IsEnabled:
            return

        if self.FT_IsArmed:
            self.armedTimer += elapsed
            if self.armedTimer >= 4.0:
                self.disarm_sniper_buy()
            return

        self.timeSinceLastQuery += elapsed
        if self.FT_AwaitingResults and self.timeSinceLastQuery < 3.0:
            return

        if self.timeSinceLastQuery >= 1.2 and self.can_send_query:
            self.timeSinceLastQuery = 0.0
            self.armedTimer = 0.0
            self.FT_AwaitingResults = True
            self.queries_sent.append(self.FT_TargetPage)

    def on_event(self, event, *args):
        if event == "AUCTION_HOUSE_SHOW":
            self.create_toggle_button()
            self.set_enabled(False)
            self.disarm_sniper_buy()
        elif event == "AUCTION_HOUSE_CLOSED":
            self.set_enabled(False)
            self.disarm_sniper_buy()
            self.FT_AwaitingResults = False
        elif event == "AUCTION_ITEM_LIST_UPDATE":
            if not self.FT_IsEnabled:
                return
            self.FT_AwaitingResults = False
            if not self.FT_IsArmed:
                self.scan_current_page()
        elif event == "PLAYER_REGEN_DISABLED":
            if self.FT_IsArmed:
                self.disarm_sniper_buy()
        elif event == "PLAYER_REGEN_ENABLED":
            if self.pending_clear_bindings or not self.FT_IsArmed:
                self.override_bindings.clear()
                self.pending_clear_bindings = False


# ===========================================================================
# 3. Behavioral Unit Tests
# ===========================================================================

def test_harness_default_state_is_disabled():
    """Verify initial state: FT_IsEnabled is False, not armed, button text shows OFF."""
    harness = WoWSniperHarness()
    assert harness.FT_IsEnabled is False
    assert harness.FT_IsArmed is False
    assert harness.FT_AwaitingResults is False

    harness.create_toggle_button()
    assert harness.btn_text == "|cffff4444[Sniper: OFF]|r"


def test_harness_toggle_button_visual_states():
    """Verify toggle button flips between green [ON] and red [OFF]."""
    harness = WoWSniperHarness()
    harness.create_toggle_button()
    assert harness.btn_text == "|cffff4444[Sniper: OFF]|r"

    harness.set_enabled(True)
    assert harness.btn_text == "|cff00ff00[Sniper: ON]|r"

    harness.set_enabled(False)
    assert harness.btn_text == "|cffff4444[Sniper: OFF]|r"


def test_harness_set_enabled_state_transitions():
    """Verify enable/disable updates internal query cooldown and cancels pending states."""
    harness = WoWSniperHarness()
    harness.FT_AwaitingResults = True
    harness.arm_sniper_buy(1, 101, 500, "101_1_0_500")
    assert harness.FT_IsArmed is True

    harness.set_enabled(False)
    assert harness.FT_IsEnabled is False
    assert harness.FT_IsArmed is False
    assert harness.FT_AwaitingResults is False
    assert "Sniper PAUSED" in harness.chat_log[-1]

    harness.set_enabled(True)
    assert harness.FT_IsEnabled is True
    assert harness.timeSinceLastQuery == 1.2
    assert "Sniper ACTIVE" in harness.chat_log[-1]


def test_harness_toggle_flips_state():
    """Verify FT_Toggle alternates states cleanly."""
    harness = WoWSniperHarness()
    assert harness.FT_IsEnabled is False

    harness.toggle()
    assert harness.FT_IsEnabled is True

    harness.toggle()
    assert harness.FT_IsEnabled is False


def test_harness_slash_command_matrix():
    """Verify full matrix of slash command arguments for /fts and /forevertrade."""
    harness = WoWSniperHarness()

    for cmd in ("on", "start", "1", "ON", "  start  ", "  1 "):
        harness.set_enabled(False)
        harness.slash_cmd(cmd)
        assert harness.FT_IsEnabled is True, f"Failed for cmd: {cmd}"

    for cmd in ("off", "stop", "0", "OFF", "  stop  ", " 0 "):
        harness.set_enabled(True)
        harness.slash_cmd(cmd)
        assert harness.FT_IsEnabled is False, f"Failed for cmd: {cmd}"

    harness.set_enabled(False)
    harness.slash_cmd("")
    assert harness.FT_IsEnabled is True

    harness.slash_cmd(None)
    assert harness.FT_IsEnabled is False

    harness.slash_cmd("toggle")
    assert harness.FT_IsEnabled is True


def test_harness_auction_house_show_resets_disabled_and_disarms():
    """Verify opening AH automatically forces sniper OFF and disarms bindings."""
    harness = WoWSniperHarness()
    harness.set_enabled(True)
    harness.arm_sniper_buy(1, 201, 1000, "201_1_0_1000")
    assert harness.FT_IsEnabled is True
    assert harness.FT_IsArmed is True

    harness.on_event("AUCTION_HOUSE_SHOW")
    assert harness.FT_IsEnabled is False
    assert harness.FT_IsArmed is False
    assert harness.has_toggle_btn is True
    assert harness.btn_text == "|cffff4444[Sniper: OFF]|r"


def test_harness_auction_house_closed_resets_disabled_and_disarms():
    """Verify closing AH disables sniper and disarms bindings."""
    harness = WoWSniperHarness()
    harness.set_enabled(True)
    harness.FT_AwaitingResults = True
    harness.arm_sniper_buy(1, 202, 1200, "202_1_0_1200")

    harness.on_event("AUCTION_HOUSE_CLOSED")
    assert harness.FT_IsEnabled is False
    assert harness.FT_IsArmed is False
    assert harness.FT_AwaitingResults is False


def test_harness_onupdate_disabled_suppresses_queries():
    """Verify OnUpdate never sends queries or accumulates timers when FT_IsEnabled is False."""
    harness = WoWSniperHarness()
    harness.set_enabled(False)

    harness.on_update(1.5)
    harness.on_update(2.0)
    assert len(harness.queries_sent) == 0


def test_harness_onupdate_enabled_rate_limits_and_queries():
    """Verify OnUpdate sends query at >= 1.2s intervals when enabled and AH ready."""
    harness = WoWSniperHarness()
    harness.set_enabled(True)

    harness.on_update(0.1)
    assert len(harness.queries_sent) == 1
    assert harness.FT_AwaitingResults is True

    harness.on_update(0.5)
    assert len(harness.queries_sent) == 1

    harness.FT_AwaitingResults = False
    harness.on_update(0.5)
    assert len(harness.queries_sent) == 1

    harness.on_update(0.8)
    assert len(harness.queries_sent) == 2


def test_harness_onupdate_armed_timeout_disarms():
    """Verify armed buy automatically disarms after 4.0 seconds if not clicked."""
    harness = WoWSniperHarness()
    harness.set_enabled(True)
    harness.arm_sniper_buy(1, 301, 800, "301_1_0_800")
    assert harness.FT_IsArmed is True

    harness.on_update(2.0)
    assert harness.FT_IsArmed is True

    harness.on_update(2.1)
    assert harness.FT_IsArmed is False
    assert len(harness.override_bindings) == 0


def test_harness_auction_item_list_update_ignored_when_disabled():
    """Verify external auction list updates are ignored while disabled (safe manual browsing)."""
    harness = WoWSniperHarness(targets={401: 5000}, min_profit=500)
    harness.set_enabled(False)
    harness.auction_lots = [{"index": 1, "item_id": 401, "buyout": 1000, "count": 1, "min_bid": 100}]

    harness.on_event("AUCTION_ITEM_LIST_UPDATE")
    assert harness.FT_IsArmed is False
    assert len(harness.bids_placed) == 0


def test_harness_auction_item_list_update_active_when_enabled():
    """Verify auction updates trigger scanning and hardware arming when enabled."""
    harness = WoWSniperHarness(targets={401: 5000}, min_profit=500)
    harness.set_enabled(True)
    harness.auction_lots = [{"index": 1, "item_id": 401, "buyout": 1000, "count": 1, "min_bid": 100}]

    harness.on_event("AUCTION_ITEM_LIST_UPDATE")
    assert harness.FT_IsArmed is True
    assert "MOUSEWHEELUP" in harness.override_bindings


def test_harness_execute_verified_buy_success():
    """Verify confirmed purchase places bid and consumes seen cache entry."""
    harness = WoWSniperHarness()
    hash_val = "501_1_100_1500"
    harness.add_seen(hash_val)
    harness.auction_lots = [{"index": 2, "item_id": 501, "buyout": 1500, "count": 1}]

    harness.execute_verified_buy(index=2, expected_item_id=501, expected_buyout=1500, hash_val=hash_val)
    assert len(harness.bids_placed) == 1
    assert harness.bids_placed[0]["item_id"] == 501
    assert harness.bids_placed[0]["buyout"] == 1500
    assert hash_val not in harness.FT_SeenCache
    assert harness.FT_IsArmed is False


def test_harness_execute_verified_buy_aborts_on_item_or_price_mismatch():
    """Verify anti-cheat/anti-snipe-trap: mismatched item ID or shifted buyout aborts bid."""
    harness = WoWSniperHarness()
    harness.auction_lots = [{"index": 3, "item_id": 999, "buyout": 10000, "count": 1}]

    harness.execute_verified_buy(index=3, expected_item_id=501, expected_buyout=1500, hash_val="hash1")
    assert len(harness.bids_placed) == 0
    assert harness.FT_IsArmed is False

    harness.auction_lots = [{"index": 3, "item_id": 501, "buyout": 50000, "count": 1}]
    harness.execute_verified_buy(index=3, expected_item_id=501, expected_buyout=1500, hash_val="hash2")
    assert len(harness.bids_placed) == 0
    assert harness.FT_IsArmed is False


def test_harness_ring_buffer_eviction_at_200():
    """Verify ring buffer caps at 200 entries and drops oldest."""
    harness = WoWSniperHarness()
    for i in range(250):
        harness.add_seen(f"hash_{i}")

    assert len(harness.FT_SeenCache) == 200
    assert len(harness.FT_SeenOrder) == 200
    assert "hash_0" not in harness.FT_SeenCache
    assert "hash_49" not in harness.FT_SeenCache
    assert "hash_50" in harness.FT_SeenCache
    assert "hash_249" in harness.FT_SeenCache


def test_harness_combat_lockdown_prevents_clear_bindings():
    """Verify disarm does not call ClearOverrideBindings during combat (avoids WoW Lua taint)."""
    harness = WoWSniperHarness()
    harness.arm_sniper_buy(1, 601, 1000, "h")
    assert len(harness.override_bindings) > 0

    harness.in_combat = True
    harness.disarm_sniper_buy()
    assert harness.FT_IsArmed is False
    assert len(harness.override_bindings) > 0

    harness.in_combat = False
    harness.disarm_sniper_buy()
    assert len(harness.override_bindings) == 0


def test_harness_slot_shift_protection_rejects_altered_stack_or_price():
    """Verify slot shift protection: altered count or buyout fails verification and disarms."""
    harness = WoWSniperHarness(targets={701: 500}, min_profit=0)
    harness.auction_lots = [{"index": 1, "item_id": 701, "buyout": 2000, "count": 5}]

    harness.arm_sniper_buy(
        index=1,
        item_id=701,
        buyout=2000,
        count=5,
        unit_target=500,
        hash_val="701_5_0_2000",
    )
    assert harness.FT_IsArmed is True

    # Shift lot at slot 1 to count=1 for same total buyout (unit price 2000 > target 500)
    harness.auction_lots = [{"index": 1, "item_id": 701, "buyout": 2000, "count": 1}]

    harness.execute_verified_buy(
        index=1,
        expected_item_id=701,
        expected_buyout=2000,
        expected_count=5,
        unit_target=500,
        hash_val="701_5_0_2000",
    )

    assert len(harness.bids_placed) == 0
    assert harness.FT_IsArmed is False
    assert len(harness.override_bindings) == 0
    assert any("Verification failed" in msg for msg in harness.chat_log)


def test_harness_combat_lockdown_deferred_cleanup_on_regen_enabled():
    """Verify combat lockdown defers ClearOverrideBindings until PLAYER_REGEN_ENABLED."""
    harness = WoWSniperHarness(targets={801: 1000}, min_profit=0)
    harness.arm_sniper_buy(index=1, item_id=801, buyout=500, count=1, unit_target=1000, hash_val="801_1_0_500")
    assert harness.FT_IsArmed is True
    assert len(harness.override_bindings) > 0

    # Enter combat
    harness.in_combat = True
    harness.on_event("PLAYER_REGEN_DISABLED")
    assert harness.FT_IsArmed is False
    assert harness.pending_clear_bindings is True
    assert len(harness.override_bindings) > 0

    harness.disarm_sniper_buy()
    assert harness.pending_clear_bindings is True
    assert len(harness.override_bindings) > 0

    # Leave combat
    harness.in_combat = False
    harness.on_event("PLAYER_REGEN_ENABLED")
    assert harness.pending_clear_bindings is False
    assert len(harness.override_bindings) == 0


def test_harness_zero_min_profit_triggers_on_target_ceiling():
    """Verify that with min_profit=0, an item where unit_price == unit_target is picked and armed."""
    harness = WoWSniperHarness(targets={901: 500}, min_profit=0)
    harness.set_enabled(True)

    harness.auction_lots = [{"index": 1, "item_id": 901, "buyout": 500, "count": 1, "min_bid": 100}]
    harness.on_event("AUCTION_ITEM_LIST_UPDATE")

    assert harness.FT_IsArmed is True
    assert "MOUSEWHEELUP" in harness.override_bindings
    binding = harness.override_bindings["MOUSEWHEELUP"]
    assert binding[1] == 1
    assert binding[2] == 901
    assert binding[3] == 500
    assert binding[4] == 1
    assert binding[5] == 500


def test_harness_slash_help_does_not_toggle_sniper():
    """Verify /fts help and /fts ? display help message without toggling FT_IsEnabled."""
    harness = WoWSniperHarness()
    assert harness.FT_IsEnabled is False

    harness.slash_cmd("help")
    assert harness.FT_IsEnabled is False
    assert any("help" in msg.lower() or "commands" in msg.lower() for msg in harness.chat_log)

    harness.slash_cmd("?")
    assert harness.FT_IsEnabled is False

    harness.set_enabled(True)
    assert harness.FT_IsEnabled is True

    harness.slash_cmd("help")
    assert harness.FT_IsEnabled is True

    harness.slash_cmd("?")
    assert harness.FT_IsEnabled is True


def test_lua_commodity_not_blocked_by_test_mode():
    """Verify that test simulation strictly checks hash, never index == 0, allowing commodities."""
    with open(LUA_FILE, "r", encoding="utf-8") as f:
        lua = f.read()

    assert 'if hash == "test_hash" or hash == "test_simulation_hash" then' in lua
    assert "if index == 0 then" not in lua


def test_lua_item_display_name_and_link_formatting():
    """Verify FT_GetItemDisplayName resolves localized clickable links with quality and fallback."""
    with open(LUA_FILE, "r", encoding="utf-8") as f:
        lua = f.read()

    assert "function FT_GetItemDisplayName(itemId, fallbackLink)" in lua
    assert "GetItemInfo(itemId)" in lua
    assert "ITEM_QUALITY_COLORS" in lua
    assert "FT_SNIPER_CONFIG.names" in lua
    assert "FT_GetItemDisplayName(bestCandidate.itemId, bestCandidate.link)" in lua
    assert 'link = string.format("item:%d", itemId)' not in lua


def parse_money_input(text):
    """Helper mirroring FT_ParseMoneyInput Lua logic."""
    if not text:
        return 0
    text = str(text).lower().replace(" ", "").replace(",", ".")
    if not text:
        return 0
    if re.search(r"[gзsсмc]", text):
        gold_m = re.search(r"([\d\.]+)[gз]", text)
        silver_m = re.search(r"([\d\.]+)[sс]", text)
        copper_m = re.search(r"([\d\.]+)[cм]", text)
        gold = float(gold_m.group(1)) if gold_m else 0.0
        silver = float(silver_m.group(1)) if silver_m else 0.0
        copper = float(copper_m.group(1)) if copper_m else 0.0
        return int(gold * 10000 + silver * 100 + copper + 0.5)
    try:
        return max(0, int(float(text)))
    except (ValueError, TypeError):
        return 0


def test_toc_saved_variables_custom_targets():
    """Verify TOC includes SavedVariables: FT_CustomTargets."""
    with open(TOC_FILE, "r", encoding="utf-8") as f:
        toc_content = f.read()
    assert "## SavedVariables: FT_CustomTargets" in toc_content


def test_harness_item_disabled_filtering():
    """Verify disabled items (enabled = False) are never sniped even at 1 copper."""
    harness = WoWSniperHarness(targets={101: 5000}, min_profit=0)
    harness.set_enabled(True)
    harness.custom_targets = {101: {"enabled": False}}

    assert harness.is_item_active(101) is False

    harness.auction_lots = [
        {"index": 1, "item_id": 101, "count": 1, "buyout": 1, "min_bid": 1}
    ]
    harness.scan_current_page()

    assert harness.FT_IsArmed is False
    assert len(harness.override_bindings) == 0


def test_harness_custom_selection_mode():
    """Verify that when custom items are selected, unselected items are ignored."""
    harness = WoWSniperHarness(targets={101: 5000, 102: 5000}, min_profit=0)
    harness.set_enabled(True)
    harness.custom_targets = {101: {"enabled": True}}

    assert harness.has_custom_selection() is True
    assert harness.is_item_active(101) is True
    assert harness.is_item_active(102) is False

    # Lot for unselected item 102 should be ignored
    harness.auction_lots = [
        {"index": 1, "item_id": 102, "count": 1, "buyout": 100, "min_bid": 100}
    ]
    harness.scan_current_page()
    assert harness.FT_IsArmed is False

    # Lot for selected item 101 should be sniped
    harness.auction_lots = [
        {"index": 2, "item_id": 101, "count": 1, "buyout": 200, "min_bid": 200}
    ]
    harness.scan_current_page()
    assert harness.FT_IsArmed is True
    assert harness.override_bindings["MOUSEWHEELUP"][1] == 2
    assert harness.override_bindings["MOUSEWHEELUP"][2] == 101


def test_harness_custom_price_override():
    """Verify customPrice takes precedence over default ceiling."""
    harness = WoWSniperHarness(targets={201: 1000}, min_profit=0)
    harness.set_enabled(True)
    harness.custom_targets = {201: {"customPrice": 2500}}

    effective_price = harness.get_effective_target_price(201, 1000)
    assert effective_price == 2500

    # Buyout 1500 > default ceiling 1000, but <= customPrice 2500
    harness.auction_lots = [
        {"index": 1, "item_id": 201, "count": 1, "buyout": 1500, "min_bid": 1500}
    ]
    harness.scan_current_page()
    assert harness.FT_IsArmed is True
    assert harness.override_bindings["MOUSEWHEELUP"][5] == 2500


def test_money_parsing_logic():
    """Verify money parsing logic for copper, silver, gold formats in both EN and RU."""
    # Plain copper integers and zero/empty
    assert parse_money_input("500") == 500
    assert parse_money_input("0") == 0
    assert parse_money_input("") == 0
    assert parse_money_input(None) == 0

    # English units: gold, silver, copper
    assert parse_money_input("10c") == 10
    assert parse_money_input("50s") == 5000
    assert parse_money_input("2g") == 20000
    assert parse_money_input("1g 50s 25c") == 15025
    assert parse_money_input("1.5g") == 15000
    assert parse_money_input("2.5s") == 250

    # Russian units: золото (з), серебро (с), медь (м)
    assert parse_money_input("50м") == 50
    assert parse_money_input("10с") == 1000
    assert parse_money_input("3з") == 30000
    assert parse_money_input("1з 20с 50м") == 12050
    assert parse_money_input("1,5з") == 15000

    # Verify Lua implementation exists in addon file
    with open(LUA_FILE, "r", encoding="utf-8") as f:
        lua = f.read()
    assert "FT_ParseMoneyInput" in lua
    assert "FT_FormatMoneyText" in lua
    assert "FT_CustomTargets" in lua
    assert "FT_HasCustomSelection" in lua
    assert "FT_IsItemActive" in lua
    assert "FT_GetEffectiveTargetPrice" in lua

