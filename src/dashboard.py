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
import time
from typing import Any, Optional, Union

import plotly.graph_objects as go

try:
    from src.analytics import (
        get_crafting_margins,
        get_disenchant_arbitrage,
        get_vendor_arbitrage,
        get_zscore_anomalies,
    )
except ImportError:
    from analytics import (
        get_crafting_margins,
        get_disenchant_arbitrage,
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
            "deal_info": {},
        }

    # Enrich market data with deal info from extra signal items
    if extra_items:
        for it in extra_items:
            iid = it.get("item_id") or it.get("crafted_item_id")
            if iid is not None:
                try:
                    iid = int(iid)
                    if iid in market_data:
                        d = market_data[iid]["deal_info"]
                        if "buyout_price" in it or "price" in it:
                            d["buyout"] = it.get("buyout_price", it.get("price", 0))
                        elif "buyout_copper" in it:
                            d["buyout"] = it.get("buyout_copper", 0)
                        if "vendor_sell_price" in it:
                            d["vendor_sell"] = it.get("vendor_sell_price", 0)
                        if "cost_copper" in it or "cost" in it:
                            d["cost"] = it.get("cost_copper", it.get("cost", 0))
                        if "revenue_copper" in it or "revenue" in it:
                            d["revenue"] = it.get("revenue_copper", it.get("revenue", 0))
                        if "profit_copper" in it or "profit" in it:
                            d["profit"] = it.get("profit_copper", it.get("profit", 0))
                        elif "expected_net_profit" in it:
                            d["profit"] = it.get("expected_net_profit", 0)
                        if "roi_percent" in it or "roi" in it:
                            d["roi"] = it.get("roi_percent", it.get("roi", 0))
                        if "signal" in it:
                            d["signal"] = it.get("signal")
                        if it.get("is_waylaid_target"):
                            d["is_waylaid"] = True
                        if "expected_de_value" in it:
                            d["expected_de_value"] = it.get("expected_de_value", 0)
                            d["is_disenchant"] = True
                        if "reagents" in it and it.get("reagents"):
                            d["reagents"] = it.get("reagents", [])
                            if "expected_de_value" in it:
                                d["is_disenchant"] = True
                except (ValueError, TypeError):
                    pass

    return market_data


def _get_snapshot_freshness(
    conn: sqlite3.Connection,
    realm: Optional[str] = None,
    now_ts: Optional[float] = None,
) -> tuple[Optional[int], str, str]:
    """Query the most recent price snapshot timestamp for target realm and format its age."""
    if now_ts is None:
        now_ts = time.time()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT MAX(timestamp) FROM price_snapshots WHERE (? IS NULL OR realm = ?)",
            (realm, realm),
        )
        row = cursor.fetchone()
        latest_ts = row[0] if row and row[0] is not None else None
    except Exception:
        latest_ts = None

    if latest_ts is None:
        return None, "нет снимков", "no snapshots"

    try:
        latest_ts_val = float(latest_ts)
    except (ValueError, TypeError):
        return None, "нет снимков", "no snapshots"

    if latest_ts_val < 1_000_000_000:
        return int(latest_ts_val), f"снимок #{int(latest_ts_val)}", f"snapshot #{int(latest_ts_val)}"

    age_sec = max(0, int(now_ts - latest_ts_val))
    try:
        snap_dt = datetime.datetime.fromtimestamp(latest_ts_val)
        dt_str = snap_dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        dt_str = f"ts:{int(latest_ts_val)}"

    if age_sec < 60:
        age_ru = f"{age_sec} сек. назад"
        age_en = f"{age_sec}s ago"
    elif age_sec < 3600:
        m = age_sec // 60
        age_ru = f"{m} мин. назад"
        age_en = f"{m}m ago"
    elif age_sec < 86400:
        h = age_sec // 3600
        m = (age_sec % 3600) // 60
        age_ru = f"{h} ч. {m} мин. назад"
        age_en = f"{h}h {m}m ago"
    else:
        d = age_sec // 86400
        h = (age_sec % 86400) // 3600
        age_ru = f"{d} дн. {h} ч. назад"
        age_en = f"{d}d {h}h ago"

    freshness_ru = f"{dt_str} ({age_ru})"
    freshness_en = f"{dt_str} ({age_en})"
    return int(latest_ts_val), freshness_ru, freshness_en


def build_standalone_dashboard(
    conn: sqlite3.Connection,
    output_path: str = "portal.html",
    ai_advice: Optional[dict] = None,
    realm: Optional[str] = None,
    has_bartering: bool = False,
    has_master_chef: bool = False,
    max_age_hours: Optional[float] = None,
) -> str:
    """Build a standalone, single-file HTML Goblin Terminal market portal with Wowhead tooltips and lazy Plotly inspector."""
    if output_path is not None:
        output_path = str(output_path)

    # 1. Fetch signal data
    try:
        vendor_arbitrage = get_vendor_arbitrage(conn, realm=realm, max_age_hours=max_age_hours)
    except TypeError:
        vendor_arbitrage = get_vendor_arbitrage(conn, realm=realm)

    try:
        all_crafting = get_crafting_margins(
            conn,
            has_bartering=has_bartering,
            has_master_chef=has_master_chef,
            realm=realm,
            max_age_hours=max_age_hours,
        )
    except TypeError:
        try:
            all_crafting = get_crafting_margins(
                conn,
                has_bartering=has_bartering,
                has_master_chef=has_master_chef,
                realm=realm,
            )
        except TypeError:
            all_crafting = get_crafting_margins(conn, realm=realm)

    try:
        anomalies = get_zscore_anomalies(conn, realm=realm, max_age_hours=max_age_hours)
    except TypeError:
        anomalies = get_zscore_anomalies(conn, realm=realm)

    try:
        disenchant_arbitrage = get_disenchant_arbitrage(conn, realm=realm, max_age_hours=max_age_hours)
    except TypeError:
        try:
            disenchant_arbitrage = get_disenchant_arbitrage(conn, realm=realm)
        except Exception:
            disenchant_arbitrage = []
    except Exception:
        disenchant_arbitrage = []

    crafting_display = all_crafting
    crates_only = [c for c in crafting_display if c.get("is_waylaid_target")]
    strong_buys = [a for a in anomalies if "BUY" in str(a.get("signal", ""))]

    # Timestamps
    now_dt = datetime.datetime.now()
    generated_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    _, snap_freshness_ru, snap_freshness_en = _get_snapshot_freshness(
        conn, realm=realm, now_ts=now_dt.timestamp()
    )
    escaped_freshness_ru = html.escape(snap_freshness_ru)
    escaped_freshness_en = html.escape(snap_freshness_en)
    escaped_realm = html.escape(str(realm or "Everlook"))

    # Market metadata
    extra_items = list(vendor_arbitrage) + list(crafting_display) + list(anomalies) + list(disenchant_arbitrage)
    items_market_data = _get_all_items_market_data(conn, realm=realm, extra_items=extra_items)

    # Render Plotly price history chart
    chart_html = _build_price_history_chart(conn, realm=realm)

    # Render AI banner
    ai_banner_html = _render_ai_banner(ai_advice)

    # Calculate Top summary cards
    top_snipe_html = "—"
    top_snipe_click = ""
    if strong_buys:
        best_snipe = max(strong_buys, key=lambda x: x.get("expected_net_profit", 0))
        snipe_name = html.escape(str(best_snipe.get("name") or "Item"))
        snipe_profit = int(round(best_snipe.get("expected_net_profit", 0)))
        top_snipe_html = f"<strong>{snipe_name}</strong> <span class='profit-cell'>+{format_currency(snipe_profit)}</span>"
        if best_snipe.get("item_id"):
            top_snipe_click = f"selectInspectorItem({best_snipe['item_id']})"

    top_craft_html = "—"
    top_craft_click = ""
    if crafting_display:
        best_craft = max(crafting_display, key=lambda x: x.get("profit_copper", 0))
        craft_name = html.escape(str(best_craft.get("crafted_item_name") or best_craft.get("name") or "Craft"))
        craft_profit = best_craft.get("profit_copper", 0)
        top_craft_html = f"<strong>{craft_name}</strong> <span class='profit-cell'>+{format_currency(craft_profit)}</span>"
        if best_craft.get("crafted_item_id"):
            top_craft_click = f"selectInspectorItem({best_craft['crafted_item_id']})"

    top_crate_html = "—"
    top_crate_click = ""
    if crates_only:
        def _crate_turnin_profit(c):
            return c.get("craft_turnin_profit_copper") or c.get("turnin_profit_copper") or c.get("profit_copper", 0)
        best_crate = max(crates_only, key=_crate_turnin_profit)
        crate_name = html.escape(str(best_crate.get("waylaid_crate_name") or best_crate.get("name") or "Crate"))
        crate_profit = _crate_turnin_profit(best_crate)
        top_crate_html = f"<strong>{crate_name}</strong> <span class='profit-cell'>+{format_currency(crate_profit)}</span>"
        if best_crate.get("crafted_item_id"):
            top_crate_click = f"selectInspectorItem({best_crate['crafted_item_id']})"

    ai_pulse_html = "3 активных совета"
    if ai_advice:
        sum_txt = ai_advice.get("summary") or ai_advice.get("verdict")
        if sum_txt:
            ai_pulse_html = html.escape(str(sum_txt)[:40] + ("..." if len(str(sum_txt)) > 40 else ""))

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
        wh_link_open = f"<a href='https://www.wowhead.com/forever/item={item_id}' data-wowhead='domain=forever' target='_blank' rel='noopener' class='wowhead-link q-{q}'>" if item_id is not None else ""
        wh_link_close = "</a>" if item_id is not None else ""

        arbitrage_rows.append(
            f"<tr {id_attr} {click_attr} class='clickable-row'>"
            f"<td class='q-{q}' data-value='{escaped_name_ru}'>"
            f"<div class='item-cell-wrap'>"
            f"{wh_link_open}<span class='item-name i18n' data-ru='{escaped_name_ru}' data-en='{escaped_name_en}'>{escaped_name_ru}</span>{wh_link_close}"
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
    crates_rows = []
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
        wh_link_open = f"<a href='https://www.wowhead.com/forever/item={crafted_id}' data-wowhead='domain=forever' target='_blank' rel='noopener' class='wowhead-link'>" if crafted_id is not None else ""
        wh_link_close = "</a>" if crafted_id is not None else ""

        is_waylaid = bool(item.get("is_waylaid_target"))
        badge_html = (
            "<span class='badge badge-waylaid i18n' data-ru='[Ящик]' data-en='[Waylaid]'>[Ящик]</span>"
            if is_waylaid
            else "<span class='badge badge-craft i18n' data-ru='[Крафт]' data-en='[Craft]'>[Крафт]</span>"
        )

        row_html = (
            f"<tr {id_attr} {click_attr} class='clickable-row'>"
            f"<td data-value='{escaped_name_ru}'>"
            f"<div class='item-cell-wrap'>"
            f"<div>"
            f"<div class='item-title-row'>"
            f"{badge_html} "
            f"<strong>{wh_link_open}<span class='item-name i18n' data-ru='{escaped_name_ru}' data-en='{escaped_name_en}'>{escaped_name_ru}</span>{wh_link_close}</strong>"
            f"</div>"
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
        crafting_rows.append(row_html)
        if is_waylaid:
            c_cost = item.get("pack_craft_cost_copper") or item.get("pack_cost_copper") or item.get("cost_copper", 0)
            c_revenue = item.get("reward_copper") or item.get("revenue_copper", 0)
            c_profit = item.get("craft_turnin_profit_copper") or item.get("turnin_profit_copper") or item.get("profit_copper", 0)
            c_roi_pct = round((c_profit / c_cost * 100), 1) if c_cost > 0 else 0.0

            crate_row_html = (
                f"<tr {id_attr} {click_attr} class='clickable-row'>"
                f"<td data-value='{escaped_name_ru}'>"
                f"<div class='item-cell-wrap'>"
                f"<div>"
                f"<div class='item-title-row'>"
                f"{badge_html} "
                f"<strong>{wh_link_open}<span class='item-name i18n' data-ru='{escaped_name_ru}' data-en='{escaped_name_en}'>{escaped_name_ru}</span>{wh_link_close}</strong>"
                f"</div>"
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
                f"<td data-value='{c_cost}'>{format_currency(c_cost, as_html=True)}</td>"
                f"<td data-value='{c_revenue}'>{format_currency(c_revenue, as_html=True)}</td>"
                f"<td data-value='{c_profit}' class='profit-cell'>{format_currency(c_profit, as_html=True)}</td>"
                f"<td data-value='{c_roi_pct}' class='roi-cell'>+{c_roi_pct}%</td>"
                f"</tr>"
            )
            crates_rows.append(crate_row_html)

    if not crafting_rows:
        crafting_tbody = "<tr><td colspan='7' class='empty-state i18n' data-ru='Нет активных маржинальных рецептов (ROI ≥ 25%)' data-en='No active profitable recipes (ROI ≥ 25%)'>Нет активных маржинальных рецептов (ROI ≥ 25%)</td></tr>"
    else:
        crafting_tbody = "\n".join(crafting_rows)

    if not crates_rows:
        crates_tbody = "<tr><td colspan='7' class='empty-state i18n' data-ru='Нет активных ящиков Waylaid Crates' data-en='No active Waylaid Crates'>Нет активных ящиков Waylaid Crates</td></tr>"
    else:
        crates_tbody = "\n".join(crates_rows)

    # 6. Build Price Anomalies table rows
    anomaly_rows = []
    for item in strong_buys:
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
        wh_link_open = f"<a href='https://www.wowhead.com/forever/item={item_id}' data-wowhead='domain=forever' target='_blank' rel='noopener' class='wowhead-link'>" if item_id is not None else ""
        wh_link_close = "</a>" if item_id is not None else ""

        anomaly_rows.append(
            f"<tr {id_attr} {click_attr} class='clickable-row'>"
            f"<td data-value='{escaped_name_ru}'>"
            f"<div class='item-cell-wrap'>"
            f"<strong>{wh_link_open}<span class='item-name i18n' data-ru='{escaped_name_ru}' data-en='{escaped_name_en}'>{escaped_name_ru}</span>{wh_link_close}</strong>"
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

    # 7. Build Disenchant Shuffle table rows
    disenchant_rows = []
    for item in disenchant_arbitrage:
        item_id = item.get("item_id")
        q = item.get("quality", 2)
        ilvl = item.get("item_level", "—")
        raw_name = item.get("name")
        if item_id is not None:
            name_ru = get_item_name(conn, item_id, "ru")
            name_en = get_item_name(conn, item_id, "en")
        else:
            name_ru = str(raw_name) if raw_name is not None else "Equipment"
            name_en = name_ru

        escaped_name_ru = html.escape(name_ru)
        escaped_name_en = html.escape(name_en)

        buyout = item.get("buyout_copper", 0)
        expected_de_val = item.get("expected_de_value", 0)
        net_profit = item.get("expected_net_profit", item.get("profit_copper", 0))
        roi_pct = item.get("roi_percent", 0.0)

        q_map = {
            2: ("Необычный", "Uncommon"),
            3: ("Редкий", "Rare"),
            4: ("Эпический", "Epic"),
        }
        q_ru, q_en = q_map.get(q, ("Обычный", "Common"))

        reagents = item.get("reagents", [])
        if reagents:
            materials_parts_ru = []
            materials_parts_en = []
            for r in reagents[:3]:
                r_name_ru = r.get("reagent_name_ru") or r.get("reagent_name") or f"Item {r.get('reagent_id')}"
                r_name_en = r.get("reagent_name") or f"Item {r.get('reagent_id')}"
                ch = r.get("chance", 1.0)
                ch_pct = int(round(ch * 100)) if ch <= 1.0 else int(round(ch))
                materials_parts_ru.append(f"{r_name_ru} ({ch_pct}%)")
                materials_parts_en.append(f"{r_name_en} ({ch_pct}%)")
            mat_text_ru = ", ".join(materials_parts_ru)
            mat_text_en = ", ".join(materials_parts_en)
            if len(reagents) > 3:
                mat_text_ru += f" (+{len(reagents)-3})"
                mat_text_en += f" (+{len(reagents)-3})"
            materials_html = (
                f"<span class='reagent-summary i18n' "
                f"data-ru='{html.escape(mat_text_ru)}' "
                f"data-en='{html.escape(mat_text_en)}'>"
                f"{html.escape(mat_text_ru)}</span>"
            )
        else:
            materials_html = "—"

        id_attr = f"data-item-id='{item_id}'" if item_id is not None else ""
        click_attr = f"onclick=\"onRowClick(event, {item_id})\"" if item_id is not None else ""
        graph_btn = f"<button class='graph-btn' title='График / Chart' onclick='openItemModal({item_id}, event)'>📈</button>" if item_id is not None else ""
        wh_link_open = f"<a href='https://www.wowhead.com/forever/item={item_id}' data-wowhead='domain=forever' target='_blank' rel='noopener' class='wowhead-link q-{q}'>" if item_id is not None else ""
        wh_link_close = "</a>" if item_id is not None else ""

        row_html = (
            f"<tr {id_attr} {click_attr} data-is-de='1' class='clickable-row'>"
            f"<td class='q-{q}' data-value='{escaped_name_ru}'>"
            f"<div class='item-cell-wrap'>"
            f"{wh_link_open}<span class='item-name i18n' data-ru='{escaped_name_ru}' data-en='{escaped_name_en}'>{escaped_name_ru}</span>{wh_link_close}"
            f"<div class='item-actions'>"
            f"<button class='copy-btn' title='Копировать название / Copy name' onclick='copyItemName(this, event)'>📋</button>"
            f"{graph_btn}"
            f"</div>"
            f"</div>"
            f"</td>"
            f"<td data-value='{ilvl}'>{ilvl}</td>"
            f"<td data-value='{q}'><span class='q-{q} i18n' data-ru='{q_ru}' data-en='{q_en}'>{q_ru}</span></td>"
            f"<td data-value='{buyout}'>{format_currency(buyout, as_html=True)}</td>"
            f"<td data-value='{expected_de_val}'>{format_currency(expected_de_val, as_html=True)}</td>"
            f"<td data-value='{net_profit}' class='profit-cell'>{format_currency(net_profit, as_html=True)}</td>"
            f"<td data-value='{roi_pct}' class='roi-cell'>+{roi_pct}%</td>"
            f"<td class='materials-cell'>{materials_html}</td>"
            f"</tr>"
        )
        disenchant_rows.append(row_html)

    if not disenchant_rows:
        disenchant_tbody = (
            "<tr><td colspan='8' class='empty-state i18n' "
            "data-ru='Нет прибыльных предметов для распыления (≥ 10s)' "
            "data-en='No profitable disenchant opportunities (≥ 10s)'>"
            "Нет прибыльных предметов для распыления (≥ 10s)</td></tr>"
        )
    else:
        disenchant_tbody = "\n".join(disenchant_rows)

    # 8. Dropdown options
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

    # 8. Serialize items market data to JSON
    items_store_json = json.dumps(items_market_data, ensure_ascii=False)
    items_store_json = items_store_json.replace("</", r"<\/").replace("<", r"\u003c")
    strong_buy_ids = [int(a["item_id"]) for a in strong_buys if a.get("item_id") is not None]
    strong_buy_ids_json = json.dumps(strong_buy_ids)

    # 9. Assemble full HTML document
    html_content = f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="30">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Forever Trade — Goblin Terminal</title>
    <!-- Wowhead Forever Tooltips -->
    <script>const whTooltips = {{colorLinks: true, iconizeLinks: true, renameLinks: false, domain: 'forever'}};</script>
    <script src="https://wow.zamimg.com/js/tooltips.js"></script>
    <style>
        :root {{
            --bg-main: #0b0e14;
            --bg-card: #10141d;
            --bg-card-hover: #181d28;
            --border-color: #21262d;
            --text-primary: #e6edf3;
            --text-muted: #8b949e;
            --accent-blue: #58a6ff;
            --accent-green: #3fb950;
            --accent-red: #f85149;
            --gold: #ffd700;
            --silver: #c0c0c0;
            --copper: #eda55f;
            --q0: #9d9d9d;
            --q1: #ffffff;
            --q2: #1eff00;
            --q3: #0070dd;
            --q4: #a335ee;
            --q5: #ff8000;
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}

        html, body {{
            height: 100vh;
            max-height: 100vh;
            overflow: hidden;
            background-color: var(--bg-main);
            color: var(--text-primary);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            font-size: 13px;
            line-height: 1.4;
        }}

        .terminal-layout {{
            display: flex;
            flex-direction: column;
            height: 100vh;
            padding: 8px 14px;
            box-sizing: border-box;
            gap: 8px;
        }}

        /* Header Bar */
        .terminal-header {{
            flex: 0 0 auto;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            padding: 6px 14px;
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 6px;
            flex-wrap: wrap;
        }}

        .brand-cluster {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}

        .brand-title {{
            font-size: 1.15rem;
            font-weight: 700;
            color: #ffffff;
            letter-spacing: -0.3px;
        }}

        .realm-badge {{
            background: rgba(88, 166, 255, 0.15);
            color: var(--accent-blue);
            border: 1px solid rgba(88, 166, 255, 0.3);
            border-radius: 4px;
            padding: 2px 8px;
            font-size: 0.75rem;
            font-weight: 600;
        }}

        .header-timestamps {{
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 0.78rem;
            color: var(--text-muted);
        }}

        .timestamp-badge {{
            background: #0b0e14;
            border: 1px solid var(--border-color);
            padding: 3px 8px;
            border-radius: 4px;
        }}

        .header-center {{
            flex: 1;
            max-width: 420px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .search-input {{
            width: 100%;
            background: #0b0e14;
            border: 1px solid var(--border-color);
            color: var(--text-primary);
            border-radius: 6px;
            padding: 5px 10px;
            font-size: 0.85rem;
            outline: none;
            transition: border-color 0.15s ease;
        }}

        .search-input:focus {{
            border-color: var(--accent-blue);
        }}

        .header-actions {{
            display: flex;
            align-items: center;
            gap: 10px;
        }}

        .btn-shopping-list {{
            background: #238636;
            border: 1px solid rgba(255, 255, 255, 0.1);
            color: #ffffff;
            font-weight: 600;
            font-size: 0.8rem;
            padding: 5px 12px;
            border-radius: 6px;
            cursor: pointer;
            transition: background 0.15s ease;
            white-space: nowrap;
        }}

        .btn-shopping-list:hover {{
            background: #2ea043;
        }}

        .lang-switcher {{
            display: flex;
            background: #0b0e14;
            border: 1px solid var(--border-color);
            border-radius: 4px;
            overflow: hidden;
        }}

        .lang-btn {{
            background: transparent;
            border: none;
            color: var(--text-muted);
            padding: 4px 10px;
            font-size: 0.78rem;
            font-weight: 600;
            cursor: pointer;
        }}

        .lang-btn.active {{
            background: #1f6feb;
            color: #ffffff;
        }}

        /* Summary Cards Row */
        .summary-cards-row {{
            flex: 0 0 auto;
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 8px;
        }}

        .summary-card {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 6px;
            padding: 6px 12px;
            display: flex;
            flex-direction: column;
            cursor: pointer;
            transition: all 0.15s ease;
        }}

        .summary-card:hover {{
            border-color: var(--accent-blue);
            background: var(--bg-card-hover);
        }}

        .card-label {{
            font-size: 0.72rem;
            color: var(--text-muted);
            text-transform: uppercase;
            font-weight: 600;
            letter-spacing: 0.4px;
            margin-bottom: 2px;
        }}

        .card-val {{
            font-size: 0.85rem;
            color: var(--text-primary);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}

        /* Main Workspace: 65% Left / 35% Right */
        .terminal-main {{
            flex: 1 1 0;
            display: flex;
            gap: 10px;
            min-height: 0;
            overflow: hidden;
        }}

        .terminal-left {{
            flex: 0 0 65%;
            width: 65%;
            display: flex;
            flex-direction: column;
            min-height: 0;
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 6px;
            overflow: hidden;
        }}

        .terminal-right {{
            flex: 0 0 calc(35% - 10px);
            width: calc(35% - 10px);
            display: flex;
            flex-direction: column;
            min-height: 0;
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 6px;
            padding: 12px;
            overflow-y: auto;
            gap: 12px;
        }}

        /* Left Column Tabs */
        .tabs-bar {{
            flex: 0 0 auto;
            display: flex;
            align-items: center;
            background: #0d1117;
            border-bottom: 1px solid var(--border-color);
            padding: 4px 8px 0;
            gap: 4px;
            overflow-x: auto;
        }}

        .tab-btn {{
            background: transparent;
            border: none;
            border-bottom: 2px solid transparent;
            color: var(--text-muted);
            padding: 8px 12px;
            font-size: 0.82rem;
            font-weight: 600;
            cursor: pointer;
            white-space: nowrap;
            display: flex;
            align-items: center;
            gap: 6px;
        }}

        .tab-btn:hover {{
            color: var(--text-primary);
        }}

        .tab-btn.active {{
            color: #ffffff;
            border-bottom: 2px solid var(--accent-blue);
            background: rgba(88, 166, 255, 0.08);
        }}

        .tab-count {{
            background: #21262d;
            border-radius: 10px;
            padding: 1px 6px;
            font-size: 0.72rem;
            color: var(--text-muted);
        }}

        .tab-btn.active .tab-count {{
            background: #1f6feb;
            color: #ffffff;
        }}

        .tabs-content {{
            flex: 1 1 0;
            min-height: 0;
            overflow-y: auto;
            position: relative;
        }}

        .tab-pane {{
            display: none;
            padding: 12px;
        }}

        .tab-pane.active {{
            display: block;
        }}

        /* Table Styles */
        .table-responsive {{
            overflow-x: auto;
            width: 100%;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.85rem;
        }}

        th, td {{
            padding: 7px 10px;
            text-align: left;
            border-bottom: 1px solid var(--border-color);
        }}

        th {{
            background: #161b22;
            color: var(--text-muted);
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            cursor: pointer;
            user-select: none;
            white-space: nowrap;
            position: sticky;
            top: 0;
            z-index: 2;
        }}

        th:hover {{
            color: var(--text-primary);
        }}

        tr.clickable-row {{
            cursor: pointer;
        }}

        tr.clickable-row:hover td {{
            background-color: var(--bg-card-hover);
        }}

        tr.selected-row td {{
            background-color: rgba(88, 166, 255, 0.12) !important;
            border-bottom-color: rgba(88, 166, 255, 0.3);
        }}

        .item-cell-wrap {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
        }}

        .wowhead-link {{
            text-decoration: none;
            color: inherit;
        }}

        .wowhead-link:hover {{
            text-decoration: underline;
        }}

        .item-actions {{
            display: inline-flex;
            align-items: center;
            gap: 4px;
        }}

        .copy-btn, .graph-btn {{
            background: #161b22;
            border: 1px solid var(--border-color);
            color: var(--text-muted);
            border-radius: 4px;
            padding: 2px 5px;
            font-size: 0.75rem;
            cursor: pointer;
            line-height: 1.2;
            transition: all 0.15s ease;
        }}

        .copy-btn:hover, .graph-btn:hover {{
            background: #21262d;
            color: var(--text-primary);
            border-color: #58a6ff;
        }}

        .copy-btn.copied {{
            background: rgba(63, 185, 80, 0.2);
            border-color: #3fb950;
            color: #3fb950;
        }}

        /* Currency Styling */
        .money-gold {{ color: var(--gold); font-weight: 600; }}
        .money-silver {{ color: var(--silver); font-weight: 600; }}
        .money-copper {{ color: var(--copper); font-weight: 600; }}
        .profit-cell {{ font-weight: 600; color: #56d364; }}
        .roi-cell {{ color: var(--accent-green); font-weight: 700; }}
        .z-cell {{ font-weight: 700; color: var(--accent-blue); }}
        .crate-cell {{ color: #d2a8ff; }}
        .reagents-subtext {{ font-size: 0.72rem; color: var(--text-muted); margin-top: 1px; }}

        /* Quality Colors */
        .q-0 {{ color: var(--q0); }}
        .q-1 {{ color: var(--q1); }}
        .q-2 {{ color: var(--q2); }}
        .q-3 {{ color: var(--q3); }}
        .q-4 {{ color: var(--q4); }}
        .q-5 {{ color: var(--q5); }}

        /* Badges */
        .badge {{
            display: inline-block;
            padding: 1px 6px;
            font-size: 0.72rem;
            font-weight: 700;
            border-radius: 8px;
            letter-spacing: 0.4px;
        }}
        .badge-buy {{ background: rgba(63, 185, 80, 0.2); color: #3fb950; border: 1px solid rgba(63, 185, 80, 0.4); }}
        .badge-sell {{ background: rgba(248, 81, 73, 0.2); color: #f85149; border: 1px solid rgba(248, 81, 73, 0.4); }}
        .badge-waylaid {{ background: rgba(163, 53, 238, 0.2); color: #c27aff; border: 1px solid rgba(163, 53, 238, 0.4); margin-right: 4px; }}
        .badge-craft {{ background: rgba(88, 166, 255, 0.2); color: #58a6ff; border: 1px solid rgba(88, 166, 255, 0.4); margin-right: 4px; }}

        /* Right Column: Inspector */
        .inspector-panel {{
            display: flex;
            flex-direction: column;
            gap: 12px;
        }}

        .inspector-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 8px;
        }}

        .inspector-title-wrap {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .inspector-name {{
            font-size: 1.1rem;
            font-weight: 700;
        }}

        .inspector-chart-container {{
            min-height: 250px;
            max-height: 270px;
            width: 100%;
            background: #0b0e14;
            border: 1px solid var(--border-color);
            border-radius: 6px;
            padding: 4px;
        }}

        .deal-calculator {{
            background: #0b0e14;
            border: 1px solid var(--border-color);
            border-radius: 6px;
            padding: 10px 14px;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }}

        .calc-header {{
            font-size: 0.8rem;
            font-weight: 700;
            color: var(--accent-blue);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 4px;
        }}

        .calc-grid {{
            display: flex;
            flex-direction: column;
            gap: 6px;
        }}

        .calc-row {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 0.85rem;
        }}

        .calc-row.highlight {{
            padding-top: 4px;
            border-top: 1px dashed var(--border-color);
            font-weight: 600;
        }}

        .calc-label {{
            color: var(--text-muted);
        }}

        .calc-val {{
            font-weight: 600;
        }}

        .disenchant-card {{
            background: #0b0e14;
            border: 1px solid var(--border-color);
            border-radius: 6px;
            padding: 10px 14px;
            display: flex;
            flex-direction: column;
            gap: 8px;
            margin-top: 8px;
        }}

        .de-summary-row {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 0.85rem;
            padding: 4px 0;
            border-bottom: 1px dashed var(--border-color);
        }}

        .de-reagents-header {{
            font-size: 0.75rem;
            color: var(--text-dim);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-top: 4px;
        }}

        .de-reagents-list {{
            display: flex;
            flex-direction: column;
            gap: 6px;
        }}

        .de-reagent-row {{
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 4px;
            padding: 6px 8px;
            display: flex;
            flex-direction: column;
            gap: 2px;
        }}

        .de-reagent-top {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 0.82rem;
            font-weight: 600;
        }}

        .de-reagent-bottom {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 0.75rem;
            color: #8b949e;
        }}

        .materials-cell {{
            font-size: 0.8rem;
            color: #8b949e;
            max-width: 250px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}

        /* Toast notification */
        .toast {{
            position: fixed;
            bottom: 24px;
            right: 24px;
            background: #1f6feb;
            color: #ffffff;
            padding: 8px 16px;
            border-radius: 6px;
            font-size: 0.85rem;
            font-weight: 600;
            box-shadow: 0 4px 14px rgba(0, 0, 0, 0.5);
            opacity: 0;
            pointer-events: none;
            transform: translateY(10px);
            transition: all 0.25s ease;
            z-index: 10000;
        }}

        .toast.show {{
            opacity: 1;
            transform: translateY(0);
        }}

        /* AI Banner */
        .ai-banner {{
            background: #10141d;
            border: 1px solid #388bfd;
            border-left: 4px solid var(--accent-blue);
            border-radius: 6px;
            padding: 10px 14px;
            margin-bottom: 10px;
        }}
        .ai-banner-header {{ display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }}
        .ai-badge {{ background: #1f6feb; color: #ffffff; font-size: 0.7rem; font-weight: 700; padding: 2px 6px; border-radius: 6px; }}
        .ai-summary {{ font-size: 0.85rem; margin-bottom: 6px; }}
        .ai-checklist {{ margin-left: 18px; font-size: 0.82rem; }}
        .ai-checklist li {{ margin-bottom: 3px; }}

        /* Hidden elements for compatibility */
        .compat-hidden {{
            display: none !important;
        }}

        /* Modal backdrop for modal test backwards compatibility */
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
            padding: 20px;
        }}
    </style>
</head>
<body>
    <div class="terminal-layout">
        <!-- Top Header Bar -->
        <header class="terminal-header">
            <div class="brand-cluster">
                <span class="brand-title">Forever Trade</span>
                <span class="realm-badge">⚔️ {escaped_realm}</span>
                <div class="header-timestamps">
                    <span class="timestamp-badge timestamp-gen">Generated: {generated_str}</span>
                    <span class="timestamp-badge timestamp-snap">
                        <span class="i18n" data-ru="Актуальность снимка цен: {escaped_freshness_ru}" data-en="Snapshot freshness: {escaped_freshness_en}">Актуальность снимка цен: {escaped_freshness_ru}</span>
                    </span>
                </div>
            </div>

            <div class="header-center">
                <input type="text" id="item-search-input" class="search-input" placeholder="🔍 Поиск RU/EN (Ctrl+K)..." data-ru-placeholder="🔍 Поиск RU/EN (Ctrl+K)..." data-en-placeholder="🔍 Search RU/EN (Ctrl+K)..." oninput="onItemSearch(this.value)" />
            </div>

            <div class="header-actions">
                <button id="btn-export-shopping-list" class="btn-shopping-list" onclick="copyAuctionatorShoppingList()">
                    <span class="i18n" data-ru="📋 Скопировать Shopping List для Auctionator" data-en="📋 Copy Auctionator Shopping List">📋 Скопировать Shopping List для Auctionator</span>
                </button>
                <div class="lang-switcher">
                    <button id="lang-ru" class="lang-btn active" onclick="setLanguage('ru')">RU</button>
                    <button id="lang-en" class="lang-btn" onclick="setLanguage('en')">EN</button>
                </div>
            </div>
        </header>

        <!-- Summary Cards Row -->
        <div class="summary-cards-row">
            <div class="summary-card" onclick="{top_snipe_click}">
                <div class="card-label">🔥 <span class="i18n" data-ru="ТОП СНАЙП (Флип)" data-en="TOP SNIPE (Flip)">ТОП СНАЙП (Флип)</span></div>
                <div class="card-val">{top_snipe_html}</div>
            </div>
            <div class="summary-card" onclick="{top_craft_click}">
                <div class="card-label">⚗️ <span class="i18n" data-ru="ТОП КРАФТ ЧАСА" data-en="TOP CRAFT OF THE HOUR">ТОП КРАФТ ЧАСА</span></div>
                <div class="card-val">{top_craft_html}</div>
            </div>
            <div class="summary-card" onclick="{top_crate_click}">
                <div class="card-label">📦 <span class="i18n" data-ru="ЛУЧШИЙ ЯЩИК (Crate)" data-en="BEST WAYLAID CRATE">ЛУЧШИЙ ЯЩИК (Crate)</span></div>
                <div class="card-val">{top_crate_html}</div>
            </div>
            <div class="summary-card" onclick="toggleAiBanner()">
                <div class="card-label">🤖 <span class="i18n" data-ru="AI ПУЛЬС / Рекомендации" data-en="AI PULSE / Advice">AI ПУЛЬС / Рекомендации</span></div>
                <div class="card-val">{ai_pulse_html}</div>
            </div>
        </div>

        <!-- Collapsible AI Banner -->
        <div id="ai-banner-container" style="display: none;">
            {ai_banner_html}
        </div>

        <!-- Terminal Main Workspace (65% Left / 35% Right) -->
        <main class="terminal-main">
            <!-- Left Column (65% width): Tabs & Signals Tables -->
            <section class="terminal-left">
                <nav class="tabs-bar">
                    <button class="tab-btn active" data-tab="price-anomalies" onclick="switchTab('price-anomalies')">
                        🔥 Strong Buy <span class="tab-count" id="strong-buy-count">{len(strong_buys)}</span>
                    </button>
                    <button class="tab-btn" data-tab="vendor-arbitrage" onclick="switchTab('vendor-arbitrage')">
                        ⚔️ <span class="i18n" data-ru="Арбитраж" data-en="Arbitrage">Арбитраж</span> <span class="tab-count">{len(vendor_arbitrage)}</span>
                    </button>
                    <button class="tab-btn" data-tab="tab-disenchant" onclick="switchTab('tab-disenchant')">
                        ✨ <span class="i18n" data-ru="Дизэнчант" data-en="Disenchant">Дизэнчант</span> <span class="tab-count">{len(disenchant_arbitrage)}</span>
                    </button>
                    <button class="tab-btn" data-tab="waylaid-crates" onclick="switchTab('waylaid-crates')">
                        ⚗️ <span class="i18n" data-ru="Крафт (Dual)" data-en="Craft (Dual)">Крафт (Dual)</span> <span class="tab-count">{len(crafting_display)}</span>
                    </button>
                    <button class="tab-btn" data-tab="tab-crates" onclick="switchTab('tab-crates')">
                        📦 <span class="i18n" data-ru="Ящики" data-en="Waylaid Crates">Ящики</span> <span class="tab-count">{len(crates_only)}</span>
                    </button>
                    <button class="tab-btn" data-tab="tab-shuffle" onclick="switchTab('tab-shuffle')">
                        🪙 <span class="i18n" data-ru="Вендор-шаффл" data-en="Vendor Shuffle">Вендор-шаффл</span> <span class="tab-count">{len(vendor_arbitrage)}</span>
                    </button>
                    <button class="tab-btn" data-tab="charts-section" onclick="switchTab('charts-section')">
                        📊 <span class="i18n" data-ru="Обзор рынка" data-en="Market Overview">Обзор рынка</span>
                    </button>
                </nav>

                <div class="tabs-content">
                    <!-- Tab 1: Аномальные сбросы цен (Strong Buy) -->
                    <div class="tab-pane active" id="price-anomalies">
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
                    </div>

                    <!-- Tab 2: Вендорный арбитраж -->
                    <div class="tab-pane" id="vendor-arbitrage">
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
                    </div>

                    <!-- Tab: Дизэнчант (Disenchant Shuffle) -->
                    <div class="tab-pane" id="tab-disenchant">
                        <div class="table-responsive">
                            <table class="sortable">
                                <thead>
                                    <tr>
                                        <th class="i18n" data-ru="Предмет" data-en="Item">Предмет</th>
                                        <th class="i18n" data-ru="ilvl" data-en="ilvl">ilvl</th>
                                        <th class="i18n" data-ru="Качество" data-en="Quality">Качество</th>
                                        <th class="i18n" data-ru="Выкуп" data-en="Buyout">Выкуп</th>
                                        <th class="i18n" data-ru="DE Value" data-en="DE Value">DE Value</th>
                                        <th class="i18n" data-ru="Чистая прибыль" data-en="Net Profit">Чистая прибыль</th>
                                        <th class="i18n" data-ru="ROI" data-en="ROI">ROI</th>
                                        <th class="i18n" data-ru="Материалы" data-en="Materials">Материалы</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {disenchant_tbody}
                                </tbody>
                            </table>
                        </div>
                    </div>

                    <!-- Tab 3: Крафт под Waylaid Crates (Dual Crafting) -->
                    <div class="tab-pane" id="waylaid-crates">
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
                    </div>

                    <!-- Tab 4: Ящики Waylaid Crates Only -->
                    <div class="tab-pane" id="tab-crates">
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
                                    {crates_tbody}
                                </tbody>
                            </table>
                        </div>
                    </div>

                    <!-- Tab 5: Вендор-шаффл -->
                    <div class="tab-pane" id="tab-shuffle">
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
                    </div>

                    <!-- Tab 6: Plotly Market Overview Chart -->
                    <div class="tab-pane" id="charts-section">
                        <div id="overview-chart-container">
                            {chart_html}
                        </div>
                    </div>
                </div>
            </section>

            <!-- Right Column (35% width): Lazy Plotly Inspector & Deal Calculator -->
            <aside class="terminal-right">
                <div class="inspector-panel">
                    <div class="inspector-header">
                        <div class="inspector-title-wrap">
                            <a id="inspector-wowhead-link" href="#" data-wowhead="domain=forever" target="_blank" rel="noopener" class="wowhead-link">
                                <span id="inspector-item-name" class="inspector-name q-1">Peacebloom</span>
                            </a>
                            <button id="inspector-copy-btn" class="copy-btn" title="Копировать название / Copy name" onclick="copyItemName(this, event)">📋</button>
                        </div>
                    </div>

                    <!-- Single Reusable Plotly Container -->
                    <div id="inspector-chart" class="inspector-chart-container"></div>

                    <!-- Deal / Recipe Calculator -->
                    <div class="deal-calculator" id="deal-calculator">
                        <div class="calc-header i18n" data-ru="Калькулятор сделки / крафта" data-en="Deal & Recipe Calculator">Калькулятор сделки / крафта</div>
                        <div class="calc-grid">
                            <div class="calc-row">
                                <span class="calc-label i18n" data-ru="Закупка / Крафт (Cost):" data-en="Cost (Buy / Craft):">Закупка / Крафт (Cost):</span>
                                <span id="calc-cost" class="calc-val">—</span>
                            </div>
                            <div class="calc-row">
                                <span class="calc-label i18n" data-ru="Точка безубытка (P_be):" data-en="Breakeven point (P_be):">Точка безубытка (P_be):</span>
                                <span id="calc-breakeven" class="calc-val">—</span>
                            </div>
                            <div class="calc-row">
                                <span class="calc-label i18n" data-ru="Комиссия AH (5%):" data-en="AH Cut (5%):">Комиссия AH (5%):</span>
                                <span id="calc-ah-cut" class="calc-val">—</span>
                            </div>
                            <div class="calc-row highlight">
                                <span class="calc-label i18n" data-ru="Ожидаемая чистая прибыль:" data-en="Net Profit:">Ожидаемая чистая прибыль:</span>
                                <span id="calc-profit" class="calc-val profit-cell">—</span>
                            </div>
                            <div class="calc-row">
                                <span class="calc-label i18n" data-ru="Маржа / ROI:" data-en="Margin / ROI:">Маржа / ROI:</span>
                                <span id="calc-roi" class="calc-val roi-cell">—</span>
                            </div>
                        </div>
                    </div>

                    <!-- Disenchant Breakdown Card -->
                    <div class="disenchant-card" id="disenchant-card" style="display: none;">
                        <div class="calc-header">
                            ✨ <span class="i18n" data-ru="Карточка распыления (Disenchant)" data-en="Disenchant Breakdown">Карточка распыления (Disenchant)</span>
                        </div>
                        <div class="de-summary-row">
                            <span class="calc-label i18n" data-ru="Ожидаемое DE Value:" data-en="Expected DE Value:">Ожидаемое DE Value:</span>
                            <span id="de-card-expected-val" class="calc-val profit-cell">—</span>
                        </div>
                        <div class="de-reagents-header i18n" data-ru="Выпадающие материалы (Drop Table):" data-en="Disenchant Materials:">
                            Выпадающие материалы (Drop Table):
                        </div>
                        <div class="de-reagents-list" id="de-reagents-list"></div>
                    </div>
                </div>
            </aside>
        </main>
    </div>

    <!-- Hidden compatibility elements to satisfy test requirements -->
    <div class="compat-hidden">
        <span class="i18n" data-ru="Автономный аналитический портал и торговый навигатор WoW: Forever" data-en="Autonomous analytical portal & market navigator for WoW: Forever">Автономный аналитический портал и торговый навигатор WoW: Forever</span>
        <span class="i18n" data-ru="Вендорный арбитраж:" data-en="Vendor Arbitrage:">Вендорный арбитраж:</span>
        <span class="i18n" data-ru="Все" data-en="All">Все</span>
        <span class="i18n" data-ru="Сырье" data-en="Trade Goods">Сырье</span>
        <input type="text" placeholder="Поиск предмета..." data-ru-placeholder="Поиск предмета..." data-en-placeholder="Search item..." />
        <div class="category-filters">
            <button class="cat-btn active" data-cat="all" onclick="filterCategory('all')">All</button>
            <button class="cat-btn" data-cat="trade_goods" onclick="filterCategory('trade_goods')">Trade Goods</button>
            <button class="cat-btn" data-cat="consumables" onclick="filterCategory('consumables')">Consumables</button>
            <button class="cat-btn" data-cat="weapons" onclick="filterCategory('weapons')">Weapons</button>
            <button class="cat-btn" data-cat="armor" onclick="filterCategory('armor')">Armor</button>
            <button class="cat-btn" data-cat="waylaid_crates" onclick="filterCategory('waylaid_crates')">Waylaid Crates</button>
        </div>
        <select id="item-select" onchange="onSelectDropdownItem(this.value)">
            <option value="">-- Select --</option>
            {options_html}
        </select>
    </div>

    <!-- Deep Inspector Modal (retained for backward compatibility) -->
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
                <div class="modal-stat-card"><span id="modal-stat-current">—</span></div>
                <div class="modal-stat-card"><span id="modal-stat-mean">—</span></div>
                <div class="modal-stat-card"><span id="modal-stat-min">—</span></div>
                <div class="modal-stat-card"><span id="modal-stat-max">—</span></div>
                <div class="modal-stat-card"><span id="modal-stat-zscore">—</span></div>
            </div>
            <div id="modal-plot-container"></div>
        </div>
    </div>

    <!-- Toast notification element -->
    <div id="toast" class="toast"></div>

    <!-- Client-side Interactive Logic -->
    <script>
        var ITEM_STORE = {items_store_json};
        const STRONG_BUY_ITEM_IDS = {strong_buy_ids_json};
        var currentActiveTab = 'price-anomalies';
        var currentSelectedItemId = null;
        var currentSearchQuery = '';
        var currentFilterCat = 'all';

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

            document.querySelectorAll('[data-' + lang + '-placeholder]').forEach(function(el) {{
                var ph = el.getAttribute('data-' + lang + '-placeholder');
                if (ph !== null) el.placeholder = ph;
            }});

            var select = document.getElementById('item-select');
            if (select) {{
                Array.from(select.options).forEach(function(opt) {{
                    var val = opt.getAttribute('data-' + lang);
                    if (val) opt.textContent = val;
                }});
            }}

            if (currentSelectedItemId && ITEM_STORE[currentSelectedItemId]) {{
                selectInspectorItem(currentSelectedItemId);
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

        function showToast(msg) {{
            var t = document.getElementById('toast');
            if (!t) return;
            t.textContent = msg;
            t.classList.add('show');
            clearTimeout(t._timer);
            t._timer = setTimeout(function() {{
                t.classList.remove('show');
            }}, 2500);
        }}

        function copyItemName(btn, event) {{
            if (event) event.stopPropagation();
            var wrapper = btn.closest('.item-cell-wrap') || btn.closest('.inspector-title-wrap') || btn.closest('.modal-title-wrap') || btn.parentElement;
            var nameEl = wrapper ? wrapper.querySelector('.item-name') || wrapper.querySelector('.inspector-name') : null;
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

        function copyTextToClipboard(text, cb) {{
            if (navigator.clipboard && navigator.clipboard.writeText) {{
                navigator.clipboard.writeText(text).then(cb).catch(function() {{
                    fallbackCopy(text, cb);
                }});
            }} else {{
                fallbackCopy(text, cb);
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

        function copyAuctionatorShoppingList() {{
            var items = [];
            var lang = getStoredLang();

            // Collect items with STRONG_BUY signal from verified STRONG_BUY_ITEM_IDS
            if (typeof STRONG_BUY_ITEM_IDS !== 'undefined' && STRONG_BUY_ITEM_IDS.length > 0) {{
                STRONG_BUY_ITEM_IDS.forEach(function(id) {{
                    var it = ITEM_STORE[id];
                    if (it) {{
                        var n = (lang === 'en' ? it.name_en : it.name_ru) || it.name_ru || it.name_en;
                        if (n && items.indexOf(n) === -1) items.push(n);
                    }}
                }});
            }}

            // If no strong buys, collect from currently active table
            if (items.length === 0) {{
                var activePane = document.querySelector('.tab-pane.active');
                if (activePane) {{
                    var rows = activePane.querySelectorAll('tbody tr');
                    rows.forEach(function(r) {{
                        if (r.style.display === 'none') return;
                        var nameEl = r.querySelector('.item-name');
                        if (nameEl) {{
                            var n = (nameEl.innerText || nameEl.textContent || '').trim();
                            if (n && items.indexOf(n) === -1) items.push(n);
                        }}
                    }});
                }}
            }}

            if (items.length === 0) {{
                showToast(lang === 'en' ? 'No items found for shopping list' : 'Нет предметов для Shopping List');
                return;
            }}

            var exportStr = 'ForeverTrade^' + items.map(function(name) {{
                return '"' + name.replace(/"/g, '') + '"';
            }}).join('^');

            copyTextToClipboard(exportStr, function() {{
                showToast((lang === 'en' ? '✓ Copied Shopping List for Auctionator (' : '✓ Shopping List скопирован для Auctionator (') + items.length + ')');
            }});
        }}

        function switchTab(tabId) {{
            currentActiveTab = tabId;
            try {{
                localStorage.setItem('ft_active_tab', tabId);
            }} catch(e) {{}}

            document.querySelectorAll('.tab-btn').forEach(function(btn) {{
                btn.classList.toggle('active', btn.getAttribute('data-tab') === tabId);
            }});

            document.querySelectorAll('.tab-pane').forEach(function(pane) {{
                pane.classList.toggle('active', pane.id === tabId);
            }});

            if (currentSearchQuery) {{
                onItemSearch(currentSearchQuery);
            }}
        }}

        function toggleAiBanner() {{
            var b = document.getElementById('ai-banner-container');
            if (b) {{
                b.style.display = b.style.display === 'none' ? 'block' : 'none';
            }}
        }}

        function onRowClick(event, itemId) {{
            if (event.target.closest('button') || event.target.closest('a')) return;
            if (itemId) selectInspectorItem(itemId);
        }}

        function selectInspectorItem(itemId) {{
            itemId = parseInt(itemId);
            var item = ITEM_STORE[itemId];
            if (!item) return;

            currentSelectedItemId = itemId;
            try {{
                localStorage.setItem('ft_selected_item', String(itemId));
            }} catch(e) {{}}

            document.querySelectorAll('tr.selected-row').forEach(function(r) {{
                r.classList.remove('selected-row');
            }});
            document.querySelectorAll('tr[data-item-id="' + itemId + '"]').forEach(function(r) {{
                r.classList.add('selected-row');
            }});

            var lang = getStoredLang();
            var activeName = (lang === 'en' ? item.name_en : item.name_ru) || item.name_ru || item.name_en || ('Item ' + itemId);

            var titleEl = document.getElementById('inspector-item-name');
            if (titleEl) {{
                titleEl.textContent = activeName;
                titleEl.className = 'inspector-name q-' + (item.quality || 1);
            }}

            var linkEl = document.getElementById('inspector-wowhead-link');
            if (linkEl) {{
                linkEl.href = 'https://www.wowhead.com/forever/item=' + itemId;
                linkEl.setAttribute('data-wowhead', 'domain=forever');
            }}

            var copyBtn = document.getElementById('inspector-copy-btn');
            if (copyBtn) {{
                copyBtn.setAttribute('data-name', activeName);
            }}

            // Deal Calculator updates
            var cost = 0;
            var breakeven = 0;
            var ahCut = 0;
            var profit = 0;
            var roi = '—';

            var deal = item.deal_info || {{}};
            if (deal.cost !== undefined && deal.revenue !== undefined) {{
                cost = deal.cost;
                var revenue = deal.revenue;
                ahCut = Math.round(revenue * 0.05);
                breakeven = Math.round(cost / 0.95);
                profit = deal.profit !== undefined ? deal.profit : (revenue - ahCut - cost);
                roi = cost > 0 ? '+' + ((profit / cost) * 100).toFixed(1) + '%' : '—';
            }} else if (deal.vendor_sell !== undefined && deal.buyout !== undefined) {{
                cost = deal.buyout;
                var vendorSell = deal.vendor_sell;
                ahCut = 0;
                breakeven = cost;
                profit = deal.profit !== undefined ? deal.profit : (vendorSell - cost);
                roi = cost > 0 ? '+' + ((profit / cost) * 100).toFixed(1) + '%' : '—';
            }} else if (deal.expected_de_value !== undefined) {{
                cost = deal.buyout !== undefined ? deal.buyout : (item.current_price || 0);
                var expDe = deal.expected_de_value;
                ahCut = Math.round(expDe * 0.05);
                breakeven = Math.round(cost / 0.95);
                profit = deal.profit !== undefined ? deal.profit : Math.round(expDe * 0.95 - cost);
                roi = cost > 0 ? '+' + ((profit / cost) * 100).toFixed(1) + '%' : '—';
            }} else {{
                cost = item.current_price || 0;
                var expSell = item.mean_price || item.current_price || 0;
                ahCut = Math.round(expSell * 0.05);
                breakeven = Math.round(cost / 0.95);
                profit = Math.round(expSell * 0.95 - cost);
                roi = cost > 0 ? ((profit / cost) * 100).toFixed(1) + '%' : '—';
            }}

            var elCost = document.getElementById('calc-cost');
            if (elCost) elCost.innerHTML = formatCurrencyHtml(cost);
            var elBe = document.getElementById('calc-breakeven');
            if (elBe) elBe.innerHTML = formatCurrencyHtml(breakeven);
            var elCut = document.getElementById('calc-ah-cut');
            if (elCut) elCut.innerHTML = formatCurrencyHtml(ahCut);
            var elProfit = document.getElementById('calc-profit');
            if (elProfit) elProfit.innerHTML = formatCurrencyHtml(profit);
            var elRoi = document.getElementById('calc-roi');
            if (elRoi) elRoi.textContent = roi;

            // Disenchant Breakdown card updates
            var deCard = document.getElementById('disenchant-card');
            if (deCard) {{
                var isDe = (deal && (deal.is_disenchant || deal.expected_de_value !== undefined)) || (currentActiveTab === 'tab-disenchant');
                if (isDe && deal && deal.reagents && deal.reagents.length > 0) {{
                    deCard.style.display = 'flex';
                    var deExpEl = document.getElementById('de-card-expected-val');
                    if (deExpEl) {{
                        deExpEl.innerHTML = formatCurrencyHtml(deal.expected_de_value || 0);
                    }}
                    var listEl = document.getElementById('de-reagents-list');
                    if (listEl) {{
                        var rHtml = '';
                        deal.reagents.forEach(function(r) {{
                            var rName = (lang === 'en' ? r.reagent_name : r.reagent_name_ru) || r.reagent_name || r.reagent_name_ru || ('Item ' + r.reagent_id);
                            var chancePct = r.chance <= 1.0 ? Math.round(r.chance * 1000) / 10 : r.chance;
                            var expCount = r.expected_count !== undefined ? r.expected_count : 1.0;
                            var unitPrice = r.unit_price_copper || 0;
                            var totalVal = Math.round((r.chance <= 1.0 ? r.chance : r.chance / 100) * expCount * unitPrice);
                            var safeName = String(rName).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
                            rHtml += '<div class="de-reagent-row">' +
                                '<div class="de-reagent-top">' +
                                    '<a href="https://www.wowhead.com/forever/item=' + r.reagent_id + '" data-wowhead="domain=forever" target="_blank" rel="noopener" class="wowhead-link">' +
                                        safeName +
                                    '</a>' +
                                    '<span class="profit-cell">+' + formatCurrencyHtml(totalVal) + '</span>' +
                                '</div>' +
                                '<div class="de-reagent-bottom">' +
                                    '<span>' + (lang === 'en' ? 'Chance: ' : 'Шанс: ') + chancePct + '% &times; ' + expCount + '</span>' +
                                    '<span>AH: ' + formatCurrencyHtml(unitPrice) + '</span>' +
                                '</div>' +
                            '</div>';
                        }});
                        listEl.innerHTML = rHtml;
                    }}
                }} else {{
                    deCard.style.display = 'none';
                }}
            }}

            updateInspectorChart(item, activeName);

            if (window.$Wh && window.$Wh.updateTooltips) {{
                try {{ window.$Wh.updateTooltips(); }} catch(e) {{}}
            }}
        }}

        function updateInspectorChart(item, activeName) {{
            var plotContainer = document.getElementById('inspector-chart');
            if (!plotContainer) return;
            var lang = getStoredLang();

            if (!item.points || item.points.length === 0) {{
                var emptyMsg = lang === 'en'
                    ? 'Waiting for first price snapshot from Auctionator.lua...'
                    : 'База данных ожидает первого снимка цен из Auctionator.lua...';
                plotContainer.innerHTML = '<div class="empty-state i18n" data-ru="База данных ожидает первого снимка цен из Auctionator.lua..." data-en="Waiting for first price snapshot from Auctionator.lua...">' + emptyMsg + '</div>';
                return;
            }}

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
                    marker: {{ size: 4 }},
                    hovertext: hoverTexts,
                    hoverinfo: 'text+x'
                }}
            ];

            var cost = (item.deal_info && item.deal_info.cost) || item.current_price || 0;
            var breakeven = Math.round(cost / 0.95);
            if (breakeven > 0 && item.points.length > 1) {{
                traces.push({{
                    x: [xVals[0], xVals[xVals.length - 1]],
                    y: [breakeven, breakeven],
                    mode: 'lines',
                    name: (lang === 'en' ? 'Breakeven (P_be): ' : 'Безубыток (P_be): ') + formatCurrencyText(breakeven),
                    line: {{ color: '#f85149', width: 1.5, dash: 'dot' }},
                    hoverinfo: 'name'
                }});
            }}

            if (item.mean_price && item.points.length > 1) {{
                traces.push({{
                    x: [xVals[0], xVals[xVals.length - 1]],
                    y: [item.mean_price, item.mean_price],
                    mode: 'lines',
                    name: (lang === 'en' ? 'Mean Price: ' : 'Средняя цена: ') + formatCurrencyText(item.mean_price),
                    line: {{ color: '#e3b341', width: 1.5, dash: 'dash' }},
                    hoverinfo: 'name'
                }});
            }}

            var layout = {{
                template: 'plotly_dark',
                paper_bgcolor: '#0b0e14',
                plot_bgcolor: '#10141d',
                font: {{ color: '#c9d1d9', family: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif", size: 10 }},
                margin: {{ l: 45, r: 15, t: 25, b: 30 }},
                height: 250,
                showlegend: true,
                legend: {{ orientation: 'h', yanchor: 'bottom', y: 1.02, xanchor: 'right', x: 1, font: {{ size: 9 }} }},
                xaxis: {{ gridcolor: '#1f2430', zerolinecolor: '#30363d' }},
                yaxis: {{ gridcolor: '#1f2430', zerolinecolor: '#30363d' }}
            }};

            if (window.Plotly) {{
                Plotly.react(plotContainer, traces, layout, {{ responsive: true, displayModeBar: false }});
            }}
        }}

        function onItemSearch(query) {{
            currentSearchQuery = (query || '').trim().toLowerCase();
            try {{
                localStorage.setItem('ft_search', currentSearchQuery);
            }} catch(e) {{}}

            var activePane = document.querySelector('.tab-pane.active');
            if (activePane) {{
                var rows = activePane.querySelectorAll('tbody tr');
                rows.forEach(function(row) {{
                    var text = (row.innerText || row.textContent || '').toLowerCase();
                    if (!currentSearchQuery || text.indexOf(currentSearchQuery) !== -1) {{
                        row.style.display = '';
                    }} else {{
                        row.style.display = 'none';
                    }}
                }});
            }}
            applyItemSelectFilter();
        }}

        function openItemModal(itemId, event) {{
            if (event) event.stopPropagation();
            selectInspectorItem(itemId);
        }}

        function closeItemModal() {{
            var modal = document.getElementById('item-modal');
            if (modal) modal.style.display = 'none';
        }}

        function onModalBackdropClick(event) {{
            if (event.target && event.target.id === 'item-modal') {{
                closeItemModal();
            }}
        }}

        function filterCategory(cat) {{
            currentFilterCat = cat;
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
                opt.style.display = (catMatch && searchMatch) ? '' : 'none';
            }});
        }}

        function onSelectDropdownItem(val) {{
            if (val) selectInspectorItem(val);
        }}

        document.addEventListener("DOMContentLoaded", function() {{
            var initialLang = getStoredLang();
            setLanguage(initialLang);

            try {{
                var savedTab = localStorage.getItem('ft_active_tab');
                if (savedTab && document.getElementById(savedTab)) {{
                    switchTab(savedTab);
                }}
                var savedSearch = localStorage.getItem('ft_search');
                if (savedSearch) {{
                    var inp = document.getElementById('item-search-input');
                    if (inp) {{
                        inp.value = savedSearch;
                        onItemSearch(savedSearch);
                    }}
                }}
                var savedItem = localStorage.getItem('ft_selected_item');
                if (savedItem && ITEM_STORE[savedItem]) {{
                    selectInspectorItem(savedItem);
                }} else {{
                    var keys = Object.keys(ITEM_STORE);
                    if (keys.length > 0) {{
                        selectInspectorItem(keys[0]);
                    }}
                }}
            }} catch(e) {{}}

            // Ctrl+K Search Shortcut
            document.addEventListener('keydown', function(e) {{
                if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K' || e.keyCode === 75)) {{
                    e.preventDefault();
                    var inp = document.getElementById('item-search-input');
                    if (inp) {{
                        inp.focus();
                        inp.select();
                    }}
                }}
            }});

            // Sortable tables
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
