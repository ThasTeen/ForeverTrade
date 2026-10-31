"""Tests for src.dashboard module under Ponytail doctrine.

Verifies real functional execution of:
- format_currency() (R13): WoW currency conversion (g/s/c), HTML styling, edge cases
- notify_high_value_arbitrage() (R14): alert triggers, threshold filtering (>= 1g / 10,000c), terminal bell
- build_standalone_dashboard() (R12, R13): standalone HTML creation, Plotly.js charts, 3 signal tables,
  AI advisor banner integration, database data rendering, offline autonomy, realm filtering.
No paper tests: invokes actual functions and validates generated files and DOM structures.
"""

import json
import logging
import os
from pathlib import Path
import sqlite3
import time
import pytest

from src.database import init_db
from src.dashboard import (
    format_currency,
    notify_high_value_arbitrage,
    build_standalone_dashboard,
)


@pytest.fixture
def memory_db():
    """Provide a fresh in-memory SQLite database connection."""
    conn = init_db(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def populated_dashboard_db(memory_db):
    """Provide an in-memory database populated with vendor arbitrage, waylaid crafting, and anomaly data."""
    now = int(time.time())
    with memory_db:
        # 1. Insert items
        memory_db.executemany(
            """INSERT INTO items (
                item_id, name, quality, item_class, category,
                vendor_sell_price, vendor_buy_price, target_item_id, target_item_count, reward_copper
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                # Item 101: High-value vendor arbitrage (buyout 2,000c, vendor sell 15,000c -> profit 13,000c > 1g)
                (101, "Golden Diamond", 3, 7, "raw", 15000, 0, None, 0, 0),
                # Item 102: Regular vendor arbitrage (buyout 100c, vendor sell 500c -> profit 400c)
                (102, "Silk Thread", 1, 7, "reagent", 500, 0, None, 0, 0),
                # Item 201: Waylaid target craft (Heavy Copper Broadsword)
                (201, "Heavy Copper Broadsword", 2, 2, "craftable", 200, 0, None, 0, 0),
                # Item 202: Reagent (Copper Bar)
                (202, "Copper Bar", 1, 7, "raw", 10, 0, None, 0, 0),
                # Item 301: Waylaid Crate container targetting item 201
                (301, "Waylaid Shipment: Heavy Copper Broadswords", 2, 0, "waylaid_crate", 0, 0, 201, 1, 5000),
                # Item 401: Raw material with price anomaly history
                (401, "Peacebloom", 1, 7, "herb", 5, 0, None, 0, 0),
            ],
        )

        # 2. Insert recipe & reagents for Waylaid Crates craft
        memory_db.execute(
            """INSERT INTO recipes (
                recipe_id, name, profession, crafted_item_id, yield
            ) VALUES (?, ?, ?, ?, ?)""",
            (1, "Heavy Copper Broadsword", "Blacksmithing", 201, 1.0),
        )
        memory_db.execute(
            """INSERT INTO recipe_reagents (
                recipe_id, reagent_item_id, reagent_id, count, quantity
            ) VALUES (?, ?, ?, ?, ?)""",
            (1, 202, 202, 2, 2),
        )

        # 3. Insert price snapshots
        # Current snapshots for arbitrage and craft
        memory_db.executemany(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            [
                (101, "Everlook", 2000, now),
                (102, "Everlook", 100, now),
                (201, "Everlook", 1000, now),   # Crafted revenue = 1000c * 0.95 = 950c
                (202, "Everlook", 100, now),    # Reagent cost = 2 * 100c = 200c -> profit 750c, ROI 375%
            ],
        )

        # 16 historical snapshots for Peacebloom to generate Z-Score anomaly
        # 15 snapshots at price ~1000, then current snapshot at 200 (sharp drop -> STRONG_BUY)
        snapshots = []
        for i in range(15):
            snapshots.append((401, "Everlook", 1000, now - 3600 * (16 - i)))
        snapshots.append((401, "Everlook", 200, now))
        memory_db.executemany(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            snapshots,
        )

    return memory_db


# ===========================================================================
# 1. format_currency() Tests (R13)
# ===========================================================================

def test_format_currency_standard_gsc():
    """Verify standard gold/silver/copper text formatting."""
    # 12345 copper -> 1g 23s 45c
    assert format_currency(12345) == "1g 23s 45c"
    # Exact gold boundary
    assert format_currency(10000) == "1g 0s 0c"
    # Exact silver boundary
    assert format_currency(100) == "0g 1s 0c"
    # Small copper
    assert format_currency(45) == "0g 0s 45c"
    # Zero copper
    assert format_currency(0) == "0g 0s 0c"


def test_format_currency_edge_cases():
    """Verify edge cases: negative values, sub-100, sub-10000, None, float."""
    # Only copper (< 100)
    assert format_currency(99) == "0g 0s 99c"
    assert format_currency(1) == "0g 0s 1c"

    # Only silver and copper (< 10000)
    assert format_currency(9999) == "0g 99s 99c"
    assert format_currency(105) == "0g 1s 5c"

    # Negative values
    assert format_currency(-12345) == "-1g 23s 45c"
    assert format_currency(-50) == "-0g 0s 50c"
    assert format_currency(-10000) == "-1g 0s 0c"

    # Non-integer / float / None / invalid strings
    assert format_currency(12345.6) == "1g 23s 46c"
    assert format_currency(None) == "0g 0s 0c"
    assert format_currency("not_a_number") == "0g 0s 0c"


def test_format_currency_as_html():
    """Verify HTML markup with color classes for gold, silver, and copper."""
    html_out = format_currency(12345, as_html=True)
    expected = (
        '<span class="money-gold">1g</span> '
        '<span class="money-silver">23s</span> '
        '<span class="money-copper">45c</span>'
    )
    assert html_out == expected
    assert 'class="money-gold"' in html_out
    assert 'class="money-silver"' in html_out
    assert 'class="money-copper"' in html_out

    # Negative HTML formatting
    neg_html = format_currency(-12345, as_html=True)
    assert neg_html.startswith("-<span class=\"money-gold\">1g</span>")


# ===========================================================================
# 2. notify_high_value_arbitrage() Tests (R14)
# ===========================================================================

def test_notify_high_value_arbitrage_default_threshold(capsys, caplog):
    """Verify notification triggers for profit >= 10,000c (1g) and ignores < 10,000c."""
    items = [
        {"name": "Epic Item", "profit_copper": 15000},     # >= 10,000 -> QUALIFIES
        {"name": "Boundary Item", "profit_copper": 10000}, # >= 10,000 -> QUALIFIES
        {"name": "Almost Item", "profit_copper": 9999},    # < 10,000  -> IGNORED
        {"name": "Cheap Item", "profit_copper": 250},      # < 10,000  -> IGNORED
    ]

    with caplog.at_level(logging.WARNING):
        result = notify_high_value_arbitrage(items)

    assert len(result) == 2
    qualifying_names = [r["name"] for r in result]
    assert qualifying_names == ["Epic Item", "Boundary Item"]

    # Verify terminal bell was sent to stdout
    captured = capsys.readouterr()
    assert "\a" in captured.out

    # Verify warning log was emitted
    assert any("HIGH VALUE ARBITRAGE ALERT" in record.message for record in caplog.records)


def test_notify_high_value_arbitrage_no_qualifying_items(capsys, caplog):
    """Verify no bell or warning is emitted when no items exceed the threshold."""
    items = [
        {"name": "Sub-threshold 1", "profit_copper": 5000},
        {"name": "Sub-threshold 2", "profit_copper": 9999},
    ]

    with caplog.at_level(logging.WARNING):
        result = notify_high_value_arbitrage(items)

    assert result == []
    captured = capsys.readouterr()
    assert "\a" not in captured.out
    assert not any("HIGH VALUE ARBITRAGE ALERT" in record.message for record in caplog.records)


def test_notify_high_value_arbitrage_custom_threshold():
    """Verify custom profit threshold behaves accurately."""
    items = [
        {"name": "Item A", "profit_copper": 5000},
        {"name": "Item B", "profit_copper": 4999},
        {"name": "Item C", "profit_copper": 25000},
    ]

    # Lower threshold (5000 copper)
    low_res = notify_high_value_arbitrage(items, threshold_copper=5000)
    assert [x["name"] for x in low_res] == ["Item A", "Item C"]

    # Higher threshold (20000 copper)
    high_res = notify_high_value_arbitrage(items, threshold_copper=20000)
    assert [x["name"] for x in high_res] == ["Item C"]


def test_notify_high_value_arbitrage_field_fallback():
    """Verify support for 'profit' field when 'profit_copper' is absent and invalid values."""
    items = [
        {"name": "Item with profit field", "profit": 12000},
        {"name": "Item with string profit", "profit": "15000"},
        {"name": "Invalid profit", "profit": "not_a_number"},
        {"name": "None profit", "profit": None},
    ]
    res = notify_high_value_arbitrage(items)
    assert len(res) == 2
    assert [x["name"] for x in res] == ["Item with profit field", "Item with string profit"]


# ===========================================================================
# 3. build_standalone_dashboard() Tests (R12, R13, R14)
# ===========================================================================

def test_build_standalone_dashboard_creates_html_file(populated_dashboard_db, tmp_path):
    """Verify build_standalone_dashboard writes an autonomous HTML file to disk."""
    out_file = tmp_path / "portal.html"
    returned_html = build_standalone_dashboard(populated_dashboard_db, output_path=str(out_file))

    # File exists and is non-empty
    assert out_file.exists()
    assert out_file.stat().st_size > 500

    # Read content from disk
    disk_html = out_file.read_text(encoding="utf-8")
    assert disk_html == returned_html
    assert disk_html.strip().startswith("<!DOCTYPE html>")
    assert disk_html.strip().endswith("</html>")


def test_build_standalone_dashboard_required_sections_and_plotly(populated_dashboard_db, tmp_path):
    """Verify presence of Plotly.js charts and the 3 required signal tables."""
    out_file = tmp_path / "portal.html"
    html_content = build_standalone_dashboard(populated_dashboard_db, output_path=str(out_file))

    # 1. Plotly.js chart presence
    assert "plotly" in html_content.lower()
    assert 'id="charts-section"' in html_content
    # Chart title in Plotly config/layout
    chart_title = "Динамика цен ключевого сырья и расходников"
    assert (chart_title in html_content) or (json.dumps(chart_title)[1:-1] in html_content)

    # 2. Block 1: «Вендорный арбитраж»
    assert "Вендорный арбитраж" in html_content
    assert 'id="vendor-arbitrage"' in html_content

    # 3. Block 2: «Крафт под Waylaid Crates»
    assert "Крафт под Waylaid Crates" in html_content
    assert 'id="waylaid-crates"' in html_content

    # 4. Block 3: «Аномальные сбросы цен»
    assert "Аномальные сбросы цен" in html_content
    assert 'id="price-anomalies"' in html_content


def test_build_standalone_dashboard_renders_database_data(populated_dashboard_db, tmp_path):
    """Verify database signal data is accurately rendered into HTML table rows."""
    out_file = tmp_path / "portal.html"
    html_content = build_standalone_dashboard(populated_dashboard_db, output_path=str(out_file))

    # Check vendor arbitrage item appears
    assert "Golden Diamond" in html_content
    assert "Silk Thread" in html_content

    # Check Waylaid Crates craft item appears
    assert "Heavy Copper Broadsword" in html_content
    assert "Blacksmithing" in html_content
    assert "Waylaid Shipment: Heavy Copper Broadswords" in html_content

    # Check price anomaly item appears
    assert "Peacebloom" in html_content
    assert "badge-buy" in html_content or "BUY" in html_content

    # Check currency styling exists in rendered tables
    assert 'class="money-gold"' in html_content
    assert 'class="money-silver"' in html_content
    assert 'class="money-copper"' in html_content


def test_build_standalone_dashboard_ai_advice_integration(populated_dashboard_db, tmp_path):
    """Verify embedding tactical advice and checklist when ai_advice is provided."""
    out_file = tmp_path / "portal.html"
    ai_advice = {
        "summary": "Приоритет на скупку Peacebloom и крафт мечей",
        "checklist": [
            "Выкупить Golden Diamond у вендора с профитом 1g 30s",
            "Скрафтить 3x Heavy Copper Broadsword под ящики",
            "Инвестировать в подешевевший Peacebloom",
        ],
    }

    html_content = build_standalone_dashboard(
        populated_dashboard_db,
        output_path=str(out_file),
        ai_advice=ai_advice,
    )

    assert "Тактические рекомендации AI-ассистента" in html_content
    assert "Приоритет на скупку Peacebloom и крафт мечей" in html_content
    assert "Выкупить Golden Diamond у вендора с профитом 1g 30s" in html_content
    assert "Скрафтить 3x Heavy Copper Broadsword под ящики" in html_content
    assert "Инвестировать в подешевевший Peacebloom" in html_content


def test_build_standalone_dashboard_empty_database_graceful(memory_db, tmp_path):
    """Verify dashboard generation succeeds on an empty database without exceptions."""
    out_file = tmp_path / "empty_portal.html"
    html_content = build_standalone_dashboard(memory_db, output_path=str(out_file))

    assert out_file.exists()
    assert "Нет лотов с вендорной прибылью" in html_content
    assert "Нет активных маржинальных рецептов" in html_content
    assert "Аномальных сбросов или всплесков цен не обнаружено" in html_content

    # Empty chart annotation check (supports literal UTF-8 and JSON-escaped unicode)
    empty_chart_msg = "Нет данных снимков цен в базе"
    assert (empty_chart_msg in html_content) or (json.dumps(empty_chart_msg)[1:-1] in html_content)


def test_build_standalone_dashboard_offline_autonomy(populated_dashboard_db, tmp_path):
    """Verify HTML is self-contained: has charset, viewport, embedded css, and embedded js."""
    out_file = tmp_path / "autonomous.html"
    html_content = build_standalone_dashboard(populated_dashboard_db, output_path=str(out_file))

    # Meta headers for standalone browser display
    assert '<meta charset="UTF-8">' in html_content
    assert '<meta name="viewport"' in html_content

    # Embedded CSS styles
    assert "<style>" in html_content
    assert "--bg-main:" in html_content
    assert ".money-gold" in html_content

    # Embedded vanilla JS for column sorting
    assert "<script>" in html_content
    assert "table.sortable" in html_content


def test_build_standalone_dashboard_realm_filter(memory_db, tmp_path):
    """Verify realm filtering isolates data belonging strictly to the requested realm."""
    now = int(time.time())
    with memory_db:
        memory_db.executemany(
            """INSERT INTO items (item_id, name, quality, item_class, category, vendor_sell_price, vendor_buy_price)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                (501, "Everlook Exclusive Herb", 1, 7, "herb", 1000, 0),
                (502, "Nordanaar Exclusive Ore", 1, 7, "ore", 1000, 0),
            ],
        )
        memory_db.executemany(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            [
                (501, "Everlook", 200, now),   # Vendor arbitrage on Everlook: profit 800
                (502, "Nordanaar", 200, now),  # Vendor arbitrage on Nordanaar: profit 800
            ],
        )

    out_everlook = tmp_path / "everlook.html"
    html_everlook = build_standalone_dashboard(memory_db, output_path=str(out_everlook), realm="Everlook")

    assert "Everlook Exclusive Herb" in html_everlook
    assert "Nordanaar Exclusive Ore" not in html_everlook
