"""Standalone HTML dashboard generator and arbitrage alert notifier for WoW: Forever."""

from collections import defaultdict
import datetime
import html
import json
import logging
import math
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

try:
    from src.database import get_item_name
except ImportError:
    try:
        from database import get_item_name
    except ImportError:
        def get_item_name(conn: Optional[sqlite3.Connection], item_id: int, lang: str = "en") -> str:
            return f"Item {item_id}"

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
            text="Нет данных снимков цен в базе. База данных ожидает первого снимка цен из Auctionator.lua... / Waiting for first price snapshot from Auctionator.lua...",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
            font=dict(size=14, color="#8b949e"),
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
                <h3 class="i18n" data-ru="Тактические рекомендации AI-ассистента" data-en="AI Assistant Tactical Recommendations">Тактические рекомендации AI-ассистента</h3>
            </div>
            <p class="ai-placeholder i18n" data-ru="Рекомендации AI-ассистента не сформированы. Запустите генерацию рекомендаций через ai_advisor." data-en="AI recommendations not generated. Run recommendation generation via ai_advisor.">Рекомендации AI-ассистента не сформированы. Запустите генерацию рекомендаций через ai_advisor.</p>
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
            parts.append(f"<div class='ai-summary'><strong><span class='i18n' data-ru='Вердикт:' data-en='Verdict:'>Вердикт:</span></strong> {html.escape(str(summary))}</div>")

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
            <h3 class="i18n" data-ru="Тактические рекомендации AI-ассистента" data-en="AI Assistant Tactical Recommendations">Тактические рекомендации AI-ассистента</h3>
        </div>
        <div class="ai-banner-body">
            {content_html}
        </div>
    </div>
    """


def _normalize_category(cat: Optional[str], item_class: Optional[int], target_item_id: Optional[int]) -> str:
    """Normalize item categories to standard UI category filter keys."""
    if cat == "waylaid_crate" or (target_item_id is not None and target_item_id > 0):
        return "waylaid_crates"
    if cat in ("trade_goods", "raw", "reagent", "herb", "ore", "cloth", "metal", "leather") or item_class == 7:
        return "trade_goods"
    if cat in ("consumable", "bivouac", "potion", "elixir", "food", "scroll") or item_class == 0:
        return "consumables"
    if cat in ("weapon", "weapons") or item_class == 2:
        return "weapons"
    if cat in ("armor", "craftsman_writ", "cloth_armor", "leather_armor", "mail_armor", "plate_armor") or item_class == 4:
        return "armor"
    return "trade_goods"


def _get_all_items_market_data(
    conn: sqlite3.Connection,
    realm: Optional[str] = None,
    extra_items: Optional[list[dict]] = None,
) -> dict[int, dict[str, Any]]:
    """Query and aggregate price history and statistics per item for client-side inspector & modal."""
    market_data: dict[int, dict[str, Any]] = {}
    cursor = conn.cursor()

    items_meta = {}
    try:
        cursor.execute(
            """
            SELECT item_id, name, name_ru, quality, item_class, category, target_item_id
            FROM items
            """
        )
        for r in cursor.fetchall():
            items_meta[r[0]] = {
                "name": r[1],
                "name_ru": r[2],
                "quality": r[3] or 1,
                "item_class": r[4],
                "category": r[5],
                "target_item_id": r[6],
            }
    except Exception as e:
        logger.warning("Could not query items metadata: %s", e)

    snapshots_by_item = defaultdict(list)
    try:
        cursor.execute(
            """
            SELECT item_id, timestamp, price
            FROM price_snapshots
            WHERE (? IS NULL OR realm = ?)
            ORDER BY timestamp ASC
            """,
            (realm, realm),
        )
        for r in cursor.fetchall():
            snapshots_by_item[r[0]].append((r[1], r[2]))
    except Exception as e:
        logger.warning("Could not query price_snapshots: %s", e)

    all_item_ids = set(snapshots_by_item.keys())
    if extra_items:
        for it in extra_items:
            iid = it.get("item_id") or it.get("crafted_item_id")
            if iid is not None:
                try:
                    all_item_ids.add(int(iid))
                except (ValueError, TypeError):
                    pass

    for item_id in all_item_ids:
        meta = items_meta.get(item_id, {})
        pts = snapshots_by_item.get(item_id, [])
        cat = _normalize_category(meta.get("category"), meta.get("item_class"), meta.get("target_item_id"))

        name_ru = get_item_name(conn, item_id, "ru")
        name_en = get_item_name(conn, item_id, "en")

        if name_ru.lower().startswith(f"item {item_id}".lower()) and meta.get("name"):
            name_ru = meta["name"]
        if name_en.lower().startswith(f"item {item_id}".lower()) and meta.get("name"):
            name_en = meta["name"]

        # Downsample points if > 300 to keep DOM fast
        downsampled_pts = pts
        if len(pts) > 300:
            step = (len(pts) + 299) // 300
            downsampled = pts[::step]
            if downsampled and downsampled[-1] != pts[-1]:
                if len(downsampled) >= 300:
                    downsampled[-1] = pts[-1]
                else:
                    downsampled.append(pts[-1])
            downsampled_pts = downsampled

        prices = [p for _, p in pts]
        curr_price = prices[-1] if prices else 0
        min_price = min(prices) if prices else 0
        max_price = max(prices) if prices else 0
        mean_price = round(sum(prices) / len(prices), 2) if prices else 0

        z_score = 0.0
        if len(prices) >= 2:
            n = len(prices)
            var = sum((p - mean_price) ** 2 for p in prices) / (n - 1)
            sigma = math.sqrt(var)
            if sigma > 0:
                z_score = round((curr_price - mean_price) / sigma, 2)

        market_data[item_id] = {
            "item_id": item_id,
            "name_ru": name_ru,
            "name_en": name_en,
            "quality": meta.get("quality", 1),
            "category": cat,
            "current_price": curr_price,
            "mean_price": mean_price,
            "min_price": min_price,
            "max_price": max_price,
            "z_score": z_score,
            "sample_count": len(prices),
            "points": [[ts, p] for ts, p in downsampled_pts],
        }

    return market_data


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

    # Collect extra item records from signals to guarantee inspector availability
    extra_items = list(vendor_arbitrage) + list(crafting_display) + list(anomalies)
    items_market_data = _get_all_items_market_data(conn, realm=realm, extra_items=extra_items)

    # 2. Render Plotly price history chart
    chart_html = _build_price_history_chart(conn, realm=realm)

    # 3. Render AI banner
    ai_banner_html = _render_ai_banner(ai_advice)

    # 4. Build Vendor Arbitrage table rows
    arbitrage_rows = []
    for item in vendor_arbitrage:
        item_id = item.get("item_id")
        q = item.get("quality", 1)
        raw_name = item.get("name")
        if item_id is not None:
            name_ru = get_item_name(conn, item_id, "ru")
            name_en = get_item_name(conn, item_id, "en")
        else:
            name_ru = str(raw_name) if raw_name is not None else "Unknown"
            name_en = name_ru

        escaped_name_ru = html.escape(name_ru)
        escaped_name_en = html.escape(name_en)

        buyout = item.get("buyout_price", item.get("price", 0))
        vendor_sell = item.get("vendor_sell_price", 0)
        profit = item.get("profit_copper", item.get("profit", 0))
        item_realm = html.escape(str(item.get("realm") or "—"))

        id_attr = f"data-item-id='{item_id}'" if item_id is not None else ""
        click_attr = f"onclick=\"onRowClick(event, {item_id})\"" if item_id is not None else ""
        graph_btn = f"<button class='graph-btn' title='График / Chart' onclick='openItemModal({item_id}, event)'>📈</button>" if item_id is not None else ""

        arbitrage_rows.append(
            f"<tr {id_attr} {click_attr} class='clickable-row'>"
            f"<td class='q-{q}' data-value='{escaped_name_ru}'>"
            f"<div class='item-cell-wrap'>"
            f"<span class='item-name i18n' data-ru='{escaped_name_ru}' data-en='{escaped_name_en}'>{escaped_name_ru}</span>"
            f"<div class='item-actions'>"
            f"<button class='copy-btn' title='Копировать название / Copy name' onclick='copyItemName(this, event)'>📋</button>"
            f"{graph_btn}"
            f"</div>"
            f"</div>"
            f"</td>"
            f"<td data-value='{buyout}'>{format_currency(buyout, as_html=True)}</td>"
            f"<td data-value='{vendor_sell}'>{format_currency(vendor_sell, as_html=True)}</td>"
            f"<td data-value='{profit}' class='profit-cell'>{format_currency(profit, as_html=True)}</td>"
            f"<td data-value='{item_realm}'>{item_realm}</td>"
            f"</tr>"
        )
    if not arbitrage_rows:
        arbitrage_tbody = "<tr><td colspan='5' class='empty-state i18n' data-ru='Нет лотов с вендорной прибылью ≥ 50c' data-en='No lots with vendor profit ≥ 50c'>Нет лотов с вендорной прибылью ≥ 50c</td></tr>"
    else:
        arbitrage_tbody = "\n".join(arbitrage_rows)

    # 5. Build Waylaid Crates Crafting table rows
    crafting_rows = []
    for item in crafting_display:
        crafted_id = item.get("crafted_item_id")
        raw_name = item.get("crafted_item_name") or item.get("name")
        if crafted_id is not None:
            name_ru = get_item_name(conn, crafted_id, "ru")
            name_en = get_item_name(conn, crafted_id, "en")
        else:
            name_ru = str(raw_name) if raw_name is not None else "Recipe"
            name_en = name_ru

        escaped_name_ru = html.escape(name_ru)
        escaped_name_en = html.escape(name_en)

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

        id_attr = f"data-item-id='{crafted_id}'" if crafted_id is not None else ""
        click_attr = f"onclick=\"onRowClick(event, {crafted_id})\"" if crafted_id is not None else ""
        graph_btn = f"<button class='graph-btn' title='График / Chart' onclick='openItemModal({crafted_id}, event)'>📈</button>" if crafted_id is not None else ""

        crafting_rows.append(
            f"<tr {id_attr} {click_attr} class='clickable-row'>"
            f"<td data-value='{escaped_name_ru}'>"
            f"<div class='item-cell-wrap'>"
            f"<div>"
            f"<strong><span class='item-name i18n' data-ru='{escaped_name_ru}' data-en='{escaped_name_en}'>{escaped_name_ru}</span></strong>"
            f"{reagents_html}"
            f"</div>"
            f"<div class='item-actions'>"
            f"<button class='copy-btn' title='Копировать название / Copy name' onclick='copyItemName(this, event)'>📋</button>"
            f"{graph_btn}"
            f"</div>"
            f"</div>"
            f"</td>"
            f"<td data-value='{profession}'>{profession}</td>"
            f"<td data-value='{crate_name}' class='crate-cell'>{crate_name}</td>"
            f"<td data-value='{cost}'>{format_currency(cost, as_html=True)}</td>"
            f"<td data-value='{revenue}'>{format_currency(revenue, as_html=True)}</td>"
            f"<td data-value='{profit}' class='profit-cell'>{format_currency(profit, as_html=True)}</td>"
            f"<td data-value='{roi_pct}' class='roi-cell'>+{roi_pct}%</td>"
            f"</tr>"
        )
    if not crafting_rows:
        crafting_tbody = "<tr><td colspan='7' class='empty-state i18n' data-ru='Нет активных маржинальных рецептов под Waylaid Crates (ROI ≥ 25%)' data-en='No active profitable recipes for Waylaid Crates (ROI ≥ 25%)'>Нет активных маржинальных рецептов под Waylaid Crates (ROI ≥ 25%)</td></tr>"
    else:
        crafting_tbody = "\n".join(crafting_rows)

    # 6. Build Price Anomalies table rows
    anomaly_rows = []
    for item in anomalies:
        item_id = item.get("item_id")
        raw_name = item.get("name")
        if item_id is not None:
            name_ru = get_item_name(conn, item_id, "ru")
            name_en = get_item_name(conn, item_id, "en")
        else:
            name_ru = str(raw_name) if raw_name is not None else "Item"
            name_en = name_ru

        escaped_name_ru = html.escape(name_ru)
        escaped_name_en = html.escape(name_en)

        signal = str(item.get("signal") or "HOLD")
        badge_cls = "badge-buy" if "BUY" in signal else "badge-sell"
        curr_price = item.get("current_price", item.get("price", 0))
        mean_price = item.get("mean_price", item.get("mean", 0))
        z = item.get("z_score", item.get("z", 0.0))
        n = item.get("sample_count", item.get("n", 0))
        item_realm = html.escape(str(item.get("realm") or "—"))

        id_attr = f"data-item-id='{item_id}'" if item_id is not None else ""
        click_attr = f"onclick=\"onRowClick(event, {item_id})\"" if item_id is not None else ""
        graph_btn = f"<button class='graph-btn' title='График / Chart' onclick='openItemModal({item_id}, event)'>📈</button>" if item_id is not None else ""

        anomaly_rows.append(
            f"<tr {id_attr} {click_attr} class='clickable-row'>"
            f"<td data-value='{escaped_name_ru}'>"
            f"<div class='item-cell-wrap'>"
            f"<strong><span class='item-name i18n' data-ru='{escaped_name_ru}' data-en='{escaped_name_en}'>{escaped_name_ru}</span></strong>"
            f"<div class='item-actions'>"
            f"<button class='copy-btn' title='Копировать название / Copy name' onclick='copyItemName(this, event)'>📋</button>"
            f"{graph_btn}"
            f"</div>"
            f"</div>"
            f"</td>"
            f"<td data-value='{signal}'><span class='badge {badge_cls}'>{signal}</span></td>"
            f"<td data-value='{curr_price}'>{format_currency(curr_price, as_html=True)}</td>"
            f"<td data-value='{mean_price}'>{format_currency(mean_price, as_html=True)}</td>"
            f"<td data-value='{z}' class='z-cell'>{z:+.2f}</td>"
            f"<td data-value='{n}'>{n}</td>"
            f"<td data-value='{item_realm}'>{item_realm}</td>"
            f"</tr>"
        )
    if not anomaly_rows:
        anomaly_tbody = "<tr><td colspan='7' class='empty-state i18n' data-ru='Аномальных сбросов или всплесков цен не обнаружено' data-en='No price anomalies or dumps detected'>Аномальных сбросов или всплесков цен не обнаружено</td></tr>"
    else:
        anomaly_tbody = "\n".join(anomaly_rows)

    # 7. Build item select dropdown options sorted alphabetically
    sorted_items = sorted(
        items_market_data.values(),
        key=lambda x: str(x.get("name_ru") or x.get("name_en") or "").lower(),
    )
    options_parts = []
    for it in sorted_items:
        i_id = it["item_id"]
        cat = it.get("category", "trade_goods")
        n_ru = html.escape(str(it.get("name_ru") or f"Item {i_id}"))
        n_en = html.escape(str(it.get("name_en") or f"Item {i_id}"))
        search_kw = html.escape(f"{n_ru} {n_en}".lower())
        options_parts.append(
            f'<option value="{i_id}" data-category="{cat}" data-ru="{n_ru}" data-en="{n_en}" data-search="{search_kw}">{n_ru}</option>'
        )
    options_html = "\n".join(options_parts)

    # 8. Serialize items market data to JSON for client-side inspector & modal
    items_store_json = json.dumps(items_market_data, ensure_ascii=False)
    # Sanitize for <script> tag inclusion
    items_store_json = items_store_json.replace("</", r"<\/").replace("<", r"\u003c")

    # 9. Assemble full HTML document
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
            gap: 20px;
            flex-wrap: wrap;
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

        .header-right {{
            display: flex;
            align-items: center;
            gap: 16px;
            flex-wrap: wrap;
        }}

        .lang-switcher {{
            display: flex;
            background: #0d1117;
            border: 1px solid var(--border-color);
            border-radius: 6px;
            overflow: hidden;
        }}

        .lang-btn {{
            background: transparent;
            border: none;
            color: var(--text-muted);
            padding: 6px 14px;
            font-size: 0.85rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
        }}

        .lang-btn:hover {{
            color: var(--text-primary);
            background: rgba(255, 255, 255, 0.05);
        }}

        .lang-btn.active {{
            background: #1f6feb;
            color: #ffffff;
        }}

        .portal-stats {{
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
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

        /* Chart Section & Category Filter */
        .chart-section {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 18px 20px;
            margin-bottom: 32px;
        }}

        .chart-header {{
            margin-bottom: 16px;
        }}

        .chart-title-wrap {{
            margin-bottom: 12px;
        }}

        .chart-title-wrap h2 {{
            font-size: 1.25rem;
            color: #ffffff;
            font-weight: 600;
        }}

        .chart-subtext {{
            font-size: 0.85rem;
            color: var(--text-muted);
            margin-top: 2px;
        }}

        .chart-controls {{
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            justify-content: space-between;
            gap: 14px;
        }}

        .category-filters {{
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
        }}

        .cat-btn {{
            background: #21262d;
            border: 1px solid var(--border-color);
            color: var(--text-muted);
            padding: 5px 12px;
            border-radius: 6px;
            font-size: 0.82rem;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.15s ease;
        }}

        .cat-btn:hover {{
            color: var(--text-primary);
            border-color: #58a6ff;
        }}

        .cat-btn.active {{
            background: #1f6feb;
            color: #ffffff;
            border-color: #388bfd;
        }}

        .item-selector-wrap {{
            display: flex;
            align-items: center;
            gap: 8px;
            flex: 1;
            max-width: 500px;
            min-width: 260px;
        }}

        .search-input, .item-dropdown {{
            background: #0d1117;
            border: 1px solid var(--border-color);
            color: var(--text-primary);
            border-radius: 6px;
            padding: 6px 10px;
            font-size: 0.85rem;
            outline: none;
        }}

        .search-input {{
            flex: 1;
            min-width: 120px;
        }}

        .search-input:focus, .item-dropdown:focus {{
            border-color: var(--accent-blue);
        }}

        .item-dropdown {{
            flex: 1.5;
            min-width: 160px;
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

        tr.clickable-row {{
            cursor: pointer;
        }}

        tr:hover td {{
            background-color: var(--bg-card-hover);
        }}

        .item-cell-wrap {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
        }}

        .item-actions {{
            display: inline-flex;
            align-items: center;
            gap: 4px;
            opacity: 0.85;
            transition: opacity 0.15s ease;
        }}

        tr:hover .item-actions {{
            opacity: 1;
        }}

        .copy-btn, .graph-btn {{
            background: #21262d;
            border: 1px solid var(--border-color);
            color: var(--text-muted);
            border-radius: 4px;
            padding: 2px 6px;
            font-size: 0.78rem;
            cursor: pointer;
            line-height: 1.2;
            transition: all 0.15s ease;
        }}

        .copy-btn:hover, .graph-btn:hover {{
            background: #30363d;
            color: var(--text-primary);
            border-color: #58a6ff;
        }}

        .copy-btn.copied {{
            background: rgba(63, 185, 80, 0.2);
            border-color: #3fb950;
            color: #3fb950;
            font-weight: 600;
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

        /* Item Modal & Dedicated Inspector */
        .modal-backdrop {{
            position: fixed;
            top: 0;
            left: 0;
            width: 100vw;
            height: 100vh;
            background: rgba(0, 0, 0, 0.75);
            backdrop-filter: blur(4px);
            z-index: 9999;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}

        .modal-content {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 10px;
            width: 100%;
            max-width: 820px;
            max-height: 90vh;
            overflow-y: auto;
            padding: 24px;
            box-shadow: 0 10px 36px rgba(0, 0, 0, 0.55);
        }}

        .modal-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 14px;
            margin-bottom: 18px;
        }}

        .modal-title-wrap {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}

        .modal-title {{
            font-size: 1.3rem;
            font-weight: 700;
        }}

        .modal-close-btn {{
            background: transparent;
            border: none;
            color: var(--text-muted);
            font-size: 1.4rem;
            cursor: pointer;
            padding: 4px 8px;
            line-height: 1;
            border-radius: 4px;
        }}

        .modal-close-btn:hover {{
            color: var(--text-primary);
            background: #21262d;
        }}

        .modal-stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
            gap: 12px;
            margin-bottom: 20px;
        }}

        .modal-stat-card {{
            background: #0d1117;
            border: 1px solid var(--border-color);
            border-radius: 6px;
            padding: 10px 12px;
            display: flex;
            flex-direction: column;
            gap: 4px;
        }}

        .modal-stat-card .stat-label {{
            font-size: 0.75rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        .modal-stat-card .stat-value {{
            font-size: 1rem;
            font-weight: 600;
            color: var(--text-primary);
        }}

        .modal-plot-container {{
            min-height: 350px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="brand">
                <h1>Forever Trade Portal</h1>
                <p class="i18n" data-ru="Автономный аналитический портал и торговый навигатор WoW: Forever" data-en="Autonomous analytical portal & market navigator for WoW: Forever">Автономный аналитический портал и торговый навигатор WoW: Forever</p>
            </div>
            <div class="header-right">
                <div class="lang-switcher">
                    <button id="lang-ru" class="lang-btn active" onclick="setLanguage('ru')">RU</button>
                    <button id="lang-en" class="lang-btn" onclick="setLanguage('en')">EN</button>
                </div>
                <div class="portal-stats">
                    <div class="stat-pill"><span class="i18n" data-ru="Вендорный арбитраж:" data-en="Vendor Arbitrage:">Вендорный арбитраж:</span> <strong>{len(vendor_arbitrage)}</strong></div>
                    <div class="stat-pill"><span class="i18n" data-ru="Маржинальный крафт:" data-en="Waylaid Crafting:">Маржинальный крафт:</span> <strong>{len(crafting_display)}</strong></div>
                    <div class="stat-pill"><span class="i18n" data-ru="Ценовые аномалии:" data-en="Price Anomalies:">Ценовые аномалии:</span> <strong>{len(anomalies)}</strong></div>
                </div>
            </div>
        </header>

        <!-- AI Assistant Banner -->
        {ai_banner_html}

        <!-- Interactive Plotly Chart & Category Filter Section -->
        <section class="chart-section" id="charts-section">
            <div class="chart-header">
                <div class="chart-title-wrap">
                    <h2 class="i18n" data-ru="Динамика цен ключевого сырья и расходников" data-en="Key Reagents & Consumables Price History">Динамика цен ключевого сырья и расходников</h2>
                    <p class="chart-subtext i18n" data-ru="Выберите предмет из каталога или нажмите 📈 в таблицах сигналов для детального анализа" data-en="Select an item from the catalog or click 📈 in signal tables for detailed analysis">Выберите предмет из каталога или нажмите 📈 в таблицах сигналов для детального анализа</p>
                </div>
                <div class="chart-controls">
                    <div class="category-filters">
                        <button class="cat-btn active" data-cat="all" onclick="filterCategory('all')"><span class="i18n" data-ru="Все" data-en="All">Все</span></button>
                        <button class="cat-btn" data-cat="trade_goods" onclick="filterCategory('trade_goods')"><span class="i18n" data-ru="Сырье" data-en="Trade Goods">Сырье</span></button>
                        <button class="cat-btn" data-cat="consumables" onclick="filterCategory('consumables')"><span class="i18n" data-ru="Расходники" data-en="Consumables">Расходники</span></button>
                        <button class="cat-btn" data-cat="weapons" onclick="filterCategory('weapons')"><span class="i18n" data-ru="Оружие" data-en="Weapons">Оружие</span></button>
                        <button class="cat-btn" data-cat="armor" onclick="filterCategory('armor')"><span class="i18n" data-ru="Броня" data-en="Armor">Броня</span></button>
                        <button class="cat-btn" data-cat="waylaid_crates" onclick="filterCategory('waylaid_crates')"><span class="i18n" data-ru="Ящики" data-en="Waylaid Crates">Ящики</span></button>
                    </div>
                    <div class="item-selector-wrap">
                        <input type="text" id="item-search-input" class="search-input" placeholder="Поиск предмета..." data-ru-placeholder="Поиск предмета..." data-en-placeholder="Search item..." oninput="onItemSearch(this.value)" />
                        <select id="item-select" class="item-dropdown" onchange="onSelectDropdownItem(this.value)">
                            <option value="" class="i18n" data-ru="-- Выберите предмет для графика --" data-en="-- Select item for chart --">-- Выберите предмет для графика --</option>
                            {options_html}
                        </select>
                    </div>
                </div>
            </div>
            <div id="overview-chart-container">
                {chart_html}
            </div>
        </section>

        <!-- Block 1: Вендорный арбитраж -->
        <section class="section-block" id="vendor-arbitrage">
            <div class="section-header">
                <h2 class="i18n" data-ru="Вендорный арбитраж" data-en="Vendor Arbitrage">Вендорный арбитраж</h2>
                <span class="stat-pill i18n" data-ru="Порог прибыли: ≥ 50c" data-en="Profit threshold: ≥ 50c">Порог прибыли: ≥ 50c</span>
            </div>
            <div class="table-responsive">
                <table class="sortable">
                    <thead>
                        <tr>
                            <th class="i18n" data-ru="Предмет" data-en="Item">Предмет</th>
                            <th class="i18n" data-ru="Выкуп (AH)" data-en="Buyout (AH)">Выкуп (AH)</th>
                            <th class="i18n" data-ru="Вендор" data-en="Vendor">Вендор</th>
                            <th class="i18n" data-ru="Прибыль" data-en="Profit">Прибыль</th>
                            <th class="i18n" data-ru="Реалм" data-en="Realm">Реалм</th>
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
                <h2 class="i18n" data-ru="Крафт под Waylaid Crates" data-en="Waylaid Crates Crafting">Крафт под Waylaid Crates</h2>
                <span class="stat-pill i18n" data-ru="Порог ROI: ≥ 25% (Bartering + Master Chef)" data-en="ROI threshold: ≥ 25% (Bartering + Master Chef)">Порог ROI: ≥ 25% (Bartering + Master Chef)</span>
            </div>
            <div class="table-responsive">
                <table class="sortable">
                    <thead>
                        <tr>
                            <th class="i18n" data-ru="Рецепт / Предмет" data-en="Recipe / Item">Рецепт / Предмет</th>
                            <th class="i18n" data-ru="Профессия" data-en="Profession">Профессия</th>
                            <th class="i18n" data-ru="Ящик Waylaid Crate" data-en="Waylaid Crate">Ящик Waylaid Crate</th>
                            <th class="i18n" data-ru="Себестоимость" data-en="Craft Cost">Себестоимость</th>
                            <th class="i18n" data-ru="Выручка (AH)" data-en="Revenue (AH)">Выручка (AH)</th>
                            <th class="i18n" data-ru="Чистая прибыль" data-en="Net Profit">Чистая прибыль</th>
                            <th class="i18n" data-ru="ROI" data-en="ROI">ROI</th>
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
                <h2 class="i18n" data-ru="Аномальные сбросы цен" data-en="Price Anomalies">Аномальные сбросы цен</h2>
                <span class="stat-pill i18n" data-ru="Z-Score сигналы (STRONG_BUY / STRONG_SELL)" data-en="Z-Score Signals (STRONG_BUY / STRONG_SELL)">Z-Score сигналы (STRONG_BUY / STRONG_SELL)</span>
            </div>
            <div class="table-responsive">
                <table class="sortable">
                    <thead>
                        <tr>
                            <th class="i18n" data-ru="Предмет" data-en="Item">Предмет</th>
                            <th class="i18n" data-ru="Сигнал" data-en="Signal">Сигнал</th>
                            <th class="i18n" data-ru="Текущая цена" data-en="Current Price">Текущая цена</th>
                            <th class="i18n" data-ru="Средняя цена" data-en="Mean Price">Средняя цена</th>
                            <th class="i18n" data-ru="Z-Score" data-en="Z-Score">Z-Score</th>
                            <th class="i18n" data-ru="Замеров" data-en="Samples">Замеров</th>
                            <th class="i18n" data-ru="Реалм" data-en="Realm">Реалм</th>
                        </tr>
                    </thead>
                    <tbody>
                        {anomaly_tbody}
                    </tbody>
                </table>
            </div>
        </section>
    </div>

    <!-- Personal Inspector Modal -->
    <div id="item-modal" class="modal-backdrop" style="display: none;" onclick="onModalBackdropClick(event)">
        <div class="modal-content" onclick="event.stopPropagation()">
            <div class="modal-header">
                <div class="modal-title-wrap">
                    <h3 id="modal-item-name" class="modal-title">Item Name</h3>
                    <button id="modal-copy-btn" class="copy-btn" title="Копировать название / Copy name" onclick="copyItemName(this, event)">📋</button>
                </div>
                <button class="modal-close-btn" onclick="closeItemModal()" title="Закрыть / Close">✕</button>
            </div>
            <div class="modal-stats-grid">
                <div class="modal-stat-card">
                    <span class="stat-label i18n" data-ru="Текущая цена" data-en="Current Price">Текущая цена</span>
                    <span id="modal-stat-current" class="stat-value">—</span>
                </div>
                <div class="modal-stat-card">
                    <span class="stat-label i18n" data-ru="Средняя цена" data-en="Mean Price">Средняя цена</span>
                    <span id="modal-stat-mean" class="stat-value">—</span>
                </div>
                <div class="modal-stat-card">
                    <span class="stat-label i18n" data-ru="Мин. цена" data-en="Min Price">Мин. цена</span>
                    <span id="modal-stat-min" class="stat-value">—</span>
                </div>
                <div class="modal-stat-card">
                    <span class="stat-label i18n" data-ru="Макс. цена" data-en="Max Price">Макс. цена</span>
                    <span id="modal-stat-max" class="stat-value">—</span>
                </div>
                <div class="modal-stat-card">
                    <span class="stat-label i18n" data-ru="Z-Score" data-en="Z-Score">Z-Score</span>
                    <span id="modal-stat-zscore" class="stat-value badge">—</span>
                </div>
            </div>
            <div id="modal-plot-container" class="modal-plot-container"></div>
        </div>
    </div>

    <!-- Client-side Interactive Logic -->
    <script>
        var ITEM_STORE = {items_store_json};
        var currentModalItemId = null;
        var currentFilterCat = 'all';
        var currentSearchQuery = '';

        function getStoredLang() {{
            try {{
                return localStorage.getItem('ft_lang') || 'ru';
            }} catch(e) {{
                return 'ru';
            }}
        }}

        function setStoredLang(lang) {{
            try {{
                localStorage.setItem('ft_lang', lang);
            }} catch(e) {{}}
        }}

        function setLanguage(lang) {{
            lang = (lang === 'en') ? 'en' : 'ru';
            setStoredLang(lang);

            var btnRu = document.getElementById('lang-ru');
            var btnEn = document.getElementById('lang-en');
            if (btnRu) btnRu.classList.toggle('active', lang === 'ru');
            if (btnEn) btnEn.classList.toggle('active', lang === 'en');

            // Update all translatable elements
            document.querySelectorAll('[data-' + lang + ']').forEach(function(el) {{
                var txt = el.getAttribute('data-' + lang);
                if (txt !== null) {{
                    if (el.children.length === 0 || el.classList.contains('item-name') || el.classList.contains('empty-state')) {{
                        el.textContent = txt;
                    }} else {{
                        var child = el.querySelector('.i18n-text');
                        if (child) {{
                            child.textContent = txt;
                        }} else if (el.childNodes.length > 0 && el.childNodes[0].nodeType === 3) {{
                            el.childNodes[0].nodeValue = txt;
                        }}
                    }}
                }}
            }});

            // Update placeholders
            document.querySelectorAll('[data-' + lang + '-placeholder]').forEach(function(el) {{
                var ph = el.getAttribute('data-' + lang + '-placeholder');
                if (ph !== null) el.placeholder = ph;
            }});

            // Update select dropdown options
            var select = document.getElementById('item-select');
            if (select) {{
                Array.from(select.options).forEach(function(opt) {{
                    var val = opt.getAttribute('data-' + lang);
                    if (val) opt.textContent = val;
                }});
            }}

            // Update overview chart title if Plotly div exists
            try {{
                var chartEl = document.querySelector('#charts-section .plotly-graph-div');
                if (chartEl && window.Plotly) {{
                    var chartTitle = (lang === 'en') ? 'Key Reagents & Consumables Price History' : 'Динамика цен ключевого сырья и расходников';
                    Plotly.relayout(chartEl, {{ 'title.text': chartTitle }});
                }}
            }} catch(e) {{}}

            // If modal is currently open, refresh its contents in new language
            if (currentModalItemId && ITEM_STORE[currentModalItemId]) {{
                openItemModal(currentModalItemId);
            }}
        }}

        function formatCurrencyHtml(copper) {{
            if (copper === null || isNaN(copper)) copper = 0;
            copper = Math.round(Number(copper));
            var sign = copper < 0 ? "-" : "";
            var abs = Math.abs(copper);
            var g = Math.floor(abs / 10000);
            var s = Math.floor((abs % 10000) / 100);
            var c = abs % 100;
            return sign + '<span class="money-gold">' + g + 'g</span> ' +
                          '<span class="money-silver">' + s + 's</span> ' +
                          '<span class="money-copper">' + c + 'c</span>';
        }}

        function formatCurrencyText(copper) {{
            if (copper === null || isNaN(copper)) copper = 0;
            copper = Math.round(Number(copper));
            var sign = copper < 0 ? "-" : "";
            var abs = Math.abs(copper);
            var g = Math.floor(abs / 10000);
            var s = Math.floor((abs % 10000) / 100);
            var c = abs % 100;
            return sign + g + 'g ' + s + 's ' + c + 'c';
        }}

        function copyItemName(btn, event) {{
            if (event) event.stopPropagation();
            var wrapper = btn.closest('.item-cell-wrap') || btn.closest('.modal-title-wrap') || btn.closest('td') || btn.parentElement;
            var nameEl = wrapper ? wrapper.querySelector('.item-name') : null;
            var textToCopy = '';
            if (nameEl) {{
                textToCopy = (nameEl.innerText || nameEl.textContent || '').trim();
            }} else if (btn.getAttribute('data-name')) {{
                textToCopy = btn.getAttribute('data-name').trim();
            }}
            if (!textToCopy) return;

            var currentLang = getStoredLang();
            var successText = currentLang === 'en' ? '✓ Copied!' : '✓ Скопировано!';
            var origHtml = btn.innerHTML;
            var origTitle = btn.title;

            function showSuccess() {{
                btn.innerHTML = successText;
                btn.classList.add('copied');
                setTimeout(function() {{
                    btn.innerHTML = origHtml;
                    btn.title = origTitle;
                    btn.classList.remove('copied');
                }}, 1500);
            }}

            if (navigator.clipboard && navigator.clipboard.writeText) {{
                navigator.clipboard.writeText(textToCopy).then(showSuccess).catch(function() {{
                    fallbackCopy(textToCopy, showSuccess);
                }});
            }} else {{
                fallbackCopy(textToCopy, showSuccess);
            }}
        }}

        function fallbackCopy(text, cb) {{
            var ta = document.createElement('textarea');
            ta.value = text;
            ta.style.position = 'fixed';
            ta.style.left = '-9999px';
            document.body.appendChild(ta);
            ta.select();
            try {{
                document.execCommand('copy');
                if (cb) cb();
            }} catch(e) {{}}
            document.body.removeChild(ta);
        }}

        function onRowClick(event, itemId) {{
            if (event.target.closest('button') || event.target.closest('a')) return;
            if (itemId) openItemModal(itemId);
        }}

        function openItemModal(itemId, event) {{
            if (event) event.stopPropagation();
            itemId = parseInt(itemId);
            var item = ITEM_STORE[itemId];
            if (!item) return;

            currentModalItemId = itemId;
            var modal = document.getElementById('item-modal');
            if (!modal) return;

            var lang = getStoredLang();
            var activeName = (lang === 'en' ? item.name_en : item.name_ru) || item.name_ru || item.name_en || ('Item ' + itemId);

            var titleEl = document.getElementById('modal-item-name');
            if (titleEl) {{
                titleEl.textContent = activeName;
                titleEl.className = 'modal-title q-' + (item.quality || 1);
                titleEl.setAttribute('data-ru', item.name_ru || activeName);
                titleEl.setAttribute('data-en', item.name_en || activeName);
            }}
            var modalCopyBtn = document.getElementById('modal-copy-btn');
            if (modalCopyBtn) {{
                modalCopyBtn.setAttribute('data-name', activeName);
            }}

            var statCurr = document.getElementById('modal-stat-current');
            if (statCurr) statCurr.innerHTML = formatCurrencyHtml(item.current_price);

            var statMean = document.getElementById('modal-stat-mean');
            if (statMean) statMean.innerHTML = formatCurrencyHtml(item.mean_price);

            var statMin = document.getElementById('modal-stat-min');
            if (statMin) statMin.innerHTML = formatCurrencyHtml(item.min_price);

            var statMax = document.getElementById('modal-stat-max');
            if (statMax) statMax.innerHTML = formatCurrencyHtml(item.max_price);

            var statZ = document.getElementById('modal-stat-zscore');
            if (statZ) {{
                var zVal = item.z_score || 0.0;
                statZ.textContent = (zVal >= 0 ? '+' : '') + zVal.toFixed(2);
                statZ.className = zVal <= -1.8 ? 'badge badge-buy' : (zVal >= 1.6 ? 'badge badge-sell' : 'badge');
            }}

            var plotContainer = document.getElementById('modal-plot-container');
            if (plotContainer) {{
                if (!item.points || item.points.length === 0) {{
                    var emptyMsg = lang === 'en' 
                        ? 'Waiting for first price snapshot from Auctionator.lua...' 
                        : 'База данных ожидает первого снимка цен из Auctionator.lua...';
                    plotContainer.innerHTML = '<div class="empty-state i18n" data-ru="База данных ожидает первого снимка цен из Auctionator.lua..." data-en="Waiting for first price snapshot from Auctionator.lua...">' + emptyMsg + '</div>';
                }} else {{
                    plotContainer.innerHTML = '';
                    var xVals = [];
                    var yVals = [];
                    var hoverTexts = [];
                    item.points.forEach(function(pt) {{
                        var ts = pt[0];
                        var p = pt[1];
                        var dt = new Date(ts * 1000);
                        var dtStr = dt.toISOString().slice(0, 16).replace('T', ' ');
                        xVals.push(dtStr);
                        yVals.push(p);
                        hoverTexts.push(activeName + '<br>' + formatCurrencyText(p));
                    }});

                    var traces = [
                        {{
                            x: xVals,
                            y: yVals,
                            mode: 'lines+markers',
                            name: activeName,
                            line: {{ color: '#58a6ff', width: 2 }},
                            marker: {{ size: 5 }},
                            hovertext: hoverTexts,
                            hoverinfo: 'text+x'
                        }}
                    ];

                    if (item.mean_price && item.points.length > 1) {{
                        traces.push({{
                            x: [xVals[0], xVals[xVals.length - 1]],
                            y: [item.mean_price, item.mean_price],
                            mode: 'lines',
                            name: (lang === 'en' ? 'Mean Price: ' : 'Средняя цена: ') + formatCurrencyText(item.mean_price),
                            line: {{ color: '#e3b341', width: 2, dash: 'dash' }},
                            hoverinfo: 'name'
                        }});
                    }}

                    var layout = {{
                        template: 'plotly_dark',
                        paper_bgcolor: '#161b22',
                        plot_bgcolor: '#0d1117',
                        font: {{ color: '#c9d1d9', family: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif" }},
                        title: {{ text: activeName, font: {{ size: 16, color: '#58a6ff' }} }},
                        xaxis: {{ title: (lang === 'en' ? 'Timestamp' : 'Время замера'), gridcolor: '#21262d', zerolinecolor: '#30363d' }},
                        yaxis: {{ title: (lang === 'en' ? 'Price (copper)' : 'Цена (медь)'), gridcolor: '#21262d', zerolinecolor: '#30363d' }},
                        hovermode: 'closest',
                        margin: {{ l: 55, r: 25, t: 45, b: 40 }},
                        height: 350,
                        legend: {{ orientation: 'h', yanchor: 'bottom', y: 1.02, xanchor: 'right', x: 1, bgcolor: 'rgba(22, 27, 34, 0.7)' }}
                    }};

                    Plotly.newPlot(plotContainer, traces, layout, {{ responsive: true, displayModeBar: false }});
                }}
            }}

            modal.style.display = 'flex';
        }}

        function closeItemModal() {{
            var modal = document.getElementById('item-modal');
            if (modal) modal.style.display = 'none';
            currentModalItemId = null;
        }}

        function onModalBackdropClick(event) {{
            if (event.target && event.target.id === 'item-modal') {{
                closeItemModal();
            }}
        }}

        function filterCategory(cat) {{
            currentFilterCat = cat;
            document.querySelectorAll('.cat-btn').forEach(function(b) {{
                b.classList.toggle('active', b.getAttribute('data-cat') === cat);
            }});
            applyItemSelectFilter();
        }}

        function onItemSearch(query) {{
            currentSearchQuery = (query || '').trim().toLowerCase();
            applyItemSelectFilter();
        }}

        function applyItemSelectFilter() {{
            var select = document.getElementById('item-select');
            if (!select) return;
            var options = select.querySelectorAll('option');

            options.forEach(function(opt) {{
                if (!opt.value) return;
                var cat = opt.getAttribute('data-category');
                var searchData = (opt.getAttribute('data-search') || '').toLowerCase();
                var catMatch = (currentFilterCat === 'all') || (cat === currentFilterCat);
                var searchMatch = !currentSearchQuery || (searchData.indexOf(currentSearchQuery) !== -1);

                if (catMatch && searchMatch) {{
                    opt.style.display = '';
                }} else {{
                    opt.style.display = 'none';
                }}
            }});
        }}

        function onSelectDropdownItem(val) {{
            if (val) {{
                openItemModal(val);
            }}
        }}

        document.addEventListener("DOMContentLoaded", function() {{
            var initialLang = getStoredLang();
            setLanguage(initialLang);

            var searchInput = document.getElementById('item-search-input');
            if (searchInput) {{
                searchInput.addEventListener('keydown', function(e) {{
                    if (e.key === 'Enter') {{
                        var select = document.getElementById('item-select');
                        var visibleOpt = Array.from(select.querySelectorAll('option')).find(function(o) {{
                            return o.value && o.style.display !== 'none';
                        }});
                        if (visibleOpt) {{
                            select.value = visibleOpt.value;
                            openItemModal(visibleOpt.value);
                        }}
                    }}
                }});
            }}

            document.addEventListener('keydown', function(e) {{
                if (e.key === 'Escape') closeItemModal();
            }});

            // Client-side Sorting Script
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
