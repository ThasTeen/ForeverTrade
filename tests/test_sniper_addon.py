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
# 2. Addon State Machine Simulation Harness & Shopping List Parsers
# ===========================================================================

def clean_item_string(item_str: str) -> str:
    """Sanitize Auctionator shopping list item string.

    Mirrors FT_CleanItemString Lua logic:
    - Strips search parameters following semicolons (e.g. 'item;10;5' -> 'item').
    - Trims surrounding whitespace.
    - Strips outer matching quotes (e.g. '"Глаз мурлока"' -> 'Глаз мурлока').
    - Handles nested or escaped quotes and spaces cleanly.
    """
    if not item_str or not isinstance(item_str, str):
        return ""
    s = item_str
    if ";" in s:
        s = s.split(";", 1)[0]
    s = s.strip()
    while len(s) >= 2 and ((s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'"))):
        s = s[1:-1].strip()
    return s


def parse_auctionator_shopping_lists(raw_lists, item_resolver=None, include_temporary=False):
    """Parse AUCTIONATOR_SHOPPING_LISTS format structures.

    Mirrors FT_GetAvailableLists and FT_RebuildItemList:
    - Validates list name and items array.
    - Sanitizes every item string with clean_item_string.
    - Handles empty/nil list and malformed structures gracefully.
    - Optionally filters or includes temporary lists (isTemporary).
    - Resolves item names to item IDs when item_resolver is provided.
    """
    if not raw_lists or not isinstance(raw_lists, list):
        return []

    parsed = []
    for idx, entry in enumerate(raw_lists, 1):
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not name or not isinstance(name, str) or not name.strip():
            continue

        is_temp = bool(entry.get("isTemporary", False))
        if is_temp and not include_temporary:
            continue

        raw_items = entry.get("items")
        if not isinstance(raw_items, list):
            raw_items = []

        clean_items = []
        item_ids = []
        for it in raw_items:
            cleaned = clean_item_string(it)
            if cleaned:
                clean_items.append(cleaned)
                if item_resolver:
                    resolved_id = item_resolver(cleaned)
                    if resolved_id is not None:
                        item_ids.append(resolved_id)

        parsed.append({
            "index": idx,
            "name": name.strip(),
            "items": clean_items,
            "item_ids": item_ids,
            "isTemporary": is_temp,
        })

    return parsed


class WoWSniperHarness:
    """High-fidelity emulator of ForeverTrade_Sniper Lua runtime state machine."""

    def __init__(self, targets=None, min_profit=1000, shopping_lists=None, settings=None, item_names=None):
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

        # Shopping lists & Dual Mode extensions
        self.shopping_lists = shopping_lists or []
        self.sniper_settings = {
            "mode": "LIST",
            "minProfit": min_profit,
            "selectedList": 1,
        }
        if settings:
            self.sniper_settings.update(settings)

        self.item_names = dict(item_names) if item_names else {}
        for tid, tval in self.config_targets.items():
            if isinstance(tval, dict) and "name" in tval:
                self.item_names[tval["name"].lower()] = tid
                self.item_names[tid] = tval["name"]

        self.active_items_map = {}
        self.cached_item_list = []
        self.rebuild_item_list()

    def get_item_id_by_name(self, name):
        if not name:
            return None
        if isinstance(name, int):
            return name
        if str(name).isdigit():
            return int(name)
        clean_name = str(name).strip()
        lower_name = clean_name.lower()
        if lower_name in self.item_names:
            val = self.item_names[lower_name]
            return int(val) if str(val).isdigit() else val
        for tid, tname in self.item_names.items():
            if str(tname).lower() == lower_name:
                return int(tid) if str(tid).isdigit() else tid
        return None

    def rebuild_item_list(self):
        self.active_items_map.clear()
        self.cached_item_list.clear()

        available_lists = parse_auctionator_shopping_lists(
            self.shopping_lists,
            item_resolver=self.get_item_id_by_name,
            include_temporary=True,
        )

        if not available_lists:
            # Fallback to config_targets
            for raw_id, tval in self.config_targets.items():
                item_id = int(raw_id) if str(raw_id).isdigit() else raw_id
                def_target = tval.get("target", 0) if isinstance(tval, dict) else tval
                name = tval.get("name", f"Item {item_id}") if isinstance(tval, dict) else self.item_names.get(item_id, f"Item {item_id}")
                self.cached_item_list.append({
                    "id": item_id,
                    "defaultTarget": def_target,
                    "name": name,
                })
                self.active_items_map[item_id] = True
        else:
            sel = self.sniper_settings.get("selectedList", 1)
            raw_items_to_process = []
            if sel == 0 or sel > len(available_lists):
                # "Все списки": merge all
                for l in available_lists:
                    raw_items_to_process.extend(l.get("items", []))
            else:
                l = available_lists[sel - 1]
                raw_items_to_process.extend(l.get("items", []))

            seen_ids = set()
            for raw_it in raw_items_to_process:
                clean = clean_item_string(raw_it)
                if clean:
                    item_id = self.get_item_id_by_name(clean)
                    if item_id is not None and item_id not in seen_ids:
                        seen_ids.add(item_id)
                        self.active_items_map[item_id] = True
                        raw_target = self.config_targets.get(item_id, 0)
                        def_target = raw_target.get("target", 0) if isinstance(raw_target, dict) else raw_target
                        name = (raw_target.get("name") if isinstance(raw_target, dict) else None) or self.item_names.get(item_id) or clean
                        self.cached_item_list.append({
                            "id": item_id,
                            "defaultTarget": def_target,
                            "name": name,
                        })

    def get_effective_min_profit(self):
        if self.sniper_settings and "minProfit" in self.sniper_settings:
            return self.sniper_settings["minProfit"]
        return self.config_min_profit

    def save_settings(self):
        """Export persistent settings dictionary (SavedVariables simulation)."""
        import copy
        return {
            "FT_SniperSettings": copy.deepcopy(self.sniper_settings),
            "FT_CustomTargets": copy.deepcopy(self.custom_targets),
        }

    def load_settings(self, saved_data):
        """Import persistent settings dictionary (SavedVariables simulation)."""
        import copy
        if not saved_data or not isinstance(saved_data, dict):
            return
        if "FT_SniperSettings" in saved_data and isinstance(saved_data["FT_SniperSettings"], dict):
            self.sniper_settings.update(copy.deepcopy(saved_data["FT_SniperSettings"]))
        if "FT_CustomTargets" in saved_data and isinstance(saved_data["FT_CustomTargets"], dict):
            self.custom_targets = copy.deepcopy(saved_data["FT_CustomTargets"])
        self.rebuild_item_list()

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

        mode = self.sniper_settings.get("mode", "LIST")
        is_list_mode = (mode == "LIST")

        if is_list_mode and not self.active_items_map:
            self.rebuild_item_list()

        min_profit = self.get_effective_min_profit()
        targets = self.config_targets
        best_candidate = None

        for lot in self.auction_lots:
            idx = lot["index"]
            count = lot.get("count", 1)
            min_bid = lot.get("min_bid", 0)
            buyout = lot.get("buyout", 0)
            item_id = lot.get("item_id")

            if not item_id or buyout <= 0 or count <= 0:
                continue

            should_check = False
            if is_list_mode:
                should_check = (item_id in self.active_items_map) and self.is_item_active(item_id)
            else:
                should_check = True

            if not should_check:
                continue

            raw_target = targets.get(item_id) if item_id else None
            default_target = raw_target.get("target", 0) if isinstance(raw_target, dict) else (raw_target or 0)
            unit_target = self.get_effective_target_price(item_id, default_target)

            if unit_target and unit_target > 0:
                unit_price = buyout / count
                total_saved = (unit_target - unit_price) * count

                matches = False
                if is_list_mode:
                    matches = (unit_price <= unit_target)
                else:
                    matches = (unit_price <= unit_target) and (min_profit <= 0 or total_saved >= min_profit)

                if matches:
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


# ===========================================================================
# 4. Shopping List Sanitization & Parsing Tests
# ===========================================================================

def test_clean_item_string_quotes():
    """Verify clean_item_string strips matching single, double, escaped, or nested outer quotes."""
    # Double quotes
    assert clean_item_string('"Глаз мурлока"') == "Глаз мурлока"
    assert clean_item_string('""Глаз мурлока""') == "Глаз мурлока"
    assert clean_item_string('"\"Глаз мурлока\""') == "Глаз мурлока"

    # Single quotes
    assert clean_item_string("'Громоцвет'") == "Громоцвет"
    assert clean_item_string("''Громоцвет''") == "Громоцвет"

    # Quotes with leading/trailing spaces
    assert clean_item_string('  "Черный лотос"  ') == "Черный лотос"
    assert clean_item_string(' "  Призрачная крапива  " ') == "Призрачная крапива"

    # Already clean / unquoted
    assert clean_item_string("Мифриловая руда") == "Мифриловая руда"
    assert clean_item_string("  Слиток железа  ") == "Слиток железа"


def test_clean_item_string_semicolons_and_search_params():
    """Verify clean_item_string strips Auctionator search parameters following semicolons."""
    # Semicolons with quantities, exact match, or levels
    assert clean_item_string("Глаз мурлока;10;5") == "Глаз мурлока"
    assert clean_item_string('"Глаз мурлока";10') == "Глаз мурлока"
    assert clean_item_string('"\"Глаз мурлока\"";exact;1') == "Глаз мурлока"
    assert clean_item_string("Снолист;exact") == "Снолист"
    assert clean_item_string("Призрачная крапива;") == "Призрачная крапива"
    assert clean_item_string("  Магическая ткань; 20 ; 40  ") == "Магическая ткань"

    # Semicolons only
    assert clean_item_string(";;;;") == ""
    assert clean_item_string(" ; ; ") == ""


def test_clean_item_string_whitespace_and_invalid_types():
    """Verify clean_item_string handles whitespace, empty strings, None, and non-string types safely."""
    assert clean_item_string("") == ""
    assert clean_item_string("   ") == ""
    assert clean_item_string(None) == ""
    assert clean_item_string(12345) == ""
    assert clean_item_string(["not", "a", "string"]) == ""
    assert clean_item_string({"name": "test"}) == ""


def test_parse_auctionator_shopping_lists_structure():
    """Verify parse_auctionator_shopping_lists correctly extracts names, items, and flags."""
    raw_lists = [
        {
            "name": "Алхимия (Alchemy)",
            "items": ['"Громоцвет"', "Снолист;10;1", '"\"Призрачная крапива\""'],
            "isTemporary": False,
        },
        {
            "name": "Инженерия",
            "items": ["Мифриловая руда", "  Ториевая руда; 20  "],
            "isTemporary": False,
        },
        {
            "name": "Временный поиск",
            "items": ['"Черный лотос"'],
            "isTemporary": True,
        },
    ]

    # Without temporary lists
    parsed = parse_auctionator_shopping_lists(raw_lists, include_temporary=False)
    assert len(parsed) == 2
    assert parsed[0]["name"] == "Алхимия (Alchemy)"
    assert parsed[0]["items"] == ["Громоцвет", "Снолист", "Призрачная крапива"]
    assert parsed[0]["isTemporary"] is False
    assert parsed[1]["name"] == "Инженерия"
    assert parsed[1]["items"] == ["Мифриловая руда", "Ториевая руда"]

    # With temporary lists
    parsed_with_temp = parse_auctionator_shopping_lists(raw_lists, include_temporary=True)
    assert len(parsed_with_temp) == 3
    assert parsed_with_temp[2]["name"] == "Временный поиск"
    assert parsed_with_temp[2]["items"] == ["Черный лотос"]
    assert parsed_with_temp[2]["isTemporary"] is True


def test_parse_auctionator_shopping_lists_empty_and_nil_fallback():
    """Verify parse_auctionator_shopping_lists handles empty, nil, and malformed inputs gracefully."""
    assert parse_auctionator_shopping_lists(None) == []
    assert parse_auctionator_shopping_lists([]) == []
    assert parse_auctionator_shopping_lists("not_a_list") == []
    assert parse_auctionator_shopping_lists(123) == []

    # Malformed elements inside list
    malformed = [
        None,
        123,
        "string_instead_of_dict",
        {},  # missing name
        {"name": ""},  # empty name
        {"name": "   "},  # whitespace name
        {"name": "Валидный список", "items": None},  # None items
        {"name": "Список без ключа items"},  # missing items key
    ]
    parsed = parse_auctionator_shopping_lists(malformed)
    assert len(parsed) == 2
    assert parsed[0]["name"] == "Валидный список"
    assert parsed[0]["items"] == []
    assert parsed[1]["name"] == "Список без ключа items"
    assert parsed[1]["items"] == []


def test_shopping_list_rebuild_fallback_when_no_lists():
    """Verify rebuild_item_list falls back to FT_SNIPER_CONFIG.targets when shopping lists are empty."""
    targets = {
        1001: {"name": "Громоцвет", "target": 5000},
        1002: {"name": "Черный лотос", "target": 80000},
    }
    harness = WoWSniperHarness(targets=targets, shopping_lists=[])
    assert 1001 in harness.active_items_map
    assert 1002 in harness.active_items_map
    assert len(harness.cached_item_list) == 2


def test_shopping_list_item_resolution_and_deduplication():
    """Verify shopping list items are resolved to numeric IDs and deduplicated across entries."""
    targets = {
        501: {"name": "Громоцвет", "target": 4000},
        502: {"name": "Снолист", "target": 6000},
    }
    raw_lists = [
        {
            "name": "Сбор трав",
            "items": [
                '"Громоцвет"',
                "Громоцвет;20",  # Duplicate with params
                "Снолист",
                "501",  # Numeric ID string
                "Неизвестный предмет",
            ],
        }
    ]

    harness = WoWSniperHarness(targets=targets, shopping_lists=raw_lists)
    # Should resolve 501 and 502, ignoring duplicate and unknown
    assert 501 in harness.active_items_map
    assert 502 in harness.active_items_map
    assert len(harness.cached_item_list) == 2
    assert {it["id"] for it in harness.cached_item_list} == {501, 502}


# ===========================================================================
# 5. Dual Sniper Modes Tests (LIST vs ALL)
# ===========================================================================

def test_mode_list_active_item_below_or_equal_ceiling_triggers():
    """Verify Mode LIST triggers purchase for active item in list with buyout <= ceiling."""
    targets = {101: {"name": "Громоцвет", "target": 500}}
    lists = [{"name": "Алхимия", "items": ["Громоцвет"]}]
    harness = WoWSniperHarness(targets=targets, shopping_lists=lists, settings={"mode": "LIST"})
    harness.set_enabled(True)

    # Buyout 400 <= ceiling 500 -> triggers
    harness.auction_lots = [
        {"index": 1, "item_id": 101, "count": 1, "buyout": 400, "min_bid": 100}
    ]
    harness.scan_current_page()

    assert harness.FT_IsArmed is True
    assert "MOUSEWHEELUP" in harness.override_bindings
    binding = harness.override_bindings["MOUSEWHEELUP"]
    assert binding[1] == 1  # index
    assert binding[2] == 101  # item_id
    assert binding[3] == 400  # buyout
    assert binding[5] == 500  # unitTarget


def test_mode_list_active_item_above_ceiling_rejected():
    """Verify Mode LIST rejects active item in list when buyout > ceiling."""
    targets = {101: {"name": "Громоцвет", "target": 500}}
    lists = [{"name": "Алхимия", "items": ["Громоцвет"]}]
    harness = WoWSniperHarness(targets=targets, shopping_lists=lists, settings={"mode": "LIST"})
    harness.set_enabled(True)

    # Buyout 600 > ceiling 500 -> rejected
    harness.auction_lots = [
        {"index": 1, "item_id": 101, "count": 1, "buyout": 600, "min_bid": 100}
    ]
    harness.scan_current_page()

    assert harness.FT_IsArmed is False
    assert len(harness.override_bindings) == 0


def test_mode_list_inactive_item_checkbox_unchecked_rejected():
    """Verify Mode LIST rejects item in shopping list if user unchecked it (enabled=False)."""
    targets = {101: {"name": "Громоцвет", "target": 500}}
    lists = [{"name": "Алхимия", "items": ["Громоцвет"]}]
    harness = WoWSniperHarness(targets=targets, shopping_lists=lists, settings={"mode": "LIST"})
    harness.set_enabled(True)
    # Checkbox unchecked
    harness.custom_targets = {101: {"enabled": False}}

    assert harness.is_item_active(101) is False

    # Buyout 10 copper (deep discount), but unchecked -> rejected
    harness.auction_lots = [
        {"index": 1, "item_id": 101, "count": 1, "buyout": 10, "min_bid": 1}
    ]
    harness.scan_current_page()

    assert harness.FT_IsArmed is False
    assert len(harness.override_bindings) == 0


def test_mode_list_item_not_in_shopping_list_rejected():
    """Verify Mode LIST rejects lots for items that are not in the selected shopping list."""
    targets = {
        101: {"name": "Громоцвет", "target": 500},
        999: {"name": "Черный лотос", "target": 100000},
    }
    # Shopping list contains only 101
    lists = [{"name": "Травы 1-100", "items": ["Громоцвет"]}]
    harness = WoWSniperHarness(targets=targets, shopping_lists=lists, settings={"mode": "LIST"})
    harness.set_enabled(True)

    assert 101 in harness.active_items_map
    assert 999 not in harness.active_items_map

    # Lot 999 with 1000 copper buyout (profit 99000!) should be ignored in LIST mode
    harness.auction_lots = [
        {"index": 1, "item_id": 999, "count": 1, "buyout": 1000, "min_bid": 100}
    ]
    harness.scan_current_page()

    assert harness.FT_IsArmed is False
    assert len(harness.override_bindings) == 0


def test_mode_list_multi_list_selection_and_all_lists_mode():
    """Verify switching selectedList filters items or merges all lists (selectedList=0)."""
    targets = {
        101: {"name": "Громоцвет", "target": 500},
        202: {"name": "Магическая ткань", "target": 800},
    }
    lists = [
        {"name": "Список 1", "items": ["Громоцвет"]},
        {"name": "Список 2", "items": ["Магическая ткань"]},
    ]

    # selectedList = 1 -> only 101 active
    harness = WoWSniperHarness(targets=targets, shopping_lists=lists, settings={"mode": "LIST", "selectedList": 1})
    harness.set_enabled(True)
    assert 101 in harness.active_items_map
    assert 202 not in harness.active_items_map

    harness.auction_lots = [{"index": 1, "item_id": 202, "count": 1, "buyout": 200, "min_bid": 100}]
    harness.scan_current_page()
    assert harness.FT_IsArmed is False  # 202 not in list 1

    # selectedList = 2 -> only 202 active
    harness.sniper_settings["selectedList"] = 2
    harness.rebuild_item_list()
    assert 101 not in harness.active_items_map
    assert 202 in harness.active_items_map

    harness.scan_current_page()
    assert harness.FT_IsArmed is True  # 202 armed
    assert harness.override_bindings["MOUSEWHEELUP"][2] == 202

    # selectedList = 0 ("Все списки") -> both 101 and 202 active
    harness.disarm_sniper_buy()
    harness.sniper_settings["selectedList"] = 0
    harness.rebuild_item_list()
    assert 101 in harness.active_items_map
    assert 202 in harness.active_items_map


def test_mode_all_triggers_when_profit_meets_threshold():
    """Verify Mode ALL snipes any lot in targets when total profit >= minProfit, even if not in list."""
    targets = {888: {"name": "Золотой слиток", "target": 10000}}
    lists = [{"name": "Пустой список", "items": []}]
    harness = WoWSniperHarness(
        targets=targets,
        shopping_lists=lists,
        settings={"mode": "ALL", "minProfit": 5000},
    )
    harness.set_enabled(True)

    # Item 888 is NOT in shopping list
    assert 888 not in harness.active_items_map

    # Buyout 3000 -> profit = 10000 - 3000 = 7000 >= minProfit 5000 -> triggers
    harness.auction_lots = [
        {"index": 1, "item_id": 888, "count": 1, "buyout": 3000, "min_bid": 500}
    ]
    harness.scan_current_page()

    assert harness.FT_IsArmed is True
    assert harness.override_bindings["MOUSEWHEELUP"][2] == 888
    assert harness.override_bindings["MOUSEWHEELUP"][3] == 3000


def test_mode_all_rejects_when_profit_below_threshold():
    """Verify Mode ALL rejects lots where profit is positive but below minProfit."""
    targets = {888: {"name": "Золотой слиток", "target": 10000}}
    harness = WoWSniperHarness(
        targets=targets,
        shopping_lists=[],
        settings={"mode": "ALL", "minProfit": 5000},
    )
    harness.set_enabled(True)

    # Buyout 8000 -> profit = 10000 - 8000 = 2000 < minProfit 5000 -> rejected
    harness.auction_lots = [
        {"index": 1, "item_id": 888, "count": 1, "buyout": 8000, "min_bid": 500}
    ]
    harness.scan_current_page()

    assert harness.FT_IsArmed is False
    assert len(harness.override_bindings) == 0


def test_mode_all_zero_min_profit_triggers_on_any_profit():
    """Verify Mode ALL with minProfit=0 triggers on any profit (unitPrice <= unitTarget)."""
    targets = {777: {"name": "Ториевая руда", "target": 1000}}
    harness = WoWSniperHarness(
        targets=targets,
        shopping_lists=[],
        settings={"mode": "ALL", "minProfit": 0},
    )
    harness.set_enabled(True)

    # Buyout 999 -> profit = 1 copper -> triggers because minProfit <= 0
    harness.auction_lots = [
        {"index": 1, "item_id": 777, "count": 1, "buyout": 999, "min_bid": 500}
    ]
    harness.scan_current_page()

    assert harness.FT_IsArmed is True
    assert harness.override_bindings["MOUSEWHEELUP"][2] == 777


def test_mode_all_rejects_lots_above_ceiling():
    """Verify Mode ALL rejects lots where buyout exceeds target ceiling (negative profit)."""
    targets = {777: {"name": "Ториевая руда", "target": 1000}}
    harness = WoWSniperHarness(
        targets=targets,
        shopping_lists=[],
        settings={"mode": "ALL", "minProfit": 0},
    )
    harness.set_enabled(True)

    # Buyout 1500 > ceiling 1000 -> rejected
    harness.auction_lots = [
        {"index": 1, "item_id": 777, "count": 1, "buyout": 1500, "min_bid": 500}
    ]
    harness.scan_current_page()

    assert harness.FT_IsArmed is False


# ===========================================================================
# 6. Settings Persistence Tests
# ===========================================================================

def test_settings_persistence_save_and_restore_full_state():
    """Verify saving and restoring mode, minProfit, selectedList, and custom prices across sessions."""
    targets = {101: 5000, 202: 10000}
    lists = [
        {"name": "Список А", "items": ["101"]},
        {"name": "Список Б", "items": ["202"]},
    ]

    harness1 = WoWSniperHarness(targets=targets, shopping_lists=lists)
    harness1.sniper_settings["mode"] = "ALL"
    harness1.sniper_settings["minProfit"] = 15000
    harness1.sniper_settings["selectedList"] = 2
    harness1.custom_targets = {
        101: {"enabled": False, "customPrice": 8500},
        202: {"enabled": True, "customPrice": 12000},
    }

    # Export SavedVariables
    saved_data = harness1.save_settings()

    assert "FT_SniperSettings" in saved_data
    assert "FT_CustomTargets" in saved_data
    assert saved_data["FT_SniperSettings"]["mode"] == "ALL"
    assert saved_data["FT_SniperSettings"]["minProfit"] == 15000
    assert saved_data["FT_SniperSettings"]["selectedList"] == 2
    assert saved_data["FT_CustomTargets"][101] == {"enabled": False, "customPrice": 8500}
    assert saved_data["FT_CustomTargets"][202] == {"enabled": True, "customPrice": 12000}

    # Create fresh harness with default values and restore
    harness2 = WoWSniperHarness(targets=targets, shopping_lists=lists)
    assert harness2.sniper_settings["mode"] == "LIST"
    assert harness2.sniper_settings["minProfit"] == 1000
    assert harness2.sniper_settings["selectedList"] == 1

    harness2.load_settings(saved_data)

    assert harness2.sniper_settings["mode"] == "ALL"
    assert harness2.sniper_settings["minProfit"] == 15000
    assert harness2.sniper_settings["selectedList"] == 2
    assert harness2.custom_targets[101] == {"enabled": False, "customPrice": 8500}
    assert harness2.custom_targets[202] == {"enabled": True, "customPrice": 12000}
    assert 202 in harness2.active_items_map


def test_settings_persistence_behavior_retained_after_restore():
    """Verify restored harness strictly enforces loaded settings during auction scanning."""
    targets = {301: 5000, 302: 20000}
    saved_state = {
        "FT_SniperSettings": {
            "mode": "ALL",
            "minProfit": 8000,
            "selectedList": 1,
        },
        "FT_CustomTargets": {
            301: {"enabled": False, "customPrice": 5000},  # Disabled
            302: {"enabled": True, "customPrice": 25000},   # Custom price override 25000 > 20000
        },
    }

    harness = WoWSniperHarness(targets=targets)
    harness.set_enabled(True)
    harness.load_settings(saved_state)

    # 1. Custom price override honored: buyout 22000 would exceed default 20000, but <= custom 25000
    # Profit = 25000 - 15000 = 10000 >= minProfit 8000
    harness.auction_lots = [
        {"index": 1, "item_id": 302, "count": 1, "buyout": 15000, "min_bid": 1000}
    ]
    harness.scan_current_page()
    assert harness.FT_IsArmed is True
    assert harness.override_bindings["MOUSEWHEELUP"][5] == 25000

    harness.disarm_sniper_buy()

    # 2. Restored minProfit threshold (8000) enforced: profit 5000 < 8000 is rejected
    harness.auction_lots = [
        {"index": 2, "item_id": 302, "count": 1, "buyout": 20000, "min_bid": 1000}
    ]
    harness.scan_current_page()
    assert harness.FT_IsArmed is False


def test_settings_persistence_corrupted_or_partial_payload():
    """Verify load_settings handles None, empty, or partial dictionaries without crashing."""
    harness = WoWSniperHarness(targets={101: 1000})

    # None and empty
    harness.load_settings(None)
    assert harness.sniper_settings["mode"] == "LIST"
    harness.load_settings({})
    assert harness.sniper_settings["mode"] == "LIST"
    harness.load_settings("corrupted string")
    assert harness.sniper_settings["mode"] == "LIST"

    # Partial payload (only FT_SniperSettings)
    harness.load_settings({"FT_SniperSettings": {"mode": "ALL", "minProfit": 2500}})
    assert harness.sniper_settings["mode"] == "ALL"
    assert harness.sniper_settings["minProfit"] == 2500
    assert harness.custom_targets == {}


