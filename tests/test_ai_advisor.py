"""Tests for src.ai_advisor module under Ponytail doctrine.

Verifies real functional execution of:
- build_compact_context() (R15): compact JSON-serializable market context (top-3 vendor, top-3 crates, top-3 price drops)
- AntigravityManagerClient (R16): Gemini 3.8 client, default model, offline heuristic fallback, network mocking, error resilience
- generate_market_advice() (R16): end-to-end advice generation and contract validation
- Dashboard AI integration (R17): embedding structured advice into build_standalone_dashboard()
No paper tests: invokes actual database queries, client classes, serialization, and HTML rendering.
"""

import io
import json
import os
from pathlib import Path
import sqlite3
import time
import urllib.error
from unittest.mock import MagicMock, patch
import pytest

from src.database import init_db
from src.ai_advisor import (
    build_compact_context,
    AntigravityManagerClient,
    generate_market_advice,
)
from src.dashboard import build_standalone_dashboard


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _default_mock_agy_unavailable(monkeypatch):
    """By default, simulate agy CLI unavailable to avoid unmocked 15s subprocess hangs in offline tests."""
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("Mock: agy CLI not available by default in tests")
    monkeypatch.setattr("src.ai_advisor.subprocess.run", fake_run)


@pytest.fixture
def memory_db():
    """Provide a fresh in-memory SQLite database connection."""
    conn = init_db(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def populated_advisor_db(memory_db):
    """Provide an in-memory database populated with multi-item market signals.

    Includes:
    - 4 vendor arbitrage candidates (to verify top-3 slicing and ordering)
    - 4 crafting recipes (3 tied to Waylaid Crates, 1 generic, to verify prioritization and top-3 slicing)
    - 4 items with price drop anomaly history (to verify Z <= -1.8 filtering and top-3 slicing)
    """
    now = int(time.time())
    with memory_db:
        # 1. Insert items
        memory_db.executemany(
            """INSERT INTO items (
                item_id, name, quality, item_class, category,
                vendor_sell_price, vendor_buy_price, target_item_id, target_item_count, reward_copper
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                # Vendor arbitrage items (buyout < vendor_sell, profit >= 50c)
                # Item 101: profit = 15000 - 2000 = 13000c (Top 1)
                (101, "Golden Diamond", 3, 7, "raw", 15000, 0, None, 0, 0),
                # Item 102: profit = 8000 - 1000 = 7000c (Top 2)
                (102, "Star Ruby", 3, 7, "raw", 8000, 0, None, 0, 0),
                # Item 103: profit = 2000 - 500 = 1500c (Top 3)
                (103, "Large Opal", 2, 7, "raw", 2000, 0, None, 0, 0),
                # Item 104: profit = 500 - 100 = 400c (Top 4, should be excluded from top-3)
                (104, "Silk Thread", 1, 7, "reagent", 500, 0, None, 0, 0),

                # Craftable items & reagents for Waylaid Crates
                (201, "Heavy Copper Broadsword", 2, 2, "craftable", 200, 0, None, 0, 0),
                (202, "Copper Bar", 1, 7, "raw", 10, 0, None, 0, 0),
                (203, "Rough Bronze Cuirass", 2, 4, "craftable", 250, 0, None, 0, 0),
                (204, "Bronze Bar", 1, 7, "raw", 20, 0, None, 0, 0),
                (205, "Lesser Healing Potion", 1, 0, "craftable", 50, 0, None, 0, 0),
                (206, "Peacebloom", 1, 7, "herb", 5, 0, None, 0, 0),
                (207, "Silverleaf", 1, 7, "herb", 5, 0, None, 0, 0),
                (208, "Linen Bandage", 1, 0, "craftable", 10, 0, None, 0, 0),
                (209, "Linen Cloth", 1, 7, "cloth", 2, 0, None, 0, 0),

                # Waylaid Crate shipment containers
                (301, "Waylaid Shipment: Heavy Copper Broadswords", 2, 0, "waylaid_crate", 0, 0, 201, 1, 5000),
                (302, "Waylaid Shipment: Rough Bronze Cuirass", 2, 0, "waylaid_crate", 0, 0, 203, 1, 6000),
                (303, "Waylaid Shipment: Lesser Healing Potions", 2, 0, "waylaid_crate", 0, 0, 205, 5, 4000),

                # Raw materials for price drops (Z-score)
                (401, "Briarthorn", 1, 7, "herb", 15, 0, None, 0, 0),
                (402, "Tin Ore", 1, 7, "ore", 20, 0, None, 0, 0),
                (403, "Mageroyal", 1, 7, "herb", 12, 0, None, 0, 0),
                (404, "Iron Ore", 1, 7, "ore", 30, 0, None, 0, 0),
            ],
        )

        # 2. Insert recipes and reagents
        memory_db.executemany(
            """INSERT INTO recipes (recipe_id, name, profession, crafted_item_id, yield)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (1, "Heavy Copper Broadsword", "Blacksmithing", 201, 1.0),
                (2, "Rough Bronze Cuirass", "Blacksmithing", 203, 1.0),
                (3, "Lesser Healing Potion", "Alchemy", 205, 1.0),
                (4, "Linen Bandage", "First Aid", 208, 1.0),  # Not a waylaid crate
            ],
        )
        memory_db.executemany(
            """INSERT INTO recipe_reagents (recipe_id, reagent_item_id, reagent_id, count, quantity)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (1, 202, 202, 2, 2),  # 2x Copper Bar
                (2, 204, 204, 2, 2),  # 2x Bronze Bar
                (3, 206, 206, 1, 1),  # 1x Peacebloom
                (3, 207, 207, 1, 1),  # 1x Silverleaf
                (4, 209, 209, 1, 1),  # 1x Linen Cloth
            ],
        )

        # 3. Current snapshots for vendor arbitrage and craft
        memory_db.executemany(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            [
                # Vendor arbitrage buyouts
                (101, "Everlook", 2000, now),
                (102, "Everlook", 1000, now),
                (103, "Everlook", 500, now),
                (104, "Everlook", 100, now),
                # Crafting items and reagents
                (201, "Everlook", 2000, now),
                (202, "Everlook", 100, now),   # cost = 200c, rev = 1900c, profit = 1700c
                (203, "Everlook", 3000, now),
                (204, "Everlook", 150, now),   # cost = 300c, rev = 2850c, profit = 2550c
                (205, "Everlook", 800, now),
                (206, "Everlook", 50, now),
                (207, "Everlook", 50, now),    # cost = 100c, rev = 760c, profit = 660c
                (208, "Everlook", 300, now),
                (209, "Everlook", 20, now),    # cost = 20c, rev = 285c, profit = 265c
            ],
        )

        # 4. Snapshots history with realistic variance for 4 anomaly items:
        # 401 Briarthorn: current at 100 (Deepest drop: Z ~ -3.69)
        # 402 Tin Ore: current at 400 (Deep drop: Z ~ -3.50)
        # 403 Mageroyal: current at 650 (Drop: Z ~ -3.01)
        # 404 Iron Ore: current at 800 (Drop: Z ~ -2.24, should be sliced off)
        anomaly_snapshots = []
        for i in range(15):
            ts = now - 3600 * (16 - i)
            base_var = (i % 5 - 2) * 50
            anomaly_snapshots.append((401, "Everlook", 1000 + base_var, ts))
            anomaly_snapshots.append((402, "Everlook", 1000 + base_var, ts))
            anomaly_snapshots.append((403, "Everlook", 1000 + base_var, ts))
            anomaly_snapshots.append((404, "Everlook", 1000 + base_var, ts))

        anomaly_snapshots.extend([
            (401, "Everlook", 100, now),
            (402, "Everlook", 400, now),
            (403, "Everlook", 650, now),
            (404, "Everlook", 800, now),
        ])
        memory_db.executemany(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            anomaly_snapshots,
        )

    return memory_db


# ---------------------------------------------------------------------------
# R15: build_compact_context() Tests
# ---------------------------------------------------------------------------

def test_build_compact_context_empty_database(memory_db):
    """Verify build_compact_context produces empty section lists on an unpopulated database."""
    context = build_compact_context(memory_db, realm="Everlook")

    assert isinstance(context, dict)
    assert context["realm"] == "Everlook"
    assert "vendor_arbitrage" in context
    assert "waylaid_crafting" in context
    assert "price_anomalies" in context

    assert context["vendor_arbitrage"] == []
    assert context["waylaid_crafting"] == []
    assert context["price_anomalies"] == []

    # Verify JSON serializability
    serialized = json.dumps(context)
    loaded = json.loads(serialized)
    assert loaded == context


def test_build_compact_context_top3_slices_and_ordering(populated_advisor_db):
    """Verify build_compact_context extracts at most 3 items per section with proper prioritization."""
    context = build_compact_context(populated_advisor_db, realm="Everlook")

    # 1. Vendor Arbitrage: 4 available in DB, only top-3 returned
    vendor = context["vendor_arbitrage"]
    assert len(vendor) == 3
    # Top-1 by profit: Golden Diamond (13000c) > Star Ruby (7000c) > Large Opal (1500c)
    assert vendor[0]["name"] == "Golden Diamond"
    assert vendor[0]["profit_copper"] == 13000
    assert vendor[1]["name"] == "Star Ruby"
    assert vendor[1]["profit_copper"] == 7000
    assert vendor[2]["name"] == "Large Opal"
    assert vendor[2]["profit_copper"] == 1500
    # Silk Thread (item 104, profit 400c) must be sliced off
    assert all(it["item_id"] != 104 for it in vendor)

    # 2. Waylaid Crates: Waylaid recipes prioritized, top-3 returned
    crafting = context["waylaid_crafting"]
    assert len(crafting) == 3
    # All 3 returned must be Waylaid targets since we inserted 3 Waylaid recipes
    for it in crafting:
        assert it["waylaid_crate_name"] is not None
        assert "Waylaid Shipment" in it["waylaid_crate_name"]
    # Non-waylaid Linen Bandage should not be in top-3 when waylaid targets exist
    assert all("Linen" not in it["name"] for it in crafting)

    # 3. Price Anomalies: 4 items with Z <= -1.8, only top-3 deepest drops returned
    anomalies = context["price_anomalies"]
    assert len(anomalies) == 3
    # Sorted by deepest drop (lowest Z score first)
    # Briarthorn (lowest Z) first, then Tin Ore, then Mageroyal
    assert anomalies[0]["name"] == "Briarthorn"
    assert anomalies[0]["z_score"] < anomalies[1]["z_score"]
    assert anomalies[1]["z_score"] < anomalies[2]["z_score"]
    assert anomalies[0]["z_score"] <= -1.8
    assert anomalies[1]["z_score"] <= -1.8
    assert anomalies[2]["z_score"] <= -1.8


def test_build_compact_context_data_structure_validation(populated_advisor_db):
    """Validate data types and required fields for all items in context."""
    context = build_compact_context(populated_advisor_db, realm="Everlook")

    # Validate vendor arbitrage items
    for item in context["vendor_arbitrage"]:
        assert isinstance(item["item_id"], int)
        assert isinstance(item["name"], str) and len(item["name"]) > 0
        assert isinstance(item["buyout_price"], int) and item["buyout_price"] > 0
        assert isinstance(item["vendor_sell_price"], int) and item["vendor_sell_price"] > 0
        assert isinstance(item["profit_copper"], int) and item["profit_copper"] >= 50
        assert item["profit_copper"] == item["vendor_sell_price"] - item["buyout_price"]

    # Validate waylaid crafting items
    for item in context["waylaid_crafting"]:
        assert isinstance(item["recipe_id"], int)
        assert isinstance(item["name"], str) and len(item["name"]) > 0
        assert isinstance(item["profession"], str) and len(item["profession"]) > 0
        assert isinstance(item["cost_copper"], int) and item["cost_copper"] > 0
        assert isinstance(item["revenue_copper"], int) and item["revenue_copper"] > 0
        assert isinstance(item["profit_copper"], int) and item["profit_copper"] > 0
        assert isinstance(item["roi_percent"], (int, float)) and item["roi_percent"] >= 25.0

    # Validate price anomalies items
    for item in context["price_anomalies"]:
        assert isinstance(item["item_id"], int)
        assert isinstance(item["name"], str) and len(item["name"]) > 0
        assert isinstance(item["current_price"], int) and item["current_price"] > 0
        assert isinstance(item["mean_price"], (int, float)) and item["mean_price"] > 0
        assert isinstance(item["z_score"], float) and item["z_score"] <= -1.8
        assert item["signal"] == "STRONG_BUY"


def test_build_compact_context_realm_isolation(populated_advisor_db):
    """Verify build_compact_context isolates queries by realm correctly."""
    context_everlook = build_compact_context(populated_advisor_db, realm="Everlook")
    assert len(context_everlook["vendor_arbitrage"]) == 3

    # On a realm with no data, lists must be empty
    context_other = build_compact_context(populated_advisor_db, realm="NonExistentRealm")
    assert len(context_other["vendor_arbitrage"]) == 0
    assert len(context_other["waylaid_crafting"]) == 0
    assert len(context_other["price_anomalies"]) == 0


# ---------------------------------------------------------------------------
# R16: AntigravityManagerClient & Heuristic Fallback Tests
# ---------------------------------------------------------------------------

def test_antigravity_client_init_defaults():
    """Verify AntigravityManagerClient initializes with Gemini 3.8 Flash High as default."""
    # Ensure environment does not have key override
    with patch.dict(os.environ, {}, clear=True):
        client = AntigravityManagerClient()
        assert client.model == "gemini-3.8-flash-high"
        assert client.timeout == 15
        assert client.base_url == "https://generativelanguage.googleapis.com/v1beta/models"
        assert client.api_key is None


def test_antigravity_client_init_custom_params():
    """Verify custom model, timeout, and base_url configuration."""
    client = AntigravityManagerClient(
        api_key="secret-gemini-key",
        model="gemini-3.8-pro",
        timeout=30,
        base_url="https://proxy.example.com/models/",
    )
    assert client.api_key == "secret-gemini-key"
    assert client.model == "gemini-3.8-pro"
    assert client.timeout == 30
    assert client.base_url == "https://proxy.example.com/models"


def test_antigravity_client_offline_heuristic_generation(populated_advisor_db):
    """Verify offline mode produces a fully structured actionable session checklist."""
    context = build_compact_context(populated_advisor_db, realm="Everlook")

    client = AntigravityManagerClient(api_key=None)
    advice = client.generate_advice(context)

    # 1. Structure contract
    assert isinstance(advice, dict)
    assert "title" in advice and len(advice["title"]) > 0
    assert "summary" in advice and len(advice["summary"]) > 0
    assert advice["model"] == "gemini-3.8-flash-high"
    assert isinstance(advice["actions"], list)
    assert isinstance(advice["checklist"], list)

    # 2. Must produce 3 vendor + 3 crafting + 3 anomalies = 9 actionable steps
    actions = advice["actions"]
    assert len(actions) == 9

    # 3. Action element structure validation
    for i, act in enumerate(actions, start=1):
        assert act["step"] == i
        assert act["category"] in {"vendor_arbitrage", "waylaid_crafting", "price_dump"}
        assert isinstance(act["action"], str) and len(act["action"]) > 0
        assert isinstance(act["target"], str) and len(act["target"]) > 0
        assert isinstance(act["expected_profit"], str) and len(act["expected_profit"]) > 0
        assert act["priority"] in {"HIGH", "MEDIUM", "LOW"}

    # 4. Check categories distribution
    categories = [a["category"] for a in actions]
    assert categories.count("vendor_arbitrage") == 3
    assert categories.count("waylaid_crafting") == 3
    assert categories.count("price_dump") == 3

    # 5. Checklist strings should correspond to actions
    checklist = advice["checklist"]
    assert len(checklist) == len(actions)
    for line in checklist:
        assert isinstance(line, str)
        assert line.startswith("[")


def test_antigravity_client_heuristic_empty_context():
    """Verify heuristic generator handles empty market context gracefully."""
    empty_context = {
        "realm": "Everlook",
        "vendor_arbitrage": [],
        "waylaid_crafting": [],
        "price_anomalies": [],
    }

    client = AntigravityManagerClient(api_key=None)
    advice = client.generate_heuristic_advice(empty_context)

    assert advice["title"] == "Тактический чек-лист торговой сессии"
    assert "не обнаружено" in advice["summary"]
    assert advice["actions"] == []
    assert advice["checklist"] == []


def test_antigravity_client_call_aliases(populated_advisor_db):
    """Verify aliases .generate(), .generate_market_advice(), and __call__() work identically."""
    context = build_compact_context(populated_advisor_db, realm="Everlook")
    client = AntigravityManagerClient(api_key=None)

    adv1 = client.generate_advice(context)
    adv2 = client.generate(context)
    adv3 = client.generate_market_advice(context)
    adv4 = client(context)

    assert adv1 == adv2 == adv3 == adv4


def test_antigravity_client_network_gemini_parsing():
    """Verify network request payload formatting and response parsing for Gemini 3.8."""
    context = {
        "vendor_arbitrage": [{"item_id": 101, "name": "Golden Diamond", "profit_copper": 13000}],
        "waylaid_crafting": [],
        "price_anomalies": [],
    }

    gemini_payload = {
        "title": "Тактический план торговой сессии",
        "summary": "Рынок стабилен, обнаружен прибыльный вендорный арбитраж.",
        "model": "gemini-3.8-flash-high",
        "actions": [
            {
                "step": 1,
                "category": "vendor_arbitrage",
                "action": "Выкупить Golden Diamond на аукционе и продать вендору",
                "target": "Golden Diamond",
                "expected_profit": "+1g 30s",
                "priority": "HIGH",
            }
        ],
    }

    gemini_api_response = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": f"```json\n{json.dumps(gemini_payload, ensure_ascii=False)}\n```"
                        }
                    ]
                }
            }
        ]
    }

    # Mock urllib response
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(gemini_api_response).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        client = AntigravityManagerClient(api_key="valid-test-key")
        advice = client.generate_advice(context)

        # Assert network call was attempted
        assert mock_urlopen.called
        req_arg = mock_urlopen.call_args[0][0]
        assert "gemini-3.8-flash-high:generateContent" in req_arg.full_url
        assert "key=valid-test-key" in req_arg.full_url

        # Assert parsed structured output
        assert advice["title"] == "Тактический план торговой сессии"
        assert advice["summary"] == "Рынок стабилен, обнаружен прибыльный вендорный арбитраж."
        assert len(advice["actions"]) == 1
        assert advice["actions"][0]["target"] == "Golden Diamond"
        assert advice["actions"][0]["expected_profit"] == "+1g 30s"
        assert len(advice["checklist"]) == 1


def test_antigravity_client_network_error_fallback():
    """Verify that any network error (e.g. URLError) seamlessly falls back to heuristic generation."""
    context = {
        "vendor_arbitrage": [{"name": "Test Lot", "profit_copper": 500, "buyout_price": 100}],
        "waylaid_crafting": [],
        "price_anomalies": [],
    }

    client = AntigravityManagerClient(api_key="test-key-failing-network")

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
        # Must not raise an exception; must return valid heuristic advice
        advice = client.generate_advice(context)
        assert isinstance(advice, dict)
        assert len(advice["actions"]) == 1
        assert advice["actions"][0]["target"] == "Test Lot"


def test_antigravity_client_corrupted_response_fallback():
    """Verify that invalid JSON or missing fields from network call fall back safely."""
    context = {
        "vendor_arbitrage": [{"name": "Fallback Item", "profit_copper": 1000, "buyout_price": 200}],
        "waylaid_crafting": [],
        "price_anomalies": [],
    }

    mock_resp = MagicMock()
    mock_resp.read.return_value = b"<html>502 Bad Gateway</html>"
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        client = AntigravityManagerClient(api_key="valid-key")
        advice = client.generate_advice(context)

        assert isinstance(advice, dict)
        assert len(advice["actions"]) == 1
        assert advice["actions"][0]["target"] == "Fallback Item"


def test_antigravity_client_call_agy_cli_success():
    """Verify _call_agy_cli parses [c4] prefix and markdown json code blocks cleanly."""
    context = {
        "realm": "Everlook",
        "vendor_arbitrage": [
            {"item_id": 101, "name": "Golden Diamond", "profit_copper": 13000, "buyout_price": 2000, "vendor_sell_price": 15000}
        ],
        "waylaid_crafting": [],
        "price_anomalies": [],
    }

    mock_agy_output = (
        "[c4]\n"
        "```json\n"
        "{\n"
        '  "title": "Тактический план торговой сессии",\n'
        '  "summary": "Рынок стабилен, обнаружен прибыльный вендорный арбитраж.",\n'
        '  "model": "gemini-3.8-flash-high",\n'
        '  "actions": [\n'
        '    {\n'
        '      "step": 1,\n'
        '      "category": "vendor_arbitrage",\n'
        '      "action": "Выкупить Golden Diamond на аукционе и продать вендору",\n'
        '      "target": "Golden Diamond",\n'
        '      "expected_profit": "+1g 30s",\n'
        '      "priority": "HIGH"\n'
        '    }\n'
        '  ]\n'
        "}\n"
        "```\n"
    )

    mock_res = MagicMock()
    mock_res.returncode = 0
    mock_res.stdout = mock_agy_output
    mock_res.stderr = ""

    with patch("src.ai_advisor.subprocess.run", return_value=mock_res) as mock_run:
        client = AntigravityManagerClient(api_key=None)
        # 1. Direct invocation of _call_agy_cli
        advice = client._call_agy_cli(context)

        assert mock_run.called
        called_cmd = mock_run.call_args[0][0]
        assert called_cmd[0] == "agy"
        assert "-p" in called_cmd
        assert "--model" in called_cmd
        assert client.model in called_cmd

        assert advice["title"] == "Тактический план торговой сессии"
        assert advice["summary"] == "Рынок стабилен, обнаружен прибыльный вендорный арбитраж."
        assert advice["model"] == "gemini-3.8-flash-high"
        assert len(advice["actions"]) == 1
        assert advice["actions"][0]["target"] == "Golden Diamond"
        assert advice["actions"][0]["expected_profit"] == "+1g 30s"
        assert advice["actions"][0]["priority"] == "HIGH"
        assert len(advice["checklist"]) == 1
        assert "Golden Diamond" in advice["checklist"][0]

        # 2. Integration via generate_advice(context) when api_key is None
        advice_via_gen = client.generate_advice(context)
        assert advice_via_gen["title"] == "Тактический план торговой сессии"
        assert len(advice_via_gen["actions"]) == 1
        assert advice_via_gen["actions"][0]["target"] == "Golden Diamond"


def test_antigravity_client_call_agy_cli_fallback_on_failure():
    """Verify generate_advice falls back to generate_heuristic_advice when agy CLI fails or is missing."""
    context = {
        "realm": "Everlook",
        "vendor_arbitrage": [
            {"item_id": 101, "name": "Golden Diamond", "profit_copper": 13000, "buyout_price": 2000, "vendor_sell_price": 15000}
        ],
        "waylaid_crafting": [],
        "price_anomalies": [],
    }

    client = AntigravityManagerClient(api_key=None)

    # 1. agy CLI command not found (FileNotFoundError)
    with patch("src.ai_advisor.subprocess.run", side_effect=FileNotFoundError("agy not found")):
        advice = client.generate_advice(context)
        assert isinstance(advice, dict)
        assert advice["title"] == "Тактический чек-лист торговой сессии"
        assert len(advice["actions"]) == 1
        assert advice["actions"][0]["target"] == "Golden Diamond"

    # 2. agy CLI returns non-zero exit code
    mock_failed_proc = MagicMock(returncode=1, stdout="", stderr="Error: service unavailable")
    with patch("src.ai_advisor.subprocess.run", return_value=mock_failed_proc):
        advice = client.generate_advice(context)
        assert isinstance(advice, dict)
        assert advice["title"] == "Тактический чек-лист торговой сессии"
        assert len(advice["actions"]) == 1
        assert advice["actions"][0]["target"] == "Golden Diamond"

    # 3. agy CLI returns empty stdout
    mock_empty_proc = MagicMock(returncode=0, stdout="   \n", stderr="")
    with patch("src.ai_advisor.subprocess.run", return_value=mock_empty_proc):
        advice = client.generate_advice(context)
        assert isinstance(advice, dict)
        assert advice["title"] == "Тактический чек-лист торговой сессии"
        assert len(advice["actions"]) == 1
        assert advice["actions"][0]["target"] == "Golden Diamond"


def test_antigravity_client_clean_code_fence_and_json_parsing():
    """Verify _clean_code_fence and _parse_json_payload handle [c4] prefix and backtick code blocks."""
    client = AntigravityManagerClient()
    raw = "[c4]\n```json\n{\"test\": 123}\n```"
    assert client._parse_json_payload(raw) == {"test": 123}

    # Without code fences, just [c4]
    raw_no_fence = "[c4] {\"test\": 456}"
    assert client._parse_json_payload(raw_no_fence) == {"test": 456}

    # Embedded in conversational noise
    raw_embedded = "Here is the plan:\n[c4]\n```\n{\"test\": 789}\n```\nDone."
    assert client._parse_json_payload(raw_embedded) == {"test": 789}


# ---------------------------------------------------------------------------
# R16: generate_market_advice() Functional Tests
# ---------------------------------------------------------------------------

def test_generate_market_advice_end_to_end(populated_advisor_db):
    """Verify top-level generate_market_advice creates standard advice object."""
    context = build_compact_context(populated_advisor_db, realm="Everlook")
    advice = generate_market_advice(context)

    assert isinstance(advice, dict)
    assert "title" in advice
    assert "summary" in advice
    assert "actions" in advice
    assert "checklist" in advice
    assert len(advice["actions"]) > 0


def test_generate_market_advice_with_custom_callable():
    """Verify generate_market_advice supports custom callable client."""
    context = {"test": 123}

    def dummy_generator(ctx):
        return {
            "title": "Custom Strategy",
            "summary": "Custom analysis",
            "actions": [
                {"step": 1, "action": "Craft 5x Pots", "target": "Healing Potion", "expected_profit": "50s"}
            ],
        }

    advice = generate_market_advice(context, client=dummy_generator)
    assert advice["title"] == "Custom Strategy"
    assert advice["summary"] == "Custom analysis"
    assert len(advice["actions"]) == 1
    assert advice["actions"][0]["target"] == "Healing Potion"
    assert advice["actions"][0]["step"] == 1


def test_generate_market_advice_invalid_client():
    """Verify generate_market_advice raises TypeError on unsupported client type."""
    with pytest.raises(TypeError):
        generate_market_advice({}, client=12345)


# ---------------------------------------------------------------------------
# R17: Dashboard Integration Tests
# ---------------------------------------------------------------------------

def test_dashboard_renders_ai_advice_banner(populated_advisor_db, tmp_path):
    """Verify generated AI advice is rendered in the top banner of the HTML dashboard."""
    context = build_compact_context(populated_advisor_db, realm="Everlook")
    advice = generate_market_advice(context)

    html_file = tmp_path / "dashboard_with_ai.html"
    build_standalone_dashboard(
        populated_advisor_db,
        output_path=str(html_file),
        ai_advice=advice,
        realm="Everlook",
    )

    assert html_file.exists()
    content = html_file.read_text(encoding="utf-8")

    # Verify AI banner container and badge
    assert 'id="ai-advisor-banner"' in content
    assert '<span class="ai-badge">Gemini 3.8 Flash</span>' in content
    assert "Тактические рекомендации AI-ассистента" in content

    # Verify verdict summary is rendered
    assert advice["summary"] in content

    # Verify checklist actions are rendered in HTML list
    assert "ai-checklist" in content
    for act in advice["actions"]:
        assert act["target"] in content


def test_dashboard_graceful_without_ai_advice(populated_advisor_db, tmp_path):
    """Verify dashboard renders placeholder when ai_advice is None without crashing."""
    html_file = tmp_path / "dashboard_no_ai.html"
    build_standalone_dashboard(
        populated_advisor_db,
        output_path=str(html_file),
        ai_advice=None,
        realm="Everlook",
    )

    assert html_file.exists()
    content = html_file.read_text(encoding="utf-8")

    assert 'id="ai-advisor-banner"' in content
    assert "ai-empty" in content
    assert "Рекомендации AI-ассистента не сформированы" in content
