"""AI Market Advisor for WoW: Forever.

Aggregates compact market context and interfaces with Antigravity Manager (Gemini 3.8 Flash High)
or deterministic heuristic fallback to produce session tactical checklists.
"""

import json
import logging
import os
import re
import sqlite3
import subprocess
import urllib.error
import urllib.request
from typing import Any, Optional

try:
    from src.analytics import (
        get_crafting_margins,
        get_vendor_arbitrage,
        get_zscore_anomalies,
    )
    from src.dashboard import format_currency
except ImportError:
    from analytics import (
        get_crafting_margins,
        get_vendor_arbitrage,
        get_zscore_anomalies,
    )
    from dashboard import format_currency

logger = logging.getLogger(__name__)

__all__ = [
    "build_compact_context",
    "generate_market_advice",
    "AntigravityManagerClient",
    "_safe_int",
    "_safe_float",
    "_sanitize_name",
]


def _safe_int(val: Any, default: int = 0) -> int:
    """Safely convert value to int, returning default on None, ValueError, or TypeError."""
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _safe_float(val: Any, default: float = 0.0) -> float:
    """Safely convert value to float, returning default on None, ValueError, or TypeError."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _sanitize_name(name: Any, max_len: int = 64) -> str:
    """Sanitize item names to prevent indirect prompt injection."""
    if name is None:
        return ""
    cleaned = re.sub(r"[\r\n\t]", " ", str(name)).strip()
    return cleaned[:max_len]


def _sanitize_context(context: dict) -> dict:
    """Return a sanitized copy of market context with cleaned item and crate names."""
    if not isinstance(context, dict):
        return context
    clean_ctx = dict(context)
    for section_key in ("vendor_arbitrage", "vendor", "waylaid_crafting", "crafting", "price_anomalies", "anomalies"):
        items = clean_ctx.get(section_key)
        if isinstance(items, list):
            sanitized_items = []
            for it in items:
                if isinstance(it, dict):
                    it_copy = dict(it)
                    for name_field in ("name", "crafted_item_name", "target"):
                        if name_field in it_copy and it_copy[name_field] is not None:
                            it_copy[name_field] = _sanitize_name(it_copy[name_field])
                    for crate_field in ("crate_name", "waylaid_crate_name"):
                        if crate_field in it_copy and it_copy[crate_field] is not None:
                            it_copy[crate_field] = _sanitize_name(it_copy[crate_field])
                    sanitized_items.append(it_copy)
                else:
                    sanitized_items.append(it)
            clean_ctx[section_key] = sanitized_items
    return clean_ctx


def _build_context_whitelist(context: Optional[dict]) -> set[str]:
    """Build a whitelist of known item/recipe names and IDs from market context."""
    whitelist: set[str] = set()
    if not context or not isinstance(context, dict):
        return whitelist
    for section_key in ("vendor_arbitrage", "vendor", "waylaid_crafting", "crafting", "price_anomalies", "anomalies"):
        items = context.get(section_key)
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict):
                    for field in ("name", "crafted_item_name", "target", "crate_name", "waylaid_crate_name"):
                        val = it.get(field)
                        if val:
                            whitelist.add(str(val).strip().lower())
                    for id_field in ("item_id", "recipe_id", "target_item_id"):
                        val = it.get(id_field)
                        if val is not None:
                            val_str = str(val).strip().lower()
                            whitelist.add(val_str)
                            whitelist.add(f"item #{val_str}")
                            whitelist.add(f"recipe #{val_str}")
    return whitelist


def _is_target_valid(target: str, whitelist: set[str]) -> bool:
    """Validate target name/ID against whitelist."""
    if not whitelist:
        return True
    t_clean = target.strip().lower()
    if not t_clean:
        return False
    if t_clean in whitelist:
        return True
    for w in whitelist:
        if len(w) >= 3:
            if w in t_clean or (len(t_clean) >= 3 and t_clean in w):
                return True
    return False


def build_compact_context(
    conn: sqlite3.Connection,
    realm: Optional[str] = None,
    **kwargs: Any,
) -> dict:
    """Build a compact, JSON-serializable market context.

    Extracts:
    1. Top-3 vendor arbitrage opportunities (min_profit_copper=50).
    2. Top-3 marginal recipes for Waylaid Crates (or fallback to top general crafting).
    3. Top-3 raw materials experiencing severe price drops (Z <= -1.8, STRONG_BUY).
    """
    # 1. Top-3 vendor arbitrage
    vendor_arbitrage_raw = kwargs.get("vendor_arbitrage")
    if vendor_arbitrage_raw is None:
        vendor_arbitrage_raw = get_vendor_arbitrage(
            conn, min_profit_copper=50, realm=realm, max_age_hours=kwargs.get("max_age_hours")
        )
    top_vendor = []
    for item in vendor_arbitrage_raw[:3]:
        buyout = _safe_int(item.get("buyout_price") if item.get("buyout_price") is not None else item.get("price"), 0)
        v_sell = _safe_int(item.get("vendor_sell_price"), 0)
        profit = _safe_int(item.get("profit_copper") if item.get("profit_copper") is not None else item.get("profit"), 0)
        name = _sanitize_name(item.get("name", f"Item #{item['item_id']}"))
        top_vendor.append({
            "item_id": int(item["item_id"]),
            "name": name,
            "buyout_price": buyout,
            "vendor_sell_price": v_sell,
            "profit_copper": profit,
            "profit": profit,
            "price": buyout,
        })

    # 2. Top-3 Waylaid Crates crafting margins
    crafting_raw = kwargs.get("crafting_margins")
    if crafting_raw is None:
        crafting_raw = get_crafting_margins(
            conn,
            realm=realm,
            has_bartering=kwargs.get("has_bartering", False),
            has_master_chef=kwargs.get("has_master_chef", False),
            max_age_hours=kwargs.get("max_age_hours"),
        )
    waylaid_recipes = [c for c in crafting_raw if c.get("is_waylaid_target")]
    crafting_pool = waylaid_recipes if waylaid_recipes else crafting_raw
    top_crafting = []
    for recipe in crafting_pool[:3]:
        cost = _safe_int(round(_safe_float(recipe.get("cost_copper") if recipe.get("cost_copper") is not None else recipe.get("cost"), 0.0)))
        rev = _safe_int(round(_safe_float(recipe.get("revenue_copper") if recipe.get("revenue_copper") is not None else recipe.get("revenue"), 0.0)))
        profit = _safe_int(round(_safe_float(recipe.get("profit_copper") if recipe.get("profit_copper") is not None else recipe.get("profit"), 0.0)))
        roi_val = recipe.get("roi_percent")
        if roi_val is None:
            roi_val = round(_safe_float(recipe.get("roi"), 0.0) * 100, 2)
        else:
            roi_val = round(_safe_float(roi_val, 0.0), 2)

        name = _sanitize_name(recipe.get("crafted_item_name") or recipe.get("name", f"Recipe #{recipe.get('recipe_id')}"))
        crate_name = _sanitize_name(recipe["waylaid_crate_name"]) if recipe.get("waylaid_crate_name") else None

        top_crafting.append({
            "recipe_id": _safe_int(recipe.get("recipe_id")) if recipe.get("recipe_id") is not None else None,
            "name": name,
            "profession": str(recipe.get("profession", "")),
            "crate_name": crate_name,
            "waylaid_crate_name": crate_name,
            "cost_copper": cost,
            "revenue_copper": rev,
            "profit_copper": profit,
            "profit": profit,
            "roi_percent": roi_val,
            "roi": round(roi_val / 100.0, 4),
        })

    # 3. Top-3 price drop anomalies (Z <= -1.8)
    anomalies_raw = kwargs.get("zscore_anomalies")
    if anomalies_raw is None:
        anomalies_raw = get_zscore_anomalies(conn, realm=realm, max_age_hours=kwargs.get("max_age_hours"))
    deep_drops = [
        a for a in anomalies_raw
        if a.get("signal") == "STRONG_BUY" or _safe_float(a.get("z_score") if a.get("z_score") is not None else a.get("z"), 0.0) <= -1.8
    ]
    deep_drops.sort(key=lambda a: _safe_float(a.get("z_score") if a.get("z_score") is not None else a.get("z"), 0.0))
    top_anomalies = []
    for item in deep_drops[:3]:
        curr = _safe_int(item.get("current_price") if item.get("current_price") is not None else item.get("price"), 0)
        mean = round(_safe_float(item.get("mean_price") if item.get("mean_price") is not None else item.get("mean"), 0.0), 2)
        z = round(_safe_float(item.get("z_score") if item.get("z_score") is not None else item.get("z"), 0.0), 2)
        name = _sanitize_name(item.get("name", f"Item #{item['item_id']}"))
        top_anomalies.append({
            "item_id": int(item["item_id"]),
            "name": name,
            "current_price": curr,
            "price": curr,
            "mean_price": mean,
            "mean": mean,
            "z_score": z,
            "z": z,
            "signal": str(item.get("signal", "STRONG_BUY")),
        })

    return {
        "realm": realm,
        "vendor_arbitrage": top_vendor,
        "waylaid_crafting": top_crafting,
        "price_anomalies": top_anomalies,
    }


class AntigravityManagerClient:
    """Client for Antigravity Manager / Gemini 3.8 Flash High market analysis.

    Provides robust communication with Google Generative AI REST endpoints,
    falling back to a deterministic heuristic checklist generator if no network
    or API key is available.
    """

    DEFAULT_MODEL = "gemini-3.8-flash-high"
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: int = 15,
        base_url: Optional[str] = None,
    ):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self.model = model or self.DEFAULT_MODEL
        self.timeout = timeout
        self.base_url = (base_url or self.BASE_URL).rstrip("/")

    def generate_advice(self, context: dict) -> dict:
        """Generate structured market advice and session checklist.

        If network or key is unavailable, seamlessly falls back to heuristic generation.
        """
        if not self.api_key:
            try:
                return self._call_agy_cli(context)
            except Exception as exc:
                logger.warning(
                    "AntigravityManagerClient: agy CLI call failed (%s). Falling back to heuristic.",
                    exc,
                )
            logger.info("AntigravityManagerClient: No API key set. Using deterministic heuristic generator.")
            return self.generate_heuristic_advice(context)

        try:
            return self._call_rest_api(context)
        except Exception as exc:
            logger.warning("AntigravityManagerClient: API request failed (%s). Falling back to heuristic.", exc)
            return self.generate_heuristic_advice(context)

    # Aliases for flexibility
    def generate(self, context: dict) -> dict:
        return self.generate_advice(context)

    def generate_market_advice(self, context: dict) -> dict:
        return self.generate_advice(context)

    def __call__(self, context: dict) -> dict:
        return self.generate_advice(context)

    def _call_rest_api(self, context: dict) -> dict:
        """Execute REST call to Gemini models without leaking API key in query string."""
        url = f"{self.base_url}/{self.model}:generateContent"
        system_prompt = (
            "Ты — тактический рыночный AI-ассистент World of Warcraft: Forever.\n"
            "На основе входного рыночного среза (вендорный арбитраж, маржинальный крафт под ящики Waylaid Crates, "
            "ценовые сбросы сырья) сформируй строгий оперативный чек-лист действий игрока на сессию.\n"
            "Верни ответ строго в формате JSON:\n"
            "{\n"
            '  "title": "Тактический план торговой сессии",\n'
            '  "summary": "Краткое резюме ситуации на рынке",\n'
            f'  "model": "{self.model}",\n'
            '  "actions": [\n'
            '    {\n'
            '      "step": 1,\n'
            '      "category": "vendor_arbitrage" | "waylaid_crafting" | "price_dump",\n'
            '      "action": "Конкретное действие (выкуп, крафт, закупка впрок)",\n'
            '      "target": "Название предмета",\n'
            '      "expected_profit": "Ожидаемая выгода",\n'
            '      "priority": "HIGH" | "MEDIUM" | "LOW"\n'
            '    }\n'
            '  ]\n'
            "}"
        )

        clean_context = _sanitize_context(context)
        body = {
            "system_instruction": {
                "parts": [{"text": system_prompt}]
            },
            "contents": [
                {
                    "parts": [
                        {
                            "text": (
                                "Рыночный контекст:\n"
                                "<market_data>\n"
                                f"{json.dumps(clean_context, ensure_ascii=False, indent=2)}\n"
                                "</market_data>"
                            )
                        }
                    ]
                }
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0.2,
            },
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        if self.api_key:
            req.add_header("x-goog-api-key", self.api_key)

        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            raw_body = resp.read().decode("utf-8")

        parsed = json.loads(raw_body)
        if isinstance(parsed, dict) and "candidates" in parsed:
            candidates = parsed.get("candidates", [])
            if candidates:
                text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                advice_data = self._parse_json_payload(text)
            else:
                advice_data = parsed
        else:
            advice_data = parsed

        return self._format_and_validate(advice_data, fallback_context=context)

    # Backward compatibility alias
    _call_gemini = _call_rest_api

    def _call_agy_cli(self, context: dict) -> dict:
        """Call local Antigravity CLI (agy) to generate market advice via stdin (avoids WinError 206)."""
        system_prompt = (
            "Ты — тактический рыночный AI-ассистент World of Warcraft: Forever.\n"
            "На основе входного рыночного среза (вендорный арбитраж, маржинальный крафт под ящики Waylaid Crates, "
            "ценовые сбросы сырья) сформируй строгий оперативный чек-лист действий игрока на сессию.\n"
            "Верни ответ строго в формате JSON:\n"
            "{\n"
            '  "title": "Тактический план торговой сессии",\n'
            '  "summary": "Краткое резюме ситуации на рынке",\n'
            f'  "model": "{self.model}",\n'
            '  "actions": [\n'
            '    {\n'
            '      "step": 1,\n'
            '      "category": "vendor_arbitrage" | "waylaid_crafting" | "price_dump",\n'
            '      "action": "Конкретное действие (выкуп, крафт, закупка впрок)",\n'
            '      "target": "Название предмета",\n'
            '      "expected_profit": "Ожидаемая выгода",\n'
            '      "priority": "HIGH" | "MEDIUM" | "LOW"\n'
            '    }\n'
            '  ]\n'
            "}"
        )
        clean_context = _sanitize_context(context)
        prompt = (
            f"{system_prompt}\n\n"
            f"Рыночный контекст:\n<market_data>\n{json.dumps(clean_context, ensure_ascii=False, indent=2)}\n</market_data>\n\n"
            "Верни ответ строго в формате JSON."
        )

        cmd = ["agy", "--model", self.model]
        res = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=self.timeout,
        )
        if res.returncode != 0:
            raise RuntimeError(f"agy CLI failed with exit code {res.returncode}: {res.stderr}")

        output = res.stdout.strip()
        if not output:
            raise ValueError("agy CLI returned empty output")

        advice_data = self._parse_json_payload(output)
        return self._format_and_validate(advice_data, fallback_context=context)

    @staticmethod
    def _clean_code_fence(text: str) -> str:
        text = text.strip()
        if text.startswith("[c4]"):
            text = text[4:].strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        return text

    def _parse_json_payload(self, text: str) -> dict:
        clean = self._clean_code_fence(text)
        try:
            return json.loads(clean)
        except json.JSONDecodeError:
            start_idx = clean.find("{")
            end_idx = clean.rfind("}")
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                return json.loads(clean[start_idx : end_idx + 1])
            raise

    def generate_heuristic_advice(self, context: dict) -> dict:
        """Deterministic heuristic generator producing actionable checklist from context."""
        vendor_items = context.get("vendor_arbitrage") or context.get("vendor") or []
        crafting_items = context.get("waylaid_crafting") or context.get("crafting") or []
        anomaly_items = context.get("price_anomalies") or context.get("anomalies") or []

        actions: list[dict] = []
        step = 1

        # 1. Vendor Arbitrage
        for it in vendor_items:
            name = _sanitize_name(it.get("name") or f"Item #{it.get('item_id', 'unknown')}")
            profit = _safe_int(it.get("profit_copper"), _safe_int(it.get("profit"), 0))
            buyout = _safe_int(it.get("buyout_price"), _safe_int(it.get("price"), 0))
            actions.append({
                "step": step,
                "category": "vendor_arbitrage",
                "action": f"Выкупить лот за {format_currency(buyout)} и сдать торговцу",
                "target": name,
                "expected_profit": f"+{format_currency(profit)}",
                "priority": "HIGH",
            })
            step += 1

        # 2. Waylaid Crates Crafting
        for it in crafting_items:
            name = _sanitize_name(it.get("name") or it.get("crafted_item_name") or f"Recipe #{it.get('recipe_id', 'unknown')}")
            prof = str(it.get("profession") or "Ремесло")
            crate = it.get("crate_name") or it.get("waylaid_crate_name")
            if crate:
                crate = _sanitize_name(crate)
            profit = _safe_int(it.get("profit_copper"), _safe_int(it.get("profit"), 0))
            roi_val = it.get("roi_percent")
            if roi_val is None:
                roi = round(_safe_float(it.get("roi"), 0.0) * 100, 1)
            else:
                roi = _safe_float(roi_val, 0.0)

            if crate:
                act = f"Скрафтить {name} ({prof}) под сдачу ящика {crate}"
            else:
                act = f"Скрафтить {name} ({prof}) для реализации на аукционе"

            actions.append({
                "step": step,
                "category": "waylaid_crafting",
                "action": act,
                "target": name,
                "expected_profit": f"+{format_currency(profit)} (ROI +{roi:.0f}%)",
                "priority": "HIGH" if roi >= 50.0 else "MEDIUM",
            })
            step += 1

        # 3. Price Dumps / Sniping
        for it in anomaly_items:
            name = _sanitize_name(it.get("name") or f"Item #{it.get('item_id', 'unknown')}")
            z = _safe_float(it.get("z_score"), _safe_float(it.get("z"), 0.0))
            curr = _safe_int(it.get("current_price"), _safe_int(it.get("price"), 0))
            mean = _safe_float(it.get("mean_price"), _safe_float(it.get("mean"), 0.0))
            diff = max(0, int(round(mean - curr)))

            profit_desc = f"Экономия {format_currency(diff)}" if diff > 0 else f"Скидка при Z={z:.1f}"

            actions.append({
                "step": step,
                "category": "price_dump",
                "action": f"Скупить сырье по аномально низкой цене (Z={z:+.1f}) для резерва или реселла",
                "target": name,
                "expected_profit": profit_desc,
                "priority": "HIGH" if z <= -2.5 else "MEDIUM",
            })
            step += 1

        if actions:
            title = "Тактический чек-лист торговой сессии"
            summary = (
                f"Сформирован оперативный план из {len(actions)} действий: "
                f"{len(vendor_items)} вендорных арбитражей, "
                f"{len(crafting_items)} крафтов под Waylaid Crates, "
                f"{len(anomaly_items)} снайпинг-закупок сырья."
            )
        else:
            title = "Тактический чек-лист торговой сессии"
            summary = "Актуальных рыночных сигналов в текущем снимке не обнаружено. Рекомендуется продолжать мониторинг."

        checklist = [
            f"[{a['category'].replace('_', ' ').capitalize()}] {a['action']} (Цель: {a['target']}, Выгода: {a['expected_profit']})"
            for a in actions
        ]

        return {
            "title": title,
            "summary": summary,
            "model": self.model,
            "actions": actions,
            "checklist": checklist,
        }

    # Alias for consistency
    _generate_heuristic_advice = generate_heuristic_advice

    def _format_and_validate(self, data: Any, fallback_context: Optional[dict] = None) -> dict:
        """Validate and enforce structured return contract."""
        if not isinstance(data, dict):
            if fallback_context is not None:
                return self.generate_heuristic_advice(fallback_context)
            data = {}

        title = str(data.get("title") or "Тактический план сессии")
        summary = str(data.get("summary") or "")
        model = str(data.get("model") or self.model)

        raw_actions = data.get("actions")
        if not isinstance(raw_actions, list):
            raw_actions = (
                data.get("checklist")
                or data.get("recommendations")
                or data.get("action_plan")
                or []
            )

        whitelist = _build_context_whitelist(fallback_context)

        actions: list[dict] = []
        step_idx = 1
        for i, item in enumerate(raw_actions, start=1):
            if isinstance(item, dict):
                target_str = _sanitize_name(item.get("target") or item.get("name") or "Рынок")
                if whitelist and not _is_target_valid(target_str, whitelist):
                    logger.warning("Target '%s' not in context whitelist. Discarding action.", target_str)
                    continue
                actions.append({
                    "step": _safe_int(item.get("step"), step_idx),
                    "category": str(item.get("category") or "market_action"),
                    "action": str(item.get("action") or item.get("text") or ""),
                    "target": target_str,
                    "expected_profit": str(item.get("expected_profit") or item.get("profit") or "—"),
                    "priority": str(item.get("priority") or "MEDIUM").upper(),
                })
                step_idx += 1
            elif isinstance(item, str):
                if whitelist and not _is_target_valid(item, whitelist):
                    continue
                actions.append({
                    "step": step_idx,
                    "category": "market_action",
                    "action": item,
                    "target": "Рынок",
                    "expected_profit": "—",
                    "priority": "MEDIUM",
                })
                step_idx += 1

        if not actions and raw_actions and fallback_context:
            logger.warning("All LLM actions rejected by target whitelist. Falling back to heuristic.")
            return self.generate_heuristic_advice(fallback_context)

        checklist = data.get("checklist")
        if not checklist or not isinstance(checklist, list) or len(actions) != len(raw_actions):
            checklist = [
                f"[{a['category'].replace('_', ' ').capitalize()}] {a['action']} (Цель: {a['target']}, Выгода: {a['expected_profit']})"
                for a in actions
            ]
        else:
            checklist = [str(x) for x in checklist]

        return {
            "title": title,
            "summary": summary,
            "model": model,
            "actions": actions,
            "checklist": checklist,
        }


def generate_market_advice(
    context: dict,
    client: Optional[Any] = None
) -> dict:
    """Generate market advice from compact context using Antigravity Manager client.

    Validates and guarantees standard output schema.
    """
    if client is None:
        client = AntigravityManagerClient()

    if hasattr(client, "generate_advice"):
        raw_advice = client.generate_advice(context)
    elif hasattr(client, "generate_market_advice"):
        raw_advice = client.generate_market_advice(context)
    elif hasattr(client, "generate"):
        raw_advice = client.generate(context)
    elif callable(client):
        raw_advice = client(context)
    else:
        raise TypeError(f"Invalid client object: {type(client)}")

    if isinstance(client, AntigravityManagerClient):
        return client._format_and_validate(raw_advice, fallback_context=context)

    helper = AntigravityManagerClient()
    return helper._format_and_validate(raw_advice, fallback_context=context)
