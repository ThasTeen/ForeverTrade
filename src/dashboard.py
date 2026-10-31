"""Standalone HTML dashboard generator and arbitrage alert notifier for WoW: Forever."""

import datetime
import html
import logging
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any, Optional, Union

import plotly.graph_objects as go

try:
    from src.analytics import (
        get_crafting_margins,
        get_vendor_arbitrage,
        get_zscore_anomalies,
    )
except ImportError:
    from analytics import (
        get_crafting_margins,
        get_vendor_arbitrage,
        get_zscore_anomalies,
    )

logger = logging.getLogger(__name__)


def format_currency(copper: Any, as_html: bool = False) -> str:
    """Format an amount of copper into WoW currency (gold, silver, copper).

    1g = 100s = 10000c.
    If as_html is False: returns "1g 23s 45c".
    If as_html is True: returns styled spans:
        '<span class="money-gold">1g</span> <span class="money-silver">23s</span> <span class="money-copper">45c</span>'
    """
    if isinstance(copper, bool):
        return "0c"
    if copper is None:
        copper = 0
    else:
        try:
            copper = int(round(float(copper)))
        except (ValueError, TypeError, OverflowError):
            copper = 0

    sign = "-" if copper < 0 else ""
    abs_copper = abs(copper)

    gold = abs_copper // 10000
    silver = (abs_copper % 10000) // 100
    rem_copper = abs_copper % 100

    if as_html:
        return (
            f'{sign}<span class="money-gold">{gold}g</span> '
            f'<span class="money-silver">{silver}s</span> '
            f'<span class="money-copper">{rem_copper}c</span>'
        )
    return f"{sign}{gold}g {silver}s {rem_copper}c"


def notify_high_value_arbitrage(
    arbitrage_items: list[dict],
    threshold_copper: int = 10000
) -> list[dict]:
    """Filter arbitrage items where net profit >= threshold_copper (default: 1g / 10,000c).

    Emits terminal bell / alert log and returns qualifying arbitrage items.
    """
    qualifying = []
    for item in arbitrage_items:
        profit = item.get("profit_copper")
        if profit is None:
            profit = item.get("profit", 0)
        if isinstance(profit, bool):
            profit = 0
        try:
            profit_val = int(round(float(profit)))
        except (ValueError, TypeError, OverflowError):
            profit_val = 0

        if profit_val >= threshold_copper:
            qualifying.append(item)

    if qualifying:
        try:
            sys.stdout.write("\a")
            sys.stdout.flush()
        except Exception:
            pass

        logger.warning(
            "HIGH VALUE ARBITRAGE ALERT: Found %d item(s) exceeding %s profit threshold!",
            len(qualifying),
            format_currency(threshold_copper),
        )
        for it in qualifying:
            name = str(it.get("name") or "Unknown item")
            p = it.get("profit_copper")
            if p is None:
                p = it.get("profit", 0)
            logger.info("  * [ARBITRAGE] %s -> Profit: %s", name, format_currency(p))

    return qualifying


def _build_price_history_chart(conn: sqlite3.Connection, realm: Optional[str] = None) -> str:
    """Generate Plotly interactive chart HTML for key raw materials and consumables."""
    fig = go.Figure()

    cursor = conn.cursor()
    try:
        # Select top-8 most active prioritized items
        cursor.execute(
            """
            SELECT s.item_id
            FROM price_snapshots s
            JOIN items i ON s.item_id = i.item_id
            LEFT JOIN recipe_reagents rr ON s.item_id = rr.reagent_item_id
            WHERE (? IS NULL OR s.realm = ?)
            GROUP BY s.item_id
            ORDER BY 
                CASE WHEN rr.reagent_item_id IS NOT NULL 
                       OR i.item_class IN (0, 7) 
                       OR i.category IN ('reagent', 'raw', 'consumable', 'trade_goods', 'herb', 'ore', 'cloth') 
                     THEN 1 ELSE 0 END DESC,
                COUNT(s.price) DESC
            LIMIT 8
            """,
            (realm, realm),
        )
        top_ids = [r[0] for r in cursor.fetchall()]
    except Exception:
        try:
            cursor.execute(
                """
                SELECT s.item_id
                FROM price_snapshots s
                JOIN items i ON s.item_id = i.item_id
                WHERE (? IS NULL OR s.realm = ?)
                GROUP BY s.item_id
                ORDER BY 
                    CASE WHEN i.item_class IN (0, 7) 
                           OR i.category IN ('reagent', 'raw', 'consumable', 'trade_goods', 'herb', 'ore', 'cloth') 
                         THEN 1 ELSE 0 END DESC,
                    COUNT(s.price) DESC
                LIMIT 8
                """,
                (realm, realm),
            )
            top_ids = [r[0] for r in cursor.fetchall()]
        except Exception as e:
            logger.warning("Could not query top items for chart: %s", e)
            top_ids = []

    rows = []
    if top_ids:
        try:
            placeholders = ",".join("?" for _ in top_ids)
            params = [realm, realm] + top_ids
            cursor.execute(
                f"""
                SELECT s.item_id, i.name, s.timestamp, s.price
                FROM price_snapshots s
                JOIN items i ON s.item_id = i.item_id
                WHERE (? IS NULL OR s.realm = ?)
                  AND s.item_id IN ({placeholders})
                ORDER BY s.timestamp ASC
                """,
                params,
            )
            rows = cursor.fetchall()
        except Exception as e:
            logger.warning("Could not query price_snapshots for chart: %s", e)
            rows = []

    if not rows:
        fig.add_annotation(
            text="Нет данных снимков цен в базе",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
            font=dict(size=15, color="#8b949e"),
        )
    else:
        items_map: dict[int, dict[str, Any]] = {}
        for r in rows:
            item_id, name, ts, price = r[0], r[1], r[2], r[3]
            if item_id not in items_map:
                items_map[item_id] = {
                    "name": html.escape(str(name or "Unknown")),
                    "points": [],
                }
            items_map[item_id]["points"].append((ts, price))

        # Plot top 8 items (in order of priority / activity from top_ids)
        for item_id in top_ids:
            if item_id not in items_map:
                continue
            item_info = items_map[item_id]
            points = item_info["points"]

            # Decimate/downsample if more than 300 points to prevent browser SVG DOM explosion
            if len(points) > 300:
                step = (len(points) + 299) // 300
                downsampled = points[::step]
                if downsampled and downsampled[-1] != points[-1]:
                    if len(downsampled) >= 300:
                        downsampled[-1] = points[-1]
                    else:
                        downsampled.append(points[-1])
                points = downsampled

            x_vals = []
            y_vals = []
            hover_texts = []
            safe_name = item_info["name"]
            for ts, p in points:
                try:
                    dt_str = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    dt_str = str(ts)
                x_vals.append(dt_str)
                y_vals.append(p)
                hover_texts.append(f"{safe_name}<br>{format_currency(p)}")

            fig.add_trace(
                go.Scatter(
                    x=x_vals,
                    y=y_vals,
                    mode="lines+markers",
                    name=safe_name,
                    hovertext=hover_texts,
                    hoverinfo="text+x",
                    line=dict(width=2),
                    marker=dict(size=5),
                )
            )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#161b22",
        plot_bgcolor="#0d1117",
        font=dict(color="#c9d1d9", family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"),
        title=dict(text="Динамика цен ключевого сырья и расходников", font=dict(size=17, color="#58a6ff")),
        xaxis=dict(
            title="Время замера",
            gridcolor="#21262d",
            zerolinecolor="#30363d",
        ),
        yaxis=dict(
            title="Цена (медь / copper)",
            gridcolor="#21262d",
            zerolinecolor="#30363d",
        ),
        hovermode="closest",
        margin=dict(l=55, r=30, t=55, b=45),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            bgcolor="rgba(22, 27, 34, 0.7)",
        ),
        height=390,
    )

    chart_html = fig.to_html(full_html=False, include_plotlyjs="cdn")
    # Escape '</' in serialized chart data to prevent Stored XSS / script tag breakout
    chart_html = chart_html.replace("</", r"<\/").replace(r"<\/script>", "</script>").replace(r"<\/div>", "</div>")
    return chart_html


def _render_ai_banner(ai_advice: Optional[Union[dict, str]]) -> str:
    """Render top banner for AI assistant tactical advice and checklist."""
    if not ai_advice:
        return """
        <div id="ai-advisor-banner" class="ai-banner ai-empty">
            <div class="ai-banner-header">
                <span class="ai-badge">AI Advisor</span>
                <h3>Тактические рекомендации AI-ассистента</h3>
            </div>
            <p class="ai-placeholder">Рекомендации AI-ассистента не сформированы. Запустите генерацию рекомендаций через ai_advisor.</p>
        </div>
        """

    if isinstance(ai_advice, str):
        content_html = f"<p class='ai-text'>{html.escape(ai_advice)}</p>"
    else:
        summary = ai_advice.get("summary") or ai_advice.get("verdict") or ai_advice.get("title") or ""
        checklist = (
            ai_advice.get("checklist")
            or ai_advice.get("recommendations")
            or ai_advice.get("actions")
            or ai_advice.get("action_plan")
            or []
        )

        parts = []
        if summary:
            parts.append(f"<div class='ai-summary'><strong>Вердикт:</strong> {html.escape(str(summary))}</div>")

        if checklist:
            parts.append("<ol class='ai-checklist'>")
            for item in checklist:
                if isinstance(item, dict):
                    step_text = item.get("action") or item.get("text") or str(item.get("step") or item)
                else:
                    step_text = str(item)
                parts.append(f"<li>{html.escape(str(step_text))}</li>")
            parts.append("</ol>")

        if not summary and not checklist:
            raw_text = ai_advice.get("advice") or ai_advice.get("content") or str(ai_advice)
            parts.append(f"<p class='ai-text'>{html.escape(str(raw_text))}</p>")

        content_html = "\n".join(parts)

    return f"""
    <div id="ai-advisor-banner" class="ai-banner">
        <div class="ai-banner-header">
            <span class="ai-badge">Gemini 3.8 Flash</span>
            <h3>Тактические рекомендации AI-ассистента</h3>
        </div>
        <div class="ai-banner-body">
            {content_html}
        </div>
    </div>
    """


def build_standalone_dashboard(
    conn: sqlite3.Connection,
    output_path: str = "portal.html",
    ai_advice: Optional[dict] = None,
    realm: Optional[str] = None,
) -> str:
    """Build a standalone, single-file HTML market portal with Plotly charts and 3 signal tables.

    Tables:
    1. «Вендорный арбитраж» (Vendor Arbitrage)
    2. «Крафт под Waylaid Crates» (Crafting Margins for Waylaid Crates)
    3. «Аномальные сбросы цен» (Z-score price anomaly signals)

    Writes the HTML file to output_path and returns the HTML string.
    """
    if output_path is not None:
        output_path = str(output_path)

    # 1. Fetch signal data
    vendor_arbitrage = get_vendor_arbitrage(conn, realm=realm)
    all_crafting = get_crafting_margins(conn, realm=realm)
    anomalies = get_zscore_anomalies(conn, realm=realm)

    # Filter crafting margins for Waylaid Crates (with fallback to all crafting if none are tagged)
    waylaid_crafting = [c for c in all_crafting if c.get("is_waylaid_target")]
    crafting_display = waylaid_crafting if waylaid_crafting else all_crafting

    # 2. Render Plotly price history chart
    chart_html = _build_price_history_chart(conn, realm=realm)

    # 3. Render AI banner
    ai_banner_html = _render_ai_banner(ai_advice)

    # 4. Build Vendor Arbitrage table rows
    arbitrage_rows = []
    for item in vendor_arbitrage:
        q = item.get("quality", 1)
        name = html.escape(str(item.get("name") or "Unknown"))
        buyout = item.get("buyout_price", item.get("price", 0))
        vendor_sell = item.get("vendor_sell_price", 0)
        profit = item.get("profit_copper", item.get("profit", 0))
        item_realm = html.escape(str(item.get("realm") or "—"))

        arbitrage_rows.append(
            f"<tr>"
            f"<td class='q-{q}' data-value='{name}'>{name}</td>"
            f"<td data-value='{buyout}'>{format_currency(buyout, as_html=True)}</td>"
            f"<td data-value='{vendor_sell}'>{format_currency(vendor_sell, as_html=True)}</td>"
            f"<td data-value='{profit}' class='profit-cell'>{format_currency(profit, as_html=True)}</td>"
            f"<td data-value='{item_realm}'>{item_realm}</td>"
            f"</tr>"
        )
    if not arbitrage_rows:
        arbitrage_tbody = "<tr><td colspan='5' class='empty-state'>Нет лотов с вендорной прибылью ≥ 50c</td></tr>"
    else:
        arbitrage_tbody = "\n".join(arbitrage_rows)

    # 5. Build Waylaid Crates Crafting table rows
    crafting_rows = []
    for item in crafting_display:
        name = html.escape(str(item.get("crafted_item_name") or item.get("name") or "Recipe"))
        profession = html.escape(str(item.get("profession") or "Craft"))
        crate_name = html.escape(str(item.get("waylaid_crate_name") or "—"))
        cost = item.get("cost_copper", int(round(item.get("cost", 0))))
        revenue = item.get("revenue_copper", int(round(item.get("revenue", 0))))
        profit = item.get("profit_copper", int(round(item.get("profit", 0))))
        roi_pct = item.get("roi_percent", round(item.get("roi", 0) * 100, 1))

        # Reagents breakdown summary
        reagents = item.get("reagents", [])
        if reagents:
            reagents_text = ", ".join(f"{str(r.get('name') or 'Unknown')} x{r.get('count', 1)}" for r in reagents[:3])
            if len(reagents) > 3:
                reagents_text += f" (+{len(reagents)-3})"
            reagents_html = f"<div class='reagents-subtext'>{html.escape(reagents_text)}</div>"
        else:
            reagents_html = ""

        crafting_rows.append(
            f"<tr>"
            f"<td data-value='{name}'><strong>{name}</strong>{reagents_html}</td>"
            f"<td data-value='{profession}'>{profession}</td>"
            f"<td data-value='{crate_name}' class='crate-cell'>{crate_name}</td>"
            f"<td data-value='{cost}'>{format_currency(cost, as_html=True)}</td>"
            f"<td data-value='{revenue}'>{format_currency(revenue, as_html=True)}</td>"
            f"<td data-value='{profit}' class='profit-cell'>{format_currency(profit, as_html=True)}</td>"
            f"<td data-value='{roi_pct}' class='roi-cell'>+{roi_pct}%</td>"
            f"</tr>"
        )
    if not crafting_rows:
        crafting_tbody = "<tr><td colspan='7' class='empty-state'>Нет активных маржинальных рецептов под Waylaid Crates (ROI ≥ 25%)</td></tr>"
    else:
        crafting_tbody = "\n".join(crafting_rows)

    # 6. Build Price Anomalies table rows
    anomaly_rows = []
    for item in anomalies:
        name = html.escape(str(item.get("name") or "Item"))
        signal = str(item.get("signal") or "HOLD")
        badge_cls = "badge-buy" if "BUY" in signal else "badge-sell"
        curr_price = item.get("current_price", item.get("price", 0))
        mean_price = item.get("mean_price", item.get("mean", 0))
        z = item.get("z_score", item.get("z", 0.0))
        n = item.get("sample_count", item.get("n", 0))
        item_realm = html.escape(str(item.get("realm") or "—"))

        anomaly_rows.append(
            f"<tr>"
            f"<td data-value='{name}'><strong>{name}</strong></td>"
            f"<td data-value='{signal}'><span class='badge {badge_cls}'>{signal}</span></td>"
            f"<td data-value='{curr_price}'>{format_currency(curr_price, as_html=True)}</td>"
            f"<td data-value='{mean_price}'>{format_currency(mean_price, as_html=True)}</td>"
            f"<td data-value='{z}' class='z-cell'>{z:+.2f}</td>"
            f"<td data-value='{n}'>{n}</td>"
            f"<td data-value='{item_realm}'>{item_realm}</td>"
            f"</tr>"
        )
    if not anomaly_rows:
        anomaly_tbody = "<tr><td colspan='7' class='empty-state'>Аномальных сбросов или всплесков цен не обнаружено</td></tr>"
    else:
        anomaly_tbody = "\n".join(anomaly_rows)

    # 7. Assemble full HTML document
    html_content = f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Forever Trade — Рыночный портал</title>
    <style>
        :root {{
            --bg-main: #0e1117;
            --bg-card: #161b22;
            --bg-card-hover: #1c2128;
            --border-color: #30363d;
            --text-primary: #e6edf3;
            --text-muted: #8b949e;
            --accent-blue: #58a6ff;
            --accent-green: #3fb950;
            --accent-red: #f85149;
            --gold: #ffd700;
            --silver: #c0c0c0;
            --copper: #eda55f;
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}

        body {{
            background-color: var(--bg-main);
            color: var(--text-primary);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            line-height: 1.5;
            padding: 24px;
        }}

        .container {{
            max-width: 1360px;
            margin: 0 auto;
        }}

        header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding-bottom: 20px;
            margin-bottom: 24px;
            border-bottom: 1px solid var(--border-color);
        }}

        .brand h1 {{
            font-size: 1.7rem;
            color: #ffffff;
            font-weight: 700;
            letter-spacing: -0.5px;
        }}

        .brand p {{
            font-size: 0.9rem;
            color: var(--text-muted);
        }}

        .portal-stats {{
            display: flex;
            gap: 16px;
        }}

        .stat-pill {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            padding: 6px 14px;
            border-radius: 6px;
            font-size: 0.85rem;
            color: var(--text-muted);
        }}

        .stat-pill strong {{
            color: var(--accent-blue);
        }}

        /* AI Banner */
        .ai-banner {{
            background: linear-gradient(135deg, #1c2128 0%, #161b22 100%);
            border: 1px solid #388bfd;
            border-left: 4px solid var(--accent-blue);
            border-radius: 8px;
            padding: 18px 24px;
            margin-bottom: 28px;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25);
        }}

        .ai-banner-header {{
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 12px;
        }}

        .ai-banner-header h3 {{
            font-size: 1.15rem;
            color: #ffffff;
        }}

        .ai-badge {{
            background: #1f6feb;
            color: #ffffff;
            font-size: 0.75rem;
            font-weight: 700;
            padding: 2px 8px;
            border-radius: 10px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        .ai-summary {{
            color: var(--text-primary);
            font-size: 0.95rem;
            margin-bottom: 10px;
        }}

        .ai-checklist {{
            margin-left: 20px;
        }}

        .ai-checklist li {{
            margin-bottom: 6px;
            font-size: 0.92rem;
            color: var(--text-primary);
        }}

        .ai-empty .ai-placeholder {{
            color: var(--text-muted);
            font-style: italic;
            font-size: 0.9rem;
        }}

        /* Plotly Section */
        .chart-section {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 16px;
            margin-bottom: 32px;
        }}

        /* Tables and Sections */
        .section-block {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 32px;
        }}

        .section-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 16px;
        }}

        .section-header h2 {{
            font-size: 1.25rem;
            color: #ffffff;
            font-weight: 600;
        }}

        .table-responsive {{
            overflow-x: auto;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.9rem;
        }}

        th, td {{
            padding: 10px 14px;
            text-align: left;
            border-bottom: 1px solid var(--border-color);
        }}

        th {{
            background: #21262d;
            color: var(--text-muted);
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            cursor: pointer;
            user-select: none;
            white-space: nowrap;
        }}

        th:hover {{
            color: var(--text-primary);
        }}

        tr:hover td {{
            background-color: var(--bg-card-hover);
        }}

        .empty-state {{
            text-align: center;
            color: var(--text-muted);
            font-style: italic;
            padding: 24px;
        }}

        /* Money Currency Styling */
        .money-gold {{
            color: var(--gold);
            font-weight: 600;
        }}

        .money-silver {{
            color: var(--silver);
            font-weight: 600;
        }}

        .money-copper {{
            color: var(--copper);
            font-weight: 600;
        }}

        .profit-cell {{
            font-weight: 600;
        }}

        .roi-cell {{
            color: var(--accent-green);
            font-weight: 700;
        }}

        .z-cell {{
            font-weight: 700;
            color: var(--accent-blue);
        }}

        .crate-cell {{
            color: #d2a8ff;
        }}

        .reagents-subtext {{
            font-size: 0.78rem;
            color: var(--text-muted);
            margin-top: 2px;
        }}

        /* Quality Colors */
        .q-0 {{ color: #9d9d9d; }}
        .q-1 {{ color: #ffffff; }}
        .q-2 {{ color: #1eff00; }}
        .q-3 {{ color: #0070dd; }}
        .q-4 {{ color: #a335ee; }}
        .q-5 {{ color: #ff8000; }}

        /* Badges */
        .badge {{
            display: inline-block;
            padding: 2px 8px;
            font-size: 0.75rem;
            font-weight: 700;
            border-radius: 12px;
            letter-spacing: 0.5px;
        }}

        .badge-buy {{
            background: rgba(63, 185, 80, 0.2);
            color: #3fb950;
            border: 1px solid rgba(63, 185, 80, 0.4);
        }}

        .badge-sell {{
            background: rgba(248, 81, 73, 0.2);
            color: #f85149;
            border: 1px solid rgba(248, 81, 73, 0.4);
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="brand">
                <h1>Forever Trade Portal</h1>
                <p>Автономный аналитический портал и торговый навигатор WoW: Forever</p>
            </div>
            <div class="portal-stats">
                <div class="stat-pill">Вендорный арбитраж: <strong>{len(vendor_arbitrage)}</strong></div>
                <div class="stat-pill">Маржинальный крафт: <strong>{len(crafting_display)}</strong></div>
                <div class="stat-pill">Ценовые аномалии: <strong>{len(anomalies)}</strong></div>
            </div>
        </header>

        <!-- AI Assistant Banner -->
        {ai_banner_html}

        <!-- Interactive Plotly Chart -->
        <section class="chart-section" id="charts-section">
            {chart_html}
        </section>

        <!-- Block 1: Вендорный арбитраж -->
        <section class="section-block" id="vendor-arbitrage">
            <div class="section-header">
                <h2>Вендорный арбитраж</h2>
                <span class="stat-pill">Порог прибыли: ≥ 50c</span>
            </div>
            <div class="table-responsive">
                <table class="sortable">
                    <thead>
                        <tr>
                            <th>Предмет</th>
                            <th>Выкуп (AH)</th>
                            <th>Вендор</th>
                            <th>Прибыль</th>
                            <th>Реалм</th>
                        </tr>
                    </thead>
                    <tbody>
                        {arbitrage_tbody}
                    </tbody>
                </table>
            </div>
        </section>

        <!-- Block 2: Крафт под Waylaid Crates -->
        <section class="section-block" id="waylaid-crates">
            <div class="section-header">
                <h2>Крафт под Waylaid Crates</h2>
                <span class="stat-pill">Порог ROI: ≥ 25% (Bartering + Master Chef)</span>
            </div>
            <div class="table-responsive">
                <table class="sortable">
                    <thead>
                        <tr>
                            <th>Рецепт / Предмет</th>
                            <th>Профессия</th>
                            <th>Ящик Waylaid Crate</th>
                            <th>Себестоимость</th>
                            <th>Выручка (AH)</th>
                            <th>Чистая прибыль</th>
                            <th>ROI</th>
                        </tr>
                    </thead>
                    <tbody>
                        {crafting_tbody}
                    </tbody>
                </table>
            </div>
        </section>

        <!-- Block 3: Аномальные сбросы цен -->
        <section class="section-block" id="price-anomalies">
            <div class="section-header">
                <h2>Аномальные сбросы цен</h2>
                <span class="stat-pill">Z-Score сигналы (STRONG_BUY / STRONG_SELL)</span>
            </div>
            <div class="table-responsive">
                <table class="sortable">
                    <thead>
                        <tr>
                            <th>Предмет</th>
                            <th>Сигнал</th>
                            <th>Текущая цена</th>
                            <th>Средняя цена</th>
                            <th>Z-Score</th>
                            <th>Замеров</th>
                            <th>Реалм</th>
                        </tr>
                    </thead>
                    <tbody>
                        {anomaly_tbody}
                    </tbody>
                </table>
            </div>
        </section>
    </div>

    <!-- Client-side Sorting Script -->
    <script>
        document.addEventListener("DOMContentLoaded", function() {{
            document.querySelectorAll("table.sortable").forEach(function(table) {{
                var headers = table.querySelectorAll("th");
                headers.forEach(function(header, colIndex) {{
                    header.addEventListener("click", function() {{
                        var tbody = table.querySelector("tbody");
                        var rows = Array.from(tbody.querySelectorAll("tr"));
                        if (rows.length <= 1) return;
                        var asc = header.getAttribute("data-sort") !== "asc";
                        headers.forEach(function(h) {{ h.removeAttribute("data-sort"); }});
                        header.setAttribute("data-sort", asc ? "asc" : "desc");
                        rows.sort(function(a, b) {{
                            var cellA = a.children[colIndex] ? a.children[colIndex].getAttribute("data-value") || a.children[colIndex].innerText.trim() : "";
                            var cellB = b.children[colIndex] ? b.children[colIndex].getAttribute("data-value") || b.children[colIndex].innerText.trim() : "";
                            var numA = parseFloat(cellA);
                            var numB = parseFloat(cellB);
                            if (!isNaN(numA) && !isNaN(numB)) {{
                                return asc ? numA - numB : numB - numA;
                            }}
                            return asc ? cellA.localeCompare(cellB) : cellB.localeCompare(cellA);
                        }});
                        rows.forEach(function(row) {{ tbody.appendChild(row); }});
                    }});
                }});
            }});
        }});
    </script>
</body>
</html>
"""

    if output_path:
        out_dir = os.path.dirname(output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_content)

    return html_content
