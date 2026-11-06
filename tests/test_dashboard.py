"""Tests for src.dashboard module under Ponytail doctrine.

Verifies real functional execution of:
- format_currency() (R13): WoW currency conversion (g/s/c), HTML styling, edge cases
- notify_high_value_arbitrage() (R14): alert triggers, threshold filtering (>= 1g / 10,000c), terminal bell
- build_standalone_dashboard() (R12, R13): standalone HTML creation, Plotly.js charts, 3 signal tables,
  AI advisor banner integration, database data rendering, offline autonomy, realm filtering.
- Task 17 (R52): Dual crafting display (Waylaid crates + classic recipes), meta auto-refresh (30s),
  header generation and snapshot freshness timestamps, parameter forwarding (has_bartering, has_master_chef, max_age_hours).
- Ticket 20 (R20): Goblin Terminal 100vh UI layout (65% signals / 35% inspector), Wowhead Forever tooltips,
  1-click copy name buttons (.copy-btn) & toast, Auctionator shopping list export format (ForeverTrade^),
  localStorage persistence (ft_active_tab, ft_search, ft_lang, ft_selected_item), and all 5 signal tabs.
No paper tests: invokes actual functions and validates generated files and DOM structures.
"""

import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import time
from unittest.mock import patch
import pytest

from src.database import init_db
from src.analytics import _CATALOG_CACHE
import src.dashboard as dashboard_mod
from src.dashboard import (
    format_currency,
    notify_high_value_arbitrage,
    build_standalone_dashboard,
    _build_price_history_chart,
)

_build_market_inspector_data = getattr(
    dashboard_mod,
    "_build_market_inspector_data",
    getattr(dashboard_mod, "_get_all_items_market_data", None),
)

generate_dashboard_html = build_standalone_dashboard


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

    # Special float values & fuzzing (R39): inf, -inf, nan must not raise OverflowError
    assert format_currency(float("inf")) in ("0c", "0g 0s 0c")
    assert format_currency(float("-inf")) in ("0c", "0g 0s 0c")
    assert format_currency(float("nan")) in ("0c", "0g 0s 0c")

    # Booleans (R39): bool is subclass of int in Python, must return "0c", not "1c"
    assert format_currency(True) == "0c"
    assert format_currency(False) == "0c"


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


# ===========================================================================
# 4. Task 09: Hardening & Bugfix Tests (R35, R36, R37, R39)
# ===========================================================================

def test_build_standalone_dashboard_stored_xss_protection(memory_db, tmp_path):
    """Verify Stored XSS protection: dangerous tag payloads in item names are safely escaped (R35)."""
    now = int(time.time())
    xss_payload = "</script><script>alert('xss')</script>"
    with memory_db:
        memory_db.execute(
            """INSERT INTO items (item_id, name, quality, item_class, category, vendor_sell_price, vendor_buy_price)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (999, xss_payload, 1, 7, "reagent", 5000, 0),
        )
        memory_db.execute(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            (999, "Everlook", 100, now),
        )

    out_file = tmp_path / "xss_test.html"
    html_content = build_standalone_dashboard(memory_db, output_path=str(out_file))

    # Raw unescaped breakout script tag MUST NOT exist in HTML
    assert "</script><script>" not in html_content

    # Safe HTML-entity escaping must be present in tables and data
    assert ("&lt;/script&gt;&lt;script&gt;" in html_content) or ("&lt;script&gt;" in html_content)


def test_build_standalone_dashboard_plot_decimation(memory_db, tmp_path):
    """Verify plot optimization: downsamples price history points to <= 300 per trace (R36)."""
    now = int(time.time())
    with memory_db:
        memory_db.execute(
            """INSERT INTO items (item_id, name, quality, item_class, category, vendor_sell_price, vendor_buy_price)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (888, "High Volume Herb", 1, 7, "herb", 10, 0),
        )
        # Insert 500 price snapshots (> 300 threshold)
        snapshots = [
            (888, "Everlook", 100 + (i % 20), now - (600 - i) * 60)
            for i in range(500)
        ]
        memory_db.executemany(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            snapshots,
        )

    out_file = tmp_path / "plot_decimation.html"
    html_content = build_standalone_dashboard(memory_db, output_path=str(out_file))

    # Extract Plotly traces from rendered HTML
    match = re.search(r'Plotly\.newPlot\([^,]+,\s*(\[\{.*?\}\])\s*,\s*\{', html_content, re.DOTALL)
    assert match is not None, "Plotly.newPlot call not found in generated HTML"
    traces = json.loads(match.group(1))

    herb_traces = [t for t in traces if t.get("name") == "High Volume Herb"]
    assert len(herb_traces) == 1
    # Verify points were downsampled to at most 300
    points_count = len(herb_traces[0].get("x", []))
    assert 0 < points_count <= 300


def test_build_standalone_dashboard_ai_banner_step_inversion_fix(memory_db, tmp_path):
    """Verify AI banner checklist renders action text rather than step numbers (R37)."""
    ai_advice = {
        "summary": "Фокус на ткани",
        "checklist": [
            {"action": "Купить 20 шт Linen Cloth", "step": 1},
            {"action": "Скрафтить Heavy Woolen Cloak", "step": 2},
        ],
    }

    out_file = tmp_path / "ai_step_fix.html"
    html_content = build_standalone_dashboard(
        memory_db,
        output_path=str(out_file),
        ai_advice=ai_advice,
    )

    # Meaningful action text must be rendered inside checklist items
    assert "Купить 20 шт Linen Cloth" in html_content
    assert "Скрафтить Heavy Woolen Cloak" in html_content
    # The step number alone must NOT be the list item content
    assert "<li>1</li>" not in html_content
    assert "<li>2</li>" not in html_content


def test_build_standalone_dashboard_handles_none_name(memory_db, tmp_path, monkeypatch):
    """Verify dashboard generation and alerts gracefully handle items with name=None without AttributeError (R39)."""
    # 1. notify_high_value_arbitrage with item name=None
    qualifying = notify_high_value_arbitrage([{"name": None, "profit_copper": 15000}])
    assert len(qualifying) == 1

    # 2. build_standalone_dashboard with vendor arbitrage item name=None
    monkeypatch.setattr(
        "src.dashboard.get_vendor_arbitrage",
        lambda conn, realm=None: [
            {"name": None, "buyout_price": 100, "vendor_sell_price": 500, "profit_copper": 400, "realm": "Everlook", "quality": 1}
        ],
    )
    # Also test crafting item name=None and anomaly item name=None
    monkeypatch.setattr(
        "src.dashboard.get_crafting_margins",
        lambda conn, realm=None: [
            {
                "crafted_item_name": None,
                "name": None,
                "profession": "Blacksmithing",
                "waylaid_crate_name": None,
                "cost_copper": 100,
                "revenue_copper": 200,
                "profit_copper": 100,
                "roi_percent": 100.0,
                "reagents": [{"name": None, "count": 1}],
            }
        ],
    )
    monkeypatch.setattr(
        "src.dashboard.get_zscore_anomalies",
        lambda conn, realm=None: [
            {
                "name": None,
                "signal": "STRONG_BUY",
                "current_price": 100,
                "mean_price": 200,
                "z_score": -2.0,
                "sample_count": 10,
                "realm": "Everlook",
            }
        ],
    )

    out_file = tmp_path / "none_name_portal.html"
    html_content = build_standalone_dashboard(memory_db, output_path=str(out_file))

    assert out_file.exists()
    assert "Unknown" in html_content
    assert "Recipe" in html_content
    assert "Item" in html_content


# ===========================================================================
# 5. Task 12: UI / UX Enhancements Tests (R42, R43, R44)
# ===========================================================================

def test_dashboard_i18n_switcher_and_attributes(populated_dashboard_db, tmp_path):
    """Verify RU/EN language switcher buttons, setLanguage JS function, and i18n data attributes (R42)."""
    out_file = tmp_path / "i18n_portal.html"
    html_content = build_standalone_dashboard(populated_dashboard_db, output_path=str(out_file))

    # 1. Switcher toggle buttons
    assert 'id="lang-ru"' in html_content
    assert 'id="lang-en"' in html_content
    assert "onclick=\"setLanguage('ru')\"" in html_content
    assert "onclick=\"setLanguage('en')\"" in html_content
    assert 'class="lang-btn active"' in html_content

    # 2. JS function setLanguage presence and implementation
    assert "function setLanguage(lang)" in html_content
    assert "localStorage.getItem('ft_lang')" in html_content
    assert "localStorage.setItem('ft_lang', lang)" in html_content
    assert "document.querySelectorAll('[data-' + lang + ']')" in html_content
    assert "document.querySelectorAll('[data-' + lang + '-placeholder]')" in html_content

    # 3. i18n data attributes on headers and UI elements
    # Brand subtitle
    assert 'data-ru="Автономный аналитический портал и торговый навигатор WoW: Forever"' in html_content
    assert 'data-en="Autonomous analytical portal & market navigator for WoW: Forever"' in html_content

    # Stat pills
    assert 'data-ru="Вендорный арбитраж:"' in html_content
    assert 'data-en="Vendor Arbitrage:"' in html_content

    # Table headers (th elements)
    assert 'data-ru="Предмет" data-en="Item"' in html_content
    assert 'data-ru="Выкуп (AH)" data-en="Buyout (AH)"' in html_content
    assert 'data-ru="Себестоимость" data-en="Craft Cost"' in html_content
    assert 'data-ru="Текущая цена" data-en="Current Price"' in html_content

    # Category filter buttons
    assert 'data-ru="Все" data-en="All"' in html_content
    assert 'data-ru="Сырье" data-en="Trade Goods"' in html_content

    # Search placeholder data attributes
    assert 'data-ru-placeholder="Поиск предмета..."' in html_content
    assert 'data-en-placeholder="Search item..."' in html_content

    # 4. Item name spans in tables: <span class="item-name i18n" data-ru="..." data-en="...">
    item_span_pattern = re.compile(
        r'<span\s+class=[\'"][^\'"]*item-name[^\'"]*i18n[^\'"]*[\'"]\s+data-ru=[\'"][^\'"]+[\'"]\s+data-en=[\'"][^\'"]+[\'"]>'
    )
    matches = item_span_pattern.findall(html_content)
    assert len(matches) >= 3, f"Expected item-name spans with data-ru and data-en in tables, found {len(matches)}"

    # Specific items check
    assert 'data-ru="Golden Diamond"' in html_content
    assert 'data-ru="Heavy Copper Broadsword"' in html_content
    assert 'data-ru="Peacebloom"' in html_content


def test_dashboard_one_click_copy_name_button(populated_dashboard_db, tmp_path):
    """Verify 1-click item name copy button, copyItemName JS logic, and clipboard writeText (R43)."""
    out_file = tmp_path / "copy_portal.html"
    html_content = build_standalone_dashboard(populated_dashboard_db, output_path=str(out_file))

    # 1. Presence of .copy-btn buttons alongside item names in table rows
    assert "class='copy-btn'" in html_content or 'class="copy-btn"' in html_content
    assert "onclick='copyItemName(this, event)'" in html_content or 'onclick="copyItemName(this, event)"' in html_content
    copy_btns_count = html_content.count("copyItemName(this, event)")
    # Must have copy buttons for rows + 1 in the inspector modal
    assert copy_btns_count >= 4

    # 2. Modal header copy button
    assert 'id="modal-copy-btn"' in html_content

    # 3. JS function copyItemName and clipboard API integration
    assert "function copyItemName(btn, event)" in html_content
    assert "navigator.clipboard.writeText(textToCopy)" in html_content
    assert "event.stopPropagation()" in html_content

    # 4. Fallback for non-secure contexts / legacy browsers
    assert "function fallbackCopy(text, cb)" in html_content
    assert "document.execCommand('copy')" in html_content

    # 5. Visual copy feedback and localized text
    assert "btn.classList.add('copied')" in html_content
    assert "✓ Скопировано!" in html_content
    assert "✓ Copied!" in html_content
    assert "setTimeout" in html_content


def test_dashboard_item_inspector_modal_structure(populated_dashboard_db, tmp_path):
    """Verify structure and controls of dedicated item inspector modal (R44)."""
    out_file = tmp_path / "modal_portal.html"
    html_content = build_standalone_dashboard(populated_dashboard_db, output_path=str(out_file))

    # 1. Modal container and backdrop
    assert 'id="item-modal"' in html_content
    assert 'class="modal-backdrop"' in html_content
    assert 'class="modal-content"' in html_content

    # 2. Header and close buttons
    assert 'id="modal-item-name"' in html_content
    assert 'class="modal-close-btn"' in html_content
    assert 'onclick="closeItemModal()"' in html_content

    # 3. Dedicated stat cards in modal
    assert 'id="modal-stat-current"' in html_content
    assert 'id="modal-stat-mean"' in html_content
    assert 'id="modal-stat-min"' in html_content
    assert 'id="modal-stat-max"' in html_content
    assert 'id="modal-stat-zscore"' in html_content

    # 4. Chart container inside modal
    assert 'id="modal-plot-container"' in html_content

    # 5. Row clickability and graph button
    assert "class='clickable-row'" in html_content or 'class="clickable-row"' in html_content
    assert "onRowClick(event," in html_content
    assert "class='graph-btn'" in html_content or 'class="graph-btn"' in html_content
    assert "openItemModal(" in html_content

    # 6. JS functions in script
    assert "function openItemModal(itemId, event)" in html_content
    assert "function closeItemModal()" in html_content
    assert "function onRowClick(event, itemId)" in html_content
    assert "function onModalBackdropClick(event)" in html_content


def test_dashboard_category_filtering_and_item_selector(populated_dashboard_db, tmp_path):
    """Verify category filtering buttons, item dropdown selector, and ITEM_STORE JSON (R44)."""
    out_file = tmp_path / "filters_portal.html"
    html_content = build_standalone_dashboard(populated_dashboard_db, output_path=str(out_file))

    # 1. Category filter buttons
    categories = ["all", "trade_goods", "consumables", "weapons", "armor", "waylaid_crates"]
    for cat in categories:
        assert f'data-cat="{cat}"' in html_content
        assert f"filterCategory('{cat}')" in html_content

    # 2. Search input & dropdown
    assert 'id="item-search-input"' in html_content
    assert 'id="item-select"' in html_content
    assert 'oninput="onItemSearch(this.value)"' in html_content
    assert 'onchange="onSelectDropdownItem(this.value)"' in html_content

    # 3. Dropdown options have necessary metadata
    assert 'data-category=' in html_content
    assert 'data-search=' in html_content

    # 4. JS functions for filtering and selection
    assert "function filterCategory(cat)" in html_content
    assert "function onItemSearch(query)" in html_content
    assert "function applyItemSelectFilter()" in html_content
    assert "function onSelectDropdownItem(val)" in html_content

    # 5. ITEM_STORE embedded data
    match = re.search(r'var ITEM_STORE = (\{.*?\});\n', html_content)
    if not match:
        match = re.search(r'var ITEM_STORE = (\{.*?\});', html_content)
    assert match is not None, "ITEM_STORE JSON variable not found in generated HTML"
    store_json_str = match.group(1)
    store_data = json.loads(store_json_str)

    # Verify keys exist and contain items from test db (101, 102, 201, 202, 401)
    assert "101" in store_data or 101 in store_data
    assert "401" in store_data or 401 in store_data
    peacebloom_entry = store_data.get("401") or store_data.get(401)
    assert peacebloom_entry is not None
    assert "Peacebloom" in peacebloom_entry.get("name_ru", "") or "Peacebloom" in peacebloom_entry.get("name_en", "")
    assert len(peacebloom_entry.get("points", [])) == 16
    assert "z_score" in peacebloom_entry
    assert "current_price" in peacebloom_entry
    assert "mean_price" in peacebloom_entry


def test_dashboard_item_modal_empty_snapshots_state(memory_db, tmp_path, monkeypatch):
    """Verify modal and overview chart handle items with 0 price snapshots gracefully without JS errors (R44)."""
    # Insert an item with 0 price snapshots
    with memory_db:
        memory_db.execute(
            """INSERT INTO items (item_id, name, quality, item_class, category, vendor_sell_price, vendor_buy_price)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (777, "Snapshotless Herb", 1, 7, "herb", 100, 0),
        )

    # Mock signal returning item 777 with no snapshots in price_snapshots table
    monkeypatch.setattr(
        "src.dashboard.get_vendor_arbitrage",
        lambda conn, realm=None: [
            {"item_id": 777, "name": "Snapshotless Herb", "buyout_price": 50, "vendor_sell_price": 100, "profit_copper": 50, "realm": "Everlook", "quality": 1}
        ],
    )

    out_file = tmp_path / "empty_item_portal.html"
    html_content = build_standalone_dashboard(memory_db, output_path=str(out_file))

    # Verify ITEM_STORE has this item with empty points
    match = re.search(r'var ITEM_STORE = (\{.*?\});\n', html_content)
    if not match:
        match = re.search(r'var ITEM_STORE = (\{.*?\});', html_content)
    assert match is not None
    store_data = json.loads(match.group(1))

    item_777 = store_data.get("777") or store_data.get(777)
    assert item_777 is not None
    assert item_777.get("points") == []
    assert item_777.get("current_price") == 0
    assert item_777.get("sample_count") == 0

    # Verify that modal rendering logic includes graceful fallback message for 0 points
    empty_msg_ru = "База данных ожидает первого снимка цен из Auctionator.lua..."
    empty_msg_en = "Waiting for first price snapshot from Auctionator.lua..."
    assert empty_msg_ru in html_content
    assert empty_msg_en in html_content


# ===========================================================================
# 6. Task 17: Dashboard Polish & Auto-refresh (R52)
# ===========================================================================

def test_dashboard_meta_refresh_tag(populated_dashboard_db, tmp_path):
    """Verify presence of <meta http-equiv="refresh" content="30"> tag in <head> (R52)."""
    out_file = tmp_path / "refresh_portal.html"
    html_content = build_standalone_dashboard(populated_dashboard_db, output_path=str(out_file))

    # Read from generated file on disk
    disk_html = out_file.read_text(encoding="utf-8")
    assert disk_html == html_content

    # Check meta tag presence
    assert '<meta http-equiv="refresh" content="30">' in disk_html

    # Check that it is located within the <head> block
    head_match = re.search(r"<head>(.*?)</head>", disk_html, re.DOTALL | re.IGNORECASE)
    assert head_match is not None, "<head> block not found in HTML"
    head_content = head_match.group(1)
    assert '<meta http-equiv="refresh" content="30">' in head_content


def test_dashboard_header_timestamps(populated_dashboard_db, tmp_path):
    """Verify display of generation timestamp and snapshot freshness in dashboard header (R52)."""
    # 1. Populated DB with recent snapshots
    out_file = tmp_path / "timestamps_portal.html"
    html_content = build_standalone_dashboard(populated_dashboard_db, output_path=str(out_file))

    assert "header-timestamps" in html_content
    assert "timestamp-badge timestamp-gen" in html_content
    assert "timestamp-badge timestamp-snap" in html_content

    # Verify generation timestamp format (YYYY-MM-DD HH:MM:SS)
    gen_match = re.search(r"Generated:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", html_content)
    assert gen_match is not None, "Generated timestamp not found or has invalid format"

    # Verify snapshot freshness localized strings
    assert 'data-ru="Актуальность снимка цен:' in html_content
    assert 'data-en="Snapshot freshness:' in html_content
    # Since populated_dashboard_db snapshots are timestamped `now`, freshness should show seconds/minutes
    assert ("сек. назад" in html_content) or ("мин. назад" in html_content) or ("s ago" in html_content) or ("m ago" in html_content)

    # 2. Fresh empty DB with no snapshots
    empty_conn = init_db(":memory:")
    try:
        out_empty = tmp_path / "empty_timestamps_portal.html"
        html_empty = build_standalone_dashboard(empty_conn, output_path=str(out_empty))
        assert ("нет снимков" in html_empty) or ("no snapshots" in html_empty)
    finally:
        empty_conn.close()


def test_dashboard_dual_crafting_display(memory_db, tmp_path):
    """Verify dual crafting display: both Waylaid Crates targets and classic profitable crafts are rendered (R52)."""
    now = int(time.time())
    with memory_db:
        # Items: 1 Waylaid crate pair + 1 classic craft pair
        memory_db.executemany(
            """INSERT INTO items (
                item_id, name, quality, item_class, category,
                vendor_sell_price, vendor_buy_price, target_item_id, target_item_count, reward_copper
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                # Waylaid craft target
                (601, "Waylaid Copper Dagger", 2, 2, "craftable", 150, 0, None, 0, 0),
                (602, "Copper Bar", 1, 7, "raw", 10, 0, None, 0, 0),
                (603, "Waylaid Shipment: Copper Daggers", 2, 0, "waylaid_crate", 0, 0, 601, 1, 4000),
                # Classic profitable craft (Alchemy potion, no Waylaid crate)
                (701, "Elixir of Giant Growth", 2, 0, "consumable", 200, 0, None, 0, 0),
                (702, "Deviate Fish", 1, 7, "raw", 20, 0, None, 0, 0),
                (703, "Earthroot", 1, 7, "herb", 15, 0, None, 0, 0),
            ],
        )

        # Recipes
        memory_db.executemany(
            """INSERT INTO recipes (recipe_id, name, profession, crafted_item_id, yield)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (10, "Waylaid Copper Dagger", "Blacksmithing", 601, 1.0),
                (20, "Elixir of Giant Growth", "Alchemy", 701, 1.0),
            ],
        )

        # Reagents
        memory_db.executemany(
            """INSERT INTO recipe_reagents (recipe_id, reagent_item_id, reagent_id, count, quantity)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (10, 602, 602, 2, 2),  # 2x Copper Bar = 200c cost
                (20, 702, 702, 1, 1),  # 1x Deviate Fish = 100c cost
                (20, 703, 703, 1, 1),  # 1x Earthroot = 100c cost -> total cost 200c
            ],
        )

        # Snapshots: high selling price to guarantee ROI >= 25%
        memory_db.executemany(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            [
                (601, "Everlook", 1000, now),   # Revenue 950c, cost 200c, profit 750c, crate turnin profit 3050c
                (602, "Everlook", 100, now),
                (701, "Everlook", 1200, now),   # Revenue 1140c, cost 200c, profit 940c, ROI 470% (Classic)
                (702, "Everlook", 100, now),
                (703, "Everlook", 100, now),
            ],
        )

    out_file = tmp_path / "dual_craft_portal.html"
    html_content = build_standalone_dashboard(memory_db, output_path=str(out_file), realm="Everlook")

    # 1. Verify table exists
    assert 'id="waylaid-crates"' in html_content

    # 2. Verify BOTH items are present in the HTML table (classic craft is not filtered out)
    assert "Waylaid Copper Dagger" in html_content
    assert "Elixir of Giant Growth" in html_content

    # 3. Verify badges distinguish Waylaid crate craft from classic craft
    assert "badge-waylaid" in html_content
    assert "[Ящик]" in html_content
    assert "badge-craft" in html_content
    assert "[Крафт]" in html_content

    # 4. Verify specific badge assignments per item
    # Waylaid Copper Dagger row must contain badge-waylaid
    dagger_row_match = re.search(r"<tr[^>]*>.*?Waylaid Copper Dagger.*?</tr>", html_content, re.DOTALL)
    assert dagger_row_match is not None, "Waylaid Copper Dagger table row not found"
    assert "badge-waylaid" in dagger_row_match.group(0)

    # Elixir of Giant Growth row must contain badge-craft
    elixir_row_match = re.search(r"<tr[^>]*>.*?Elixir of Giant Growth.*?</tr>", html_content, re.DOTALL)
    assert elixir_row_match is not None, "Elixir of Giant Growth table row not found"
    assert "badge-craft" in elixir_row_match.group(0)


def test_dashboard_parameter_forwarding(memory_db, tmp_path, monkeypatch):
    """Verify build_standalone_dashboard accurately forwards has_bartering, has_master_chef, max_age_hours (R52)."""
    crafting_kwargs = {}
    arbitrage_kwargs = {}
    anomaly_kwargs = {}

    import src.dashboard as dashboard_mod

    orig_get_crafting = dashboard_mod.get_crafting_margins
    orig_get_arbitrage = dashboard_mod.get_vendor_arbitrage
    orig_get_anomalies = dashboard_mod.get_zscore_anomalies

    def mock_crafting_margins(conn, **kwargs):
        crafting_kwargs.update(kwargs)
        return orig_get_crafting(conn, **kwargs)

    def mock_vendor_arbitrage(conn, **kwargs):
        arbitrage_kwargs.update(kwargs)
        return orig_get_arbitrage(conn, **kwargs)

    def mock_zscore_anomalies(conn, **kwargs):
        anomaly_kwargs.update(kwargs)
        return orig_get_anomalies(conn, **kwargs)

    monkeypatch.setattr(dashboard_mod, "get_crafting_margins", mock_crafting_margins)
    monkeypatch.setattr(dashboard_mod, "get_vendor_arbitrage", mock_vendor_arbitrage)
    monkeypatch.setattr(dashboard_mod, "get_zscore_anomalies", mock_zscore_anomalies)

    out_file = tmp_path / "forwarding_portal.html"
    build_standalone_dashboard(
        memory_db,
        output_path=str(out_file),
        realm="Everlook",
        has_bartering=True,
        has_master_chef=True,
        max_age_hours=12.5,
    )

    # Verify get_crafting_margins received all parameters
    assert crafting_kwargs.get("has_bartering") is True
    assert crafting_kwargs.get("has_master_chef") is True
    assert crafting_kwargs.get("max_age_hours") == 12.5
    assert crafting_kwargs.get("realm") == "Everlook"

    # Verify get_vendor_arbitrage received max_age_hours and realm
    assert arbitrage_kwargs.get("max_age_hours") == 12.5
    assert arbitrage_kwargs.get("realm") == "Everlook"

    # Verify get_zscore_anomalies received max_age_hours and realm
    assert anomaly_kwargs.get("max_age_hours") == 12.5
    assert anomaly_kwargs.get("realm") == "Everlook"


def test_dashboard_max_age_hours_functional_filtering(memory_db, tmp_path):
    """Verify max_age_hours functionally excludes stale price snapshots from dashboard table rows (R52)."""
    now = int(time.time())
    stale_ts = now - int(48 * 3600)  # 48 hours ago
    fresh_ts = now - int(10 * 60)    # 10 minutes ago

    with memory_db:
        memory_db.executemany(
            """INSERT INTO items (item_id, name, quality, item_class, category, vendor_sell_price, vendor_buy_price)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                (801, "Stale Arbitrage Item", 1, 7, "raw", 500, 0),
                (802, "Fresh Arbitrage Item", 1, 7, "raw", 500, 0),
            ],
        )
        memory_db.executemany(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            [
                (801, "Everlook", 100, stale_ts),  # Vendor arbitrage 48h old
                (802, "Everlook", 100, fresh_ts),  # Vendor arbitrage 10m old
            ],
        )

    # 1. With max_age_hours=2.0 -> Stale item must be excluded from vendor arbitrage table rows
    out_filtered = tmp_path / "portal_filtered.html"
    html_filtered = build_standalone_dashboard(
        memory_db,
        output_path=str(out_filtered),
        realm="Everlook",
        max_age_hours=2.0,
    )
    # Check vendor arbitrage tbody specifically
    match_filtered = re.search(r'id=["\']vendor-arbitrage["\'].*?<tbody>(.*?)</tbody>', html_filtered, re.DOTALL)
    assert match_filtered is not None, "vendor-arbitrage tbody not found in filtered HTML"
    tbody_filtered = match_filtered.group(1)
    assert "Fresh Arbitrage Item" in tbody_filtered
    assert "Stale Arbitrage Item" not in tbody_filtered

    # 2. With max_age_hours=None -> Both items must appear in vendor arbitrage table rows
    out_all = tmp_path / "portal_all.html"
    html_all = build_standalone_dashboard(
        memory_db,
        output_path=str(out_all),
        realm="Everlook",
        max_age_hours=None,
    )
    match_all = re.search(r'id=["\']vendor-arbitrage["\'].*?<tbody>(.*?)</tbody>', html_all, re.DOTALL)
    assert match_all is not None, "vendor-arbitrage tbody not found in unfiltered HTML"
    tbody_all = match_all.group(1)
    assert "Fresh Arbitrage Item" in tbody_all
    assert "Stale Arbitrage Item" in tbody_all


# ===========================================================================
# 6. Ticket 20 Goblin Terminal UI & Integration Tests
# ===========================================================================

def test_defillama_terminal_shell(populated_dashboard_db, tmp_path):
    out_file = tmp_path / "defillama_terminal_portal.html"
    html = build_standalone_dashboard(
        populated_dashboard_db, output_path=str(out_file), realm="Everlook"
    )

    assert '<div class="terminal-layout app-shell">' in html
    assert '<aside class="terminal-sidebar">' in html
    assert '<div class="terminal-content">' in html
    assert '<header class="terminal-header">' in html
    assert '<nav class="terminal-nav" aria-label="Разделы рынка">' in html
    assert '<div class="summary-cards-row">' in html
    assert '<main class="terminal-main">' in html
    assert '<section class="terminal-left">' in html
    assert '<aside class="terminal-right">' in html

    for token in (
        "--ft-bg: #0b0e14;",
        "--ft-surface: #11161f;",
        "--ft-accent: #6b9dff;",
        "--ft-sidebar-w: 224px;",
    ):
        assert token in html

    layout_match = re.search(r"\.terminal-layout\s*\{([^}]+)\}", html)
    assert layout_match is not None
    assert "height: 100vh;" in layout_match.group(1)
    assert "display: grid;" in layout_match.group(1)
    assert "grid-template-columns: var(--ft-sidebar-w) minmax(0, 1fr);" in layout_match.group(1)

    main_match = re.search(r"\.terminal-main\s*\{([^}]+)\}", html)
    assert main_match is not None
    main_css = main_match.group(1)
    assert "display: grid;" in main_css
    assert "grid-template-columns: minmax(0, 1.65fr) minmax(320px, 0.85fr);" in main_css
    assert "min-height: 0;" in main_css
    assert "overflow: hidden;" in main_css

    # Inspector panel elements inside .terminal-right
    assert 'class="inspector-panel"' in html
    assert 'class="inspector-header"' in html
    assert 'id="inspector-item-name"' in html
    assert 'id="inspector-copy-btn"' in html
    assert 'id="inspector-wowhead-link"' in html
    assert 'id="inspector-chart"' in html
    assert 'class="inspector-chart-container"' in html

    # Deal calculator elements inside inspector
    assert 'id="deal-calculator"' in html
    assert 'id="calc-cost"' in html
    assert 'id="calc-breakeven"' in html
    assert 'id="calc-ah-cut"' in html
    assert 'id="calc-profit"' in html
    assert 'id="calc-roi"' in html


def test_defillama_navigation_and_controls(populated_dashboard_db):
    html = build_standalone_dashboard(populated_dashboard_db, output_path=None, realm="Everlook")

    assert html.count('class="tab-btn') >= 7
    assert 'class="terminal-nav"' in html
    assert 'class="sidebar-brand"' in html
    assert 'aria-hidden="true"' in html
    assert 'id="item-search-input"' in html
    assert 'id="btn-export-shopping-list"' in html
    assert 'onclick="copyAuctionatorShoppingList()"' in html
    assert 'id="lang-ru"' in html
    assert 'id="lang-en"' in html
    assert ".tab-btn:focus-visible" in html
    assert ".tab-btn.active" in html
    assert "btnRu.setAttribute('aria-pressed'" in html
    assert "btnEn.setAttribute('aria-pressed'" in html


def test_defillama_component_styles(populated_dashboard_db):
    html = build_standalone_dashboard(populated_dashboard_db, output_path=None, realm="Everlook")

    for selector in (
        ".summary-card",
        ".terminal-left",
        ".terminal-right",
        ".data-table",
        ".data-table thead",
        ".sortable th",
        ".selected-row",
        ".inspector-panel",
        ".deal-calculator",
        ".empty-state",
        ".toast",
    ):
        assert selector in html

    assert "position: sticky;" in html
    assert "font-variant-numeric: tabular-nums;" in html
    assert "background: var(--ft-accent-soft);" in html
    assert "border: 1px solid var(--ft-border);" in html



def test_wowhead_tooltips_integration(populated_dashboard_db, tmp_path):
    """Verify Wowhead tooltips integration with Forever domain (R20).

    Verifies:
    - Script inclusion: https://wow.zamimg.com/js/tooltips.js
    - whTooltips configuration object with domain: 'forever'
    - Item links in table rows having data-wowhead="domain=forever"
    - Inspector link having data-wowhead="domain=forever"
    - Client-side JS dynamically updates data-wowhead and triggers window.$Wh.updateTooltips()
    """
    out_file = tmp_path / "wowhead_portal.html"
    html_content = build_standalone_dashboard(populated_dashboard_db, output_path=str(out_file), realm="Everlook")

    # 1. Script tag inclusion
    assert '<script src="https://wow.zamimg.com/js/tooltips.js"></script>' in html_content

    # 2. whTooltips configuration object
    wh_config_match = re.search(r"const\s+whTooltips\s*=\s*\{([^}]+)\};", html_content)
    assert wh_config_match is not None, "const whTooltips configuration not found"
    wh_config = wh_config_match.group(1)
    assert "domain: 'forever'" in wh_config or 'domain: "forever"' in wh_config
    assert "colorLinks: true" in wh_config
    assert "iconizeLinks: true" in wh_config

    # 3. Item links in table rows must have data-wowhead="domain=forever"
    # Find all anchor tags pointing to wowhead forever item URLs
    wh_row_links = re.findall(
        r'<a\s+[^>]*href=[\'"]https://www\.wowhead\.com/forever/item=(\d+)[\'"][^>]*data-wowhead=[\'"]domain=forever[\'"][^>]*>',
        html_content,
    )
    assert len(wh_row_links) >= 3, f"Expected at least 3 Wowhead item links with data-wowhead='domain=forever', found {len(wh_row_links)}"
    # Verify specific item IDs from populated DB are present
    assert "101" in wh_row_links  # Golden Diamond
    assert "201" in wh_row_links  # Heavy Copper Broadsword
    assert "401" in wh_row_links  # Peacebloom

    # 4. Inspector panel link has data-wowhead="domain=forever"
    assert 'id="inspector-wowhead-link"' in html_content
    inspector_link_match = re.search(
        r'<a\s+id=[\'"]inspector-wowhead-link[\'"][^>]*data-wowhead=[\'"]domain=forever[\'"][^>]*>',
        html_content,
    )
    assert inspector_link_match is not None, "Inspector wowhead link does not have data-wowhead='domain=forever'"

    # 5. Client-side JS dynamically sets data-wowhead and invokes $Wh.updateTooltips()
    assert "linkEl.setAttribute('data-wowhead', 'domain=forever')" in html_content
    assert "window.$Wh" in html_content
    assert "window.$Wh.updateTooltips()" in html_content


def test_dashboard_copy_name_button_and_toast(populated_dashboard_db, tmp_path):
    """Verify 1-click copy name button (.copy-btn) and copy toast presence (R20).

    Verifies:
    - Presence of .copy-btn buttons in all table rows and inspector panel
    - Toast container #toast with .toast styling
    - Client-side JS showToast() and copyItemName() functions
    - Clipboard writeText API with fallbackCopy support
    """
    out_file = tmp_path / "copy_toast_portal.html"
    html_content = build_standalone_dashboard(populated_dashboard_db, output_path=str(out_file), realm="Everlook")

    # 1. Presence of .copy-btn buttons in table rows
    row_copy_buttons = re.findall(
        r'<button\s+class=[\'"][^\'"]*copy-btn[^\'"]*[\'"][^>]*onclick=[\'"]copyItemName\(this,\s*event\)[\'"][^>]*>',
        html_content,
    )
    assert len(row_copy_buttons) >= 3, f"Expected multiple table row copy buttons, found {len(row_copy_buttons)}"

    # 2. Dedicated inspector copy button
    assert 'id="inspector-copy-btn"' in html_content
    assert 'onclick="copyItemName(this, event)"' in html_content

    # 3. Toast element and CSS rules
    assert '<div id="toast" class="toast"></div>' in html_content
    assert ".toast {" in html_content
    assert ".toast.show {" in html_content
    assert "z-index: 10000;" in html_content

    # 4. JS showToast function implementation
    assert "function showToast(msg)" in html_content
    assert "document.getElementById('toast')" in html_content
    assert "t.classList.add('show')" in html_content
    assert "t.classList.remove('show')" in html_content

    # 5. JS copyItemName function and clipboard API
    assert "function copyItemName(btn, event)" in html_content
    assert "event.stopPropagation()" in html_content
    assert "navigator.clipboard.writeText(textToCopy)" in html_content
    assert "btn.classList.add('copied')" in html_content
    assert "function fallbackCopy(text, cb)" in html_content


def test_auctionator_shopping_list_export_button_and_format(populated_dashboard_db, tmp_path):
    """Verify Auctionator Shopping List export button and ForeverTrade^ format (R20).

    Verifies:
    - Presence of #btn-export-shopping-list button in header actions
    - Button click handler copyAuctionatorShoppingList()
    - JS export formatting: 'ForeverTrade^"item1"^"item2"...' for STRONG_BUY signals
    - ITEM_STORE contains STRONG_BUY anomaly data (Peacebloom)
    - Functional simulation of export string generation
    """
    out_file = tmp_path / "shopping_list_portal.html"
    html_content = build_standalone_dashboard(populated_dashboard_db, output_path=str(out_file), realm="Everlook")

    # 1. Export button presence in header actions
    assert 'id="btn-export-shopping-list"' in html_content
    assert 'class="btn-shopping-list"' in html_content
    assert 'onclick="copyAuctionatorShoppingList()"' in html_content
    assert 'data-ru="📋 Скопировать Shopping List для Auctionator"' in html_content
    assert 'data-en="📋 Copy Auctionator Shopping List"' in html_content

    # 2. JS copyAuctionatorShoppingList function implementation
    assert "function copyAuctionatorShoppingList()" in html_content
    assert "var exportStr = 'ForeverTrade^' + items.map(function(name)" in html_content
    assert 'return \'"\' + name.replace(/"/g, \'\') + \'"\';' in html_content
    assert ".join('^')" in html_content
    assert "copyTextToClipboard(exportStr," in html_content

    # 3. Verify ITEM_STORE has STRONG_BUY items to export
    store_match = re.search(r'var ITEM_STORE = (\{.*?\});', html_content)
    assert store_match is not None, "ITEM_STORE variable not found"
    item_store = json.loads(store_match.group(1))

    # Peacebloom (401) has historical drop in populated_dashboard_db -> must qualify for STRONG_BUY
    peacebloom = item_store.get("401") or item_store.get(401)
    assert peacebloom is not None, "Peacebloom not found in ITEM_STORE"
    z_score = peacebloom.get("z_score", 0.0)
    deal_signal = (peacebloom.get("deal_info") or {}).get("signal", "")
    assert z_score <= -1.8 or "BUY" in deal_signal

    # 4. Functional simulation of export format in Python
    # Filter items matching the client-side logic: z_score <= -1.8 or deal_info signal containing 'BUY'
    export_items = []
    for k, it in item_store.items():
        z = it.get("z_score", 0.0)
        sig = (it.get("deal_info") or {}).get("signal", "")
        if z <= -1.8 or "BUY" in sig:
            name = it.get("name_ru") or it.get("name_en")
            if name and name not in export_items:
                export_items.append(name)

    assert len(export_items) >= 1
    assert "Peacebloom" in export_items

    # Format exactly as the client JS generates: ForeverTrade^"item1"^"item2"
    formatted_export = "ForeverTrade^" + "^".join(f'"{name.replace(chr(34), "")}"' for name in export_items)
    assert formatted_export.startswith("ForeverTrade^")
    assert '"Peacebloom"' in formatted_export
    # Verify caret delimiter separation
    tokens = formatted_export.split("^")
    assert tokens[0] == "ForeverTrade"
    assert '"Peacebloom"' in tokens[1:]


def test_localstorage_persistence_keys(populated_dashboard_db, tmp_path):
    """Verify localStorage persistence for ft_active_tab, ft_search, ft_lang, ft_selected_item (R20).

    Verifies:
    - Saving each key:
      * ft_active_tab in switchTab()
      * ft_search in onItemSearch()
      * ft_lang in setLanguage() / setStoredLang()
      * ft_selected_item in selectInspectorItem()
    - Restoring each key on DOMContentLoaded:
      * ft_active_tab activates the saved tab pane
      * ft_search sets the search input value and filters rows
      * ft_lang sets the active language and updates data-lang elements
      * ft_selected_item highlights and loads the saved item in inspector
    """
    out_file = tmp_path / "storage_portal.html"
    html_content = build_standalone_dashboard(populated_dashboard_db, output_path=str(out_file), realm="Everlook")

    # 1. Saving keys into localStorage
    assert "localStorage.setItem('ft_active_tab', tabId)" in html_content
    assert "localStorage.setItem('ft_search', currentSearchQuery)" in html_content
    assert "localStorage.setItem('ft_lang', lang)" in html_content
    assert "localStorage.setItem('ft_selected_item', String(itemId))" in html_content

    # 2. Reading keys from localStorage
    assert "localStorage.getItem('ft_active_tab')" in html_content
    assert "localStorage.getItem('ft_search')" in html_content
    assert "localStorage.getItem('ft_lang')" in html_content
    assert "localStorage.getItem('ft_selected_item')" in html_content

    # 3. DOMContentLoaded restoration lifecycle
    dom_idx = html_content.find('document.addEventListener("DOMContentLoaded"')
    assert dom_idx != -1, "DOMContentLoaded event listener not found"
    script_end = html_content.find("</script>", dom_idx)
    dom_code = html_content[dom_idx:script_end]

    # Verify all 4 keys are restored inside DOMContentLoaded
    assert "localStorage.getItem('ft_active_tab')" in dom_code
    assert "switchTab(savedTab)" in dom_code
    assert "localStorage.getItem('ft_search')" in dom_code
    assert "onItemSearch(savedSearch)" in dom_code
    assert "getStoredLang()" in dom_code
    assert "setLanguage(initialLang)" in dom_code
    assert "localStorage.getItem('ft_selected_item')" in dom_code
    assert "selectInspectorItem(savedItem)" in dom_code


def test_all_signal_tabs_present_and_configured(populated_dashboard_db, tmp_path):
    """Verify presence and configuration of all signal tabs: Strong Buy, Arbitrage, Craft Dual, Crates, Vendor Shuffle (R20).

    Verifies:
    - All 5 signal tab buttons with data-tab and switchTab() onclick handlers
    - Market Overview chart tab
    - Corresponding tab panes in .tabs-content
    - Tab count badges
    - Sortable tables inside each signal tab pane
    - Client-side switchTab() JS logic
    """
    out_file = tmp_path / "tabs_portal.html"
    html_content = build_standalone_dashboard(populated_dashboard_db, output_path=str(out_file), realm="Everlook")

    # 1. Verify 5 signal tabs + 1 chart tab buttons exist in .tabs-bar
    expected_tabs = [
        ("price-anomalies", "Strong Buy"),
        ("vendor-arbitrage", "Арбитраж"),
        ("waylaid-crates", "Крафт (Dual)"),
        ("tab-crates", "Ящики"),
        ("tab-shuffle", "Вендор-шаффл"),
        ("charts-section", "Обзор рынка"),
    ]

    for tab_id, label_fragment in expected_tabs:
        # Button check
        btn_pattern = rf'<button\s+class=[\'"][^\'"]*tab-btn[^\'"]*[\'"][^>]*data-tab=[\'"]{tab_id}[\'"][^>]*onclick=[\'"]switchTab\([\'"]{tab_id}[\'"]\)[\'"][^>]*>'
        assert re.search(btn_pattern, html_content) is not None, f"Tab button for '{tab_id}' not found"

        # Tab pane check
        pane_pattern = rf'<div\s+class=[\'"][^\'"]*tab-pane[^\'"]*[\'"][^>]*id=[\'"]{tab_id}[\'"]'
        assert re.search(pane_pattern, html_content) is not None, f"Tab pane for '{tab_id}' not found"

    # 2. Check Strong Buy is the active default tab
    assert 'class="tab-btn active" data-tab="price-anomalies"' in html_content
    assert 'class="tab-pane active" id="price-anomalies"' in html_content

    # 3. Check count badges exist on signal tabs
    assert 'id="strong-buy-count"' in html_content
    tab_count_matches = re.findall(r'<span\s+class=[\'"][^\'"]*tab-count[^\'"]*[\'"]', html_content)
    assert len(tab_count_matches) >= 5, f"Expected at least 5 tab-count badges, found {len(tab_count_matches)}"

    # 4. Check sortable tables inside each signal pane
    signal_panes = ["price-anomalies", "vendor-arbitrage", "waylaid-crates", "tab-crates", "tab-shuffle"]
    for pane_id in signal_panes:
        pane_match = re.search(rf'<div\s+class=[\'"][^\'"]*tab-pane[^\'"]*[\'"][^>]*id=[\'"]{pane_id}[\'"][^>]*>(.*?)</div>\s*(?:<!--|<div|\s*</section>)', html_content, re.DOTALL)
        assert pane_match is not None, f"Pane content for '{pane_id}' not found"
        pane_html = pane_match.group(1)
        assert '<table class="sortable">' in pane_html, f"Sortable table not found in pane '{pane_id}'"
        assert '<thead>' in pane_html, f"thead not found in pane '{pane_id}'"
        assert '<tbody>' in pane_html, f"tbody not found in pane '{pane_id}'"

    # 5. Check client-side switchTab JS implementation
    assert "function switchTab(tabId)" in html_content
    assert "btn.classList.toggle('active', btn.getAttribute('data-tab') === tabId)" in html_content
    assert "pane.classList.toggle('active', pane.id === tabId)" in html_content


def test_top_snipe_card_summary_uses_expected_net_profit(memory_db, tmp_path):
    """Verify Top Snipe card displays expected_net_profit (+22c) and not unadjusted markup (+37c) (Audit §12)."""
    fake_anomaly = [{
        "item_id": 5001,
        "name": "Audit Snipe Target",
        "signal": "STRONG_BUY",
        "current_price": 50,
        "mean_price": 91.67,
        "deposit": 15,
        "expected_net_profit": 22,
        "z_score": -2.1,
        "sample_count": 10,
        "realm": "Everlook",
    }]

    out_file = tmp_path / "top_snipe_dashboard.html"
    with patch("src.dashboard.get_zscore_anomalies", return_value=fake_anomaly), \
         patch("src.dashboard.get_vendor_arbitrage", return_value=[]), \
         patch("src.dashboard.get_crafting_margins", return_value=[]):
        html_content = build_standalone_dashboard(
            memory_db,
            output_path=str(out_file),
            realm="Everlook",
        )

    assert "ТОП СНАЙП (Флип)" in html_content
    assert "Audit Snipe Target" in html_content
    assert "+22c" in html_content or f"+{format_currency(22)}" in html_content
    assert "+37c" not in html_content


def test_anomalies_only_strong_sell_shows_empty_state_in_strong_buy_table(memory_db, tmp_path):
    """Verify that when anomalies only contains STRONG_SELL, Strong Buy table shows empty state and no sell rows (Audit §13)."""
    sell_only_anomalies = [{
        "item_id": 5002,
        "name": "Overpriced Herb",
        "signal": "STRONG_SELL",
        "current_price": 5000,
        "mean_price": 1000.0,
        "deposit": 20,
        "expected_net_profit": 0,
        "z_score": 3.5,
        "sample_count": 12,
        "realm": "Everlook",
    }]

    out_file = tmp_path / "strong_sell_dashboard.html"
    with patch("src.dashboard.get_zscore_anomalies", return_value=sell_only_anomalies), \
         patch("src.dashboard.get_vendor_arbitrage", return_value=[]), \
         patch("src.dashboard.get_crafting_margins", return_value=[]):
        html_content = build_standalone_dashboard(
            memory_db,
            output_path=str(out_file),
            realm="Everlook",
        )

    # Strong buy count badge should be 0
    assert '<span class="tab-count" id="strong-buy-count">0</span>' in html_content

    # Extract price-anomalies pane tbody
    pane_match = re.search(
        r'<div\s+class=[\'"][^\'"]*tab-pane[^\'"]*[\'"][^>]*id=[\'"]price-anomalies[\'"][^>]*>.*?<tbody>(.*?)</tbody>',
        html_content,
        re.DOTALL,
    )
    assert pane_match is not None, "price-anomalies tbody not found"
    tbody_html = pane_match.group(1)

    assert "empty-state" in tbody_html
    assert "Аномальных сбросов или всплесков цен не обнаружено" in tbody_html
    assert "STRONG_SELL" not in tbody_html
    assert "Overpriced Herb" not in tbody_html


def test_generate_dashboard_html_embeds_strong_buy_item_ids_and_script_resolves(memory_db, tmp_path):
    """Verify generate_dashboard_html embeds STRONG_BUY_ITEM_IDS and client script resolves via it (Audit §14)."""
    generate_dashboard_html = build_standalone_dashboard

    strong_buys = [
        {
            "item_id": 7001,
            "name": "Snipe Target Alpha",
            "signal": "STRONG_BUY",
            "current_price": 100,
            "mean_price": 300.0,
            "deposit": 10,
            "expected_net_profit": 175,
            "z_score": -2.2,
            "sample_count": 10,
            "realm": "Everlook",
        },
        {
            "item_id": 7002,
            "name": "Snipe Target Beta",
            "signal": "STRONG_BUY",
            "current_price": 250,
            "mean_price": 700.0,
            "deposit": 25,
            "expected_net_profit": 390,
            "z_score": -2.4,
            "sample_count": 8,
            "realm": "Everlook",
        },
    ]

    out_file = tmp_path / "strong_buy_ids_dashboard.html"
    with patch("src.dashboard.get_zscore_anomalies", return_value=strong_buys), \
         patch("src.dashboard.get_vendor_arbitrage", return_value=[]), \
         patch("src.dashboard.get_crafting_margins", return_value=[]):
        html_content = generate_dashboard_html(
            memory_db,
            output_path=str(out_file),
            realm="Everlook",
        )

    # 1. Verify const STRONG_BUY_ITEM_IDS is embedded with verified IDs [7001, 7002]
    ids_match = re.search(r'const STRONG_BUY_ITEM_IDS = (\[.*?\]);', html_content)
    assert ids_match is not None, "STRONG_BUY_ITEM_IDS variable not found in script"
    parsed_ids = json.loads(ids_match.group(1))
    assert parsed_ids == [7001, 7002]

    # 2. Verify client-side script uses STRONG_BUY_ITEM_IDS to resolve items from ITEM_STORE
    assert "if (typeof STRONG_BUY_ITEM_IDS !== 'undefined' && STRONG_BUY_ITEM_IDS.length > 0)" in html_content
    assert "STRONG_BUY_ITEM_IDS.forEach(function(id)" in html_content
    assert "var it = ITEM_STORE[id];" in html_content


def test_waylaid_tab_renders_crate_turnin_economics_not_single_item_craft(memory_db, tmp_path):
    """Verify Waylaid tab (tab-crates) renders crate turn-in economics rather than 1-item AH crafting (Audit §15)."""
    dual_crafting_data = [
        {
            "crafted_item_id": 8001,
            "crafted_item_name": "Rough Sharpening Stone",
            "name": "Rough Sharpening Stone",
            "profession": "Blacksmithing",
            "is_waylaid_target": True,
            "waylaid_crate_name": "Waylaid Shipment: Rough Sharpening Stones",
            # 1-item AH crafting economics
            "cost_copper": 150,
            "revenue_copper": 400,
            "profit_copper": 250,
            "roi_percent": 166.7,
            # Full crate turn-in economics (pack of 4 items turned into NPC for fixed reward)
            "pack_cost_copper": 600,
            "pack_craft_cost_copper": 600,
            "reward_copper": 1500,
            "craft_turnin_profit_copper": 900,
            "turnin_profit_copper": 900,
        }
    ]

    out_file = tmp_path / "waylaid_turnin_dashboard.html"
    with patch("src.dashboard.get_crafting_margins", return_value=dual_crafting_data), \
         patch("src.dashboard.get_vendor_arbitrage", return_value=[]), \
         patch("src.dashboard.get_zscore_anomalies", return_value=[]):
        html_content = build_standalone_dashboard(
            memory_db,
            output_path=str(out_file),
            realm="Everlook",
        )

    # Extract Dual tab (waylaid-crates)
    dual_match = re.search(
        r'<div\s+class=[\'"][^\'"]*tab-pane[^\'"]*[\'"][^>]*id=[\'"]waylaid-crates[\'"][^>]*>.*?<tbody>(.*?)</tbody>',
        html_content,
        re.DOTALL,
    )
    assert dual_match is not None
    dual_tbody = dual_match.group(1)

    # Extract Waylaid Crates Only tab (tab-crates)
    crate_match = re.search(
        r'<div\s+class=[\'"][^\'"]*tab-pane[^\'"]*[\'"][^>]*id=[\'"]tab-crates[\'"][^>]*>.*?<tbody>(.*?)</tbody>',
        html_content,
        re.DOTALL,
    )
    assert crate_match is not None
    crate_tbody = crate_match.group(1)

    # Dual tab renders 1-item AH crafting profit (250c) and cost (150c)
    assert "data-value='250'" in dual_tbody
    assert "data-value='150'" in dual_tbody

    # Waylaid tab (tab-crates) renders crate turn-in economics:
    # Cost = pack_craft_cost_copper (600c), Revenue = reward_copper (1500c), Profit = craft_turnin_profit_copper (900c)
    assert "data-value='600'" in crate_tbody
    assert "data-value='1500'" in crate_tbody
    assert "data-value='900'" in crate_tbody
    # Must NOT render 1-item AH profit (250c) in crate turn-in tab
    assert "data-value='250'" not in crate_tbody


def test_dashboard_disenchant_tab_and_inspector_integration(memory_db, tmp_path):
    """Verify generate_dashboard_html renders disenchant tab, inspector card and transmits data to client script (Ticket 03)."""
    # 1. Setup catalog items
    _CATALOG_CACHE[9201] = {
        "item_id": 9201,
        "name": "Glowing Green Blade",
        "name_ru": "Светящийся зеленый клинок",
        "quality": 2,
        "item_class": 2,  # Weapon
        "item_level": 22,
        "category": "weapon",
    }
    _CATALOG_CACHE[9202] = {
        "item_id": 9202,
        "name": "Bulwark of Ancient Kings",
        "name_ru": "Оплот древних королей",
        "quality": 3,
        "item_class": 4,  # Armor / Shield
        "item_level": 35,
        "category": "armor",
    }

    now = int(time.time())
    with memory_db:
        # Insert equipment items and enchanting reagents into SQLite
        memory_db.executemany(
            """INSERT INTO items (item_id, name, name_ru, quality, item_class, category, vendor_sell_price)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                (9201, "Glowing Green Blade", "Светящийся зеленый клинок", 2, 2, "weapon", 50),
                (9202, "Bulwark of Ancient Kings", "Оплот древних королей", 3, 4, "armor", 200),
                (10940, "Strange Dust", "Странная пыль", 1, 7, "trade_goods", 4),
                (10939, "Greater Magic Essence", "Большая магическая эссенция", 2, 7, "trade_goods", 45),
                (11138, "Small Radiant Shard", "Малый светящийся осколок", 3, 7, "trade_goods", 500),
            ]
        )
        memory_db.executemany(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            [
                (10940, "Everlook", 200, now),
                (10939, "Everlook", 1000, now),
                (11138, "Everlook", 5000, now),
                (9201, "Everlook", 100, now),
                (9202, "Everlook", 1000, now),
            ]
        )

    # 2. Call generate_dashboard_html()
    out_file = tmp_path / "portal_de_test.html"
    html_content = generate_dashboard_html(
        memory_db,
        output_path=str(out_file),
        realm="Everlook",
    )

    # 3. Verify HTML output file is generated
    assert out_file.is_file(), "portal HTML file was not written to output_path"
    assert len(html_content) > 1000

    # 4. Verify Disenchant Tab existence in navigation
    assert 'data-tab="tab-disenchant"' in html_content
    assert "tab-disenchant" in html_content
    assert "Дизэнчант" in html_content

    # Verify tab-pane container
    assert '<div class="tab-pane" id="tab-disenchant">' in html_content

    # Extract disenchant table rows
    de_match = re.search(
        r'<div\s+class=[\'"][^\'"]*tab-pane[^\'"]*[\'"][^>]*id=[\'"]tab-disenchant[\'"][^>]*>.*?<tbody>(.*?)</tbody>',
        html_content,
        re.DOTALL,
    )
    assert de_match is not None, "tab-disenchant tbody was not found in generated HTML"
    de_tbody = de_match.group(1)

    # Verify rows for both items rendered in table
    assert "data-item-id='9202'" in de_tbody
    assert "data-item-id='9201'" in de_tbody
    assert "data-is-de='1'" in de_tbody
    assert "Bulwark of Ancient Kings" in de_tbody or "Оплот древних королей" in de_tbody
    assert "Glowing Green Blade" in de_tbody or "Светящийся зеленый клинок" in de_tbody
    assert "reagent-summary" in de_tbody

    # 5. Verify Disenchant Card in Inspector layout
    assert 'id="disenchant-card"' in html_content
    assert 'id="de-card-expected-val"' in html_content
    assert 'id="de-reagents-list"' in html_content
    assert "Карточка распыления (Disenchant)" in html_content

    # 6. Verify client script data transmission (ITEM_STORE JSON)
    store_match = re.search(r'var ITEM_STORE = (\{.*?\});\s*(?:var|const|\n)', html_content, re.DOTALL)
    assert store_match is not None, "ITEM_STORE JSON was not serialized into client script"
    item_store = json.loads(store_match.group(1))

    # Verify item 9202 (Shield) in ITEM_STORE
    assert "9202" in item_store, "Item 9202 missing from client ITEM_STORE"
    item_9202 = item_store["9202"]
    assert "deal_info" in item_9202
    deal_9202 = item_9202["deal_info"]
    assert deal_9202.get("is_disenchant") is True
    assert deal_9202.get("expected_de_value") == 5000.0
    assert deal_9202.get("buyout") == 1000
    assert deal_9202.get("profit") == 3750
    assert deal_9202.get("roi") == 375.0
    assert "reagents" in deal_9202 and len(deal_9202["reagents"]) > 0
    assert deal_9202["reagents"][0]["reagent_id"] == 11138

    # Verify item 9201 (Sword) in ITEM_STORE
    assert "9201" in item_store, "Item 9201 missing from client ITEM_STORE"
    deal_9201 = item_store["9201"]["deal_info"]
    assert deal_9201.get("is_disenchant") is True
    assert deal_9201.get("expected_de_value") == 1275.0
    assert deal_9201.get("buyout") == 100
    assert deal_9201.get("roi") == 1111.25

    # Verify client JavaScript dynamic update handlers for disenchant card
    assert "var deCard = document.getElementById('disenchant-card');" in html_content
    assert "deal.is_disenchant || deal.expected_de_value !== undefined" in html_content
    assert "deCard.style.display = 'flex';" in html_content


def test_dashboard_crafting_dag_tree_integration(memory_db, tmp_path):
    """Verify generate_dashboard_html integrates DAG crafting tree in Inspector and client script (Ticket 05).

    Verifies:
      1. Inspection panel layout contains #crafting-tree and #tab-crafting container.
      2. ITEM_STORE client dataset transmits reagents_tree structure with has_subcraft flag.
      3. JavaScript includes renderCraftingTree() function, dynamic tree population logic,
         and badges [🔨 Craft], [🏛️ Vendor], [🛒 AH].
    """
    # 1. Setup catalog items cache
    _CATALOG_CACHE[2320] = {
        "item_id": 2320,
        "name": "Coarse Thread",
        "name_ru": "Грубая нить",
        "quality": 1,
        "item_class": 7,
        "category": "reagent",
    }
    _CATALOG_CACHE[2589] = {
        "item_id": 2589,
        "name": "Linen Cloth",
        "name_ru": "Льняная ткань",
        "quality": 1,
        "item_class": 7,
        "category": "reagent",
    }
    _CATALOG_CACHE[2996] = {
        "item_id": 2996,
        "name": "Bolt of Linen Cloth",
        "name_ru": "Рулон льняной ткани",
        "quality": 1,
        "item_class": 7,
        "category": "trade_goods",
    }
    _CATALOG_CACHE[4238] = {
        "item_id": 4238,
        "name": "Linen Bag",
        "name_ru": "Льняная сумка",
        "quality": 1,
        "item_class": 1,
        "category": "container",
    }

    now = int(time.time())
    with memory_db:
        # Insert items
        memory_db.executemany(
            """INSERT INTO items (
                item_id, name, name_ru, quality, item_class, category,
                vendor_sell_price, vendor_buy_copper
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (2320, "Coarse Thread", "Грубая нить", 1, 7, "reagent", 2, 10),
                (2589, "Linen Cloth", "Льняная ткань", 1, 7, "reagent", 5, 0),
                (2996, "Bolt of Linen Cloth", "Рулон льняной ткани", 1, 7, "trade_goods", 10, 0),
                (4238, "Linen Bag", "Льняная сумка", 1, 1, "container", 50, 0),
            ],
        )

        # Insert recipes: Linen Cloth -> Bolt, and Bolt + Thread -> Linen Bag
        memory_db.executemany(
            """INSERT INTO recipes (recipe_id, name, crafted_item_id, yield, profession)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (3001, "Bolt of Linen Cloth", 2996, 1.0, "Tailoring"),
                (3002, "Linen Bag", 4238, 1.0, "Tailoring"),
            ],
        )
        memory_db.executemany(
            """INSERT INTO recipe_reagents (recipe_id, reagent_item_id, reagent_id, count, quantity)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (3001, 2589, 2589, 2, 2),
                (3002, 2996, 2996, 3, 3),
                (3002, 2320, 2320, 1, 1),
            ],
        )

        # Insert price snapshots
        memory_db.executemany(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (?, ?, ?, ?)""",
            [
                (2589, "Everlook", 10, now),
                (2996, "Everlook", 50, now),
                (4238, "Everlook", 200, now),
            ],
        )

    # 2. Invoke generate_dashboard_html()
    out_file = tmp_path / "portal_crafting_dag_test.html"
    html_content = generate_dashboard_html(
        memory_db,
        output_path=str(out_file),
        realm="Everlook",
    )

    # 3. Verify file generation
    assert out_file.is_file(), "Generated dashboard HTML file must exist on disk"
    assert len(html_content) > 1000

    # 4. Verify inspector DOM markup for crafting tree
    assert 'id="crafting-tree"' in html_content
    assert 'class="crafting-tree-list"' in html_content
    assert 'id="tab-crafting"' in html_content
    assert "crafting-tree-card" in html_content
    assert "Дерево компонентов (DAG Craft)" in html_content or "Crafting Tree (DAG Craft)" in html_content

    # 5. Verify client script data transmission (ITEM_STORE JSON)
    store_match = re.search(r'var ITEM_STORE = (\{.*?\});\s*(?:var|const|\n)', html_content, re.DOTALL)
    assert store_match is not None, "ITEM_STORE JSON was not serialized into client script"
    item_store = json.loads(store_match.group(1))

    # Verify Linen Bag (item 4238) contains reagents_tree in deal_info
    assert "4238" in item_store, "Item 4238 missing from client ITEM_STORE"
    bag_item = item_store["4238"]
    assert "deal_info" in bag_item
    deal = bag_item["deal_info"]
    assert deal.get("has_subcraft") is True
    assert "reagents_tree" in deal
    reagents_tree = deal["reagents_tree"]
    assert len(reagents_tree) == 2

    # Verify Bolt node in reagents_tree has sub_tree
    bolt_node = next((n for n in reagents_tree if n["item_id"] == 2996), None)
    assert bolt_node is not None, "Bolt reagent node missing from reagents_tree"
    assert bolt_node["source"] == "craft"
    assert bolt_node["effective_unit_cost"] == 20
    assert len(bolt_node["sub_tree"]) == 1
    assert bolt_node["sub_tree"][0]["item_id"] == 2589
    assert bolt_node["sub_tree"][0]["source"] == "ah"

    # 6. Verify client JavaScript implementation of renderCraftingTree
    assert "function renderCraftingTree(nodes, depth, lang)" in html_content
    assert "badge-craft" in html_content
    assert "badge-vendor" in html_content
    assert "badge-ah" in html_content
    assert "treeContainer.innerHTML = renderCraftingTree(" in html_content


def test_defillama_plotly_palette(populated_dashboard_db):
    html = build_standalone_dashboard(populated_dashboard_db, output_path=None, realm="Everlook")

    assert "paper_bgcolor: '#11161f'" in html
    assert "plot_bgcolor: '#11161f'" in html
    assert "font: { color: '#8b98a9'" in html
    assert "line: { color: '#6b9dff', width: 2 }" in html
    assert "line: { color: '#f85149', width: 1.5, dash: 'dot' }" in html
    assert "line: { color: '#d29922', width: 1.5, dash: 'dash' }" in html


def test_defillama_responsive_and_accessibility(populated_dashboard_db):
    html = build_standalone_dashboard(populated_dashboard_db, output_path=None, realm="Everlook")

    assert "@media (max-width: 1279px)" in html
    assert "@media (max-width: 1023px)" in html
    assert "@media (max-width: 767px)" in html
    assert "@media (prefers-reduced-motion: reduce)" in html
    assert "grid-template-columns: 72px minmax(0, 1fr);" in html
    assert "grid-template-columns: 1fr;" in html
    assert "overflow-x: auto;" in html
    assert "outline: 2px solid var(--ft-focus);" in html
    assert 'header.setAttribute("tabindex", "0")' in html
    assert 'header.addEventListener("keydown"' in html
    assert 'header.click();' in html


def test_dashboard_stock_exchange_chart_controls_and_layout(populated_dashboard_db):
    """Verify build_standalone_dashboard generates stock exchange controls, timeframe buttons, yaxis2, and Russian labels."""
    html = build_standalone_dashboard(populated_dashboard_db, output_path=None, realm="Everlook")

    # 1. Chart controls container and mode toggle buttons (candles vs line)
    assert 'class="chart-controls"' in html
    assert ('class="chart-control-group chart-mode-group"' in html or 'ft-chart-mode-btn' in html)
    assert 'data-mode="candles"' in html
    assert 'data-mode="line"' in html

    # 2. Timeframe buttons (24h, 3d, 7d, all)
    assert ('class="chart-control-group chart-tf-group"' in html or 'ft-tf-btn' in html)
    assert 'data-tf="24h"' in html
    assert 'data-tf="3d"' in html
    assert 'data-tf="7d"' in html
    assert 'data-tf="all"' in html

    # 3. Secondary volume axis (yaxis2)
    assert "yaxis2:" in html
    assert "overlaying: 'y'" in html
    assert "side: 'right'" in html

    # 4. Localized Russian labels for chart controls
    assert 'data-ru="Свечи"' in html
    assert 'data-ru="Линия"' in html
    assert 'data-ru="24ч"' in html
    assert 'data-ru="3д"' in html
    assert 'data-ru="7д"' in html
    assert 'data-ru="Все"' in html

    # 5. Client JavaScript functions for chart interaction
    assert "function setChartMode(mode)" in html
    assert "function setChartTimeframe(tf)" in html
    assert "function updateChartControlsUI()" in html


def test_build_price_history_chart_and_inspector_data_points(memory_db):
    """Verify _build_price_history_chart and _build_market_inspector_data function cleanly with 0, 1, and multiple points."""
    assert _build_market_inspector_data is not None, "_build_market_inspector_data function must exist"

    # -------------------------------------------------------------
    # Case A: 0 points (empty database)
    # -------------------------------------------------------------
    chart_0 = _build_price_history_chart(memory_db, realm="Everlook")
    assert isinstance(chart_0, str)
    assert len(chart_0) > 0
    # Empty chart renders localized fallback annotation
    assert ("Нет данных снимков цен" in chart_0 or "Waiting for first" in chart_0)

    data_0 = _build_market_inspector_data(memory_db, realm="Everlook")
    assert isinstance(data_0, dict)
    for it in data_0.values():
        assert it["sample_count"] == 0
        assert it["points"] == []
        assert it["mean_price"] == 0
        assert it["z_score"] == 0.0

    # -------------------------------------------------------------
    # Case B: 1 point (single price snapshot)
    # -------------------------------------------------------------
    with memory_db:
        memory_db.execute(
            """INSERT INTO items (item_id, name, name_ru, quality, item_class, category, vendor_sell_price)
               VALUES (2447, 'Peacebloom', 'Мироцвет', 1, 7, 'herb', 10)"""
        )
        memory_db.execute(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (2447, 'Everlook', 150, 1000)"""
        )

    chart_1 = _build_price_history_chart(memory_db, realm="Everlook")
    assert isinstance(chart_1, str)
    assert "plotly" in chart_1.lower()
    assert "Peacebloom" in chart_1

    data_1 = _build_market_inspector_data(memory_db, realm="Everlook")
    assert 2447 in data_1
    it_1 = data_1[2447]
    assert it_1["sample_count"] == 1
    assert len(it_1["points"]) == 1
    assert it_1["points"][0] == [1000, 150]
    assert it_1["current_price"] == 150
    assert it_1["mean_price"] == 150.0
    assert it_1["min_price"] == 150
    assert it_1["max_price"] == 150
    assert it_1["z_score"] == 0.0  # single point -> no variance -> z_score 0.0

    # -------------------------------------------------------------
    # Case C: Multiple points (5 price snapshots with variance)
    # -------------------------------------------------------------
    with memory_db:
        memory_db.executemany(
            """INSERT INTO price_snapshots (item_id, realm, price, timestamp)
               VALUES (2447, 'Everlook', ?, ?)""",
            [
                (160, 2000),
                (140, 3000),
                (180, 4000),
                (200, 5000),
            ]
        )

    chart_multi = _build_price_history_chart(memory_db, realm="Everlook")
    assert isinstance(chart_multi, str)
    assert "Peacebloom" in chart_multi
    assert "plotly" in chart_multi.lower()

    data_multi = _build_market_inspector_data(memory_db, realm="Everlook")
    assert 2447 in data_multi
    it_multi = data_multi[2447]
    assert it_multi["sample_count"] == 5
    assert len(it_multi["points"]) == 5
    assert it_multi["current_price"] == 200
    assert it_multi["min_price"] == 140
    assert it_multi["max_price"] == 200
    expected_mean = round((150 + 160 + 140 + 180 + 200) / 5, 2)
    assert it_multi["mean_price"] == expected_mean
    assert isinstance(it_multi["z_score"], float)
    assert it_multi["z_score"] > 0.0  # current price (200) > mean (166) -> positive z-score









