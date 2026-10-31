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
import re
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

