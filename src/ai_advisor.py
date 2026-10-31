"""AI Market Advisor for WoW: Forever.

Aggregates compact market context and interfaces with Antigravity Manager (Gemini 3.8 Flash High)
or deterministic heuristic fallback to produce session tactical checklists.
"""

import json
import logging
import os
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
]


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
    vendor_arbitrage_raw = get_vendor_arbitrage(conn, min_profit_copper=50, realm=realm)
    top_vendor = []
    for item in vendor_arbitrage_raw[:3]:
        buyout = int(item.get("buyout_price", item.get("price", 0)))
        v_sell = int(item.get("vendor_sell_price", 0))
        profit = int(item.get("profit_copper", item.get("profit", 0)))
        top_vendor.append({
            "item_id": int(item["item_id"]),
            "name": str(item.get("name", f"Item #{item['item_id']}")),
            "buyout_price": buyout,
            "vendor_sell_price": v_sell,
            "profit_copper": profit,
            "profit": profit,
            "price": buyout,
        })

    # 2. Top-3 Waylaid Crates crafting margins
    crafting_raw = get_crafting_margins(conn, realm=realm)
    waylaid_recipes = [c for c in crafting_raw if c.get("is_waylaid_target")]
    crafting_pool = waylaid_recipes if waylaid_recipes else crafting_raw
    top_crafting = []
    for recipe in crafting_pool[:3]:
        cost = int(round(recipe.get("cost_copper", recipe.get("cost", 0))))
        rev = int(round(recipe.get("revenue_copper", recipe.get("revenue", 0))))
        profit = int(round(recipe.get("profit_copper", recipe.get("profit", 0))))
        roi_val = recipe.get("roi_percent")
        if roi_val is None:
            roi_val = round(float(recipe.get("roi", 0.0)) * 100, 2)
        else:
            roi_val = round(float(roi_val), 2)

        top_crafting.append({
            "recipe_id": int(recipe["recipe_id"]),
            "name": str(recipe.get("crafted_item_name") or recipe.get("name", f"Recipe #{recipe['recipe_id']}")),
            "profession": str(recipe.get("profession", "")),
            "crate_name": str(recipe["waylaid_crate_name"]) if recipe.get("waylaid_crate_name") else None,
            "waylaid_crate_name": str(recipe["waylaid_crate_name"]) if recipe.get("waylaid_crate_name") else None,
            "cost_copper": cost,
            "revenue_copper": rev,
            "profit_copper": profit,
            "profit": profit,
            "roi_percent": roi_val,
            "roi": round(roi_val / 100.0, 4),
        })

    # 3. Top-3 price drop anomalies (Z <= -1.8)
    anomalies_raw = get_zscore_anomalies(conn, z_buy_threshold=-1.8, realm=realm)
    deep_drops = [
        a for a in anomalies_raw
        if a.get("signal") == "STRONG_BUY" or float(a.get("z_score", a.get("z", 0.0))) <= -1.8
    ]
    deep_drops.sort(key=lambda a: float(a.get("z_score", a.get("z", 0.0))))
    top_anomalies = []
    for item in deep_drops[:3]:
        curr = int(item.get("current_price", item.get("price", 0)))
        mean = round(float(item.get("mean_price", item.get("mean", 0.0))), 2)
        z = round(float(item.get("z_score", item.get("z", 0.0))), 2)
        top_anomalies.append({
            "item_id": int(item["item_id"]),
            "name": str(item.get("name", f"Item #{item['item_id']}")),
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
            return self._call_gemini(context)
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

    def _call_gemini(self, context: dict) -> dict:
        url = f"{self.base_url}/{self.model}:generateContent?key={self.api_key}"
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

        body = {
            "system_instruction": {
                "parts": [{"text": system_prompt}]
            },
            "contents": [
                {
                    "parts": [
                        {"text": f"Рыночный контекст:\n{json.dumps(context, ensure_ascii=False, indent=2)}"}
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

    def _call_agy_cli(self, context: dict) -> dict:
        """Call local Antigravity CLI (agy) to generate market advice."""
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
        prompt = (
            f"{system_prompt}\n\n"
            f"Рыночный контекст:\n{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
            "Верни ответ строго в формате JSON."
        )

        cmd = ["agy", "-p", prompt, "--model", self.model]
        res = subprocess.run(
            cmd,
            input="",
            capture_output=True,
            text=True,
            timeout=self.timeout,
            encoding="utf-8",
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
            name = str(it.get("name") or f"Item #{it.get('item_id', 'unknown')}")
            profit = int(it.get("profit_copper", it.get("profit", 0)))
            buyout = int(it.get("buyout_price", it.get("price", 0)))
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
            name = str(it.get("name") or it.get("crafted_item_name") or f"Recipe #{it.get('recipe_id', 'unknown')}")
            prof = str(it.get("profession") or "Ремесло")
            crate = it.get("crate_name") or it.get("waylaid_crate_name")
            profit = int(it.get("profit_copper", it.get("profit", 0)))
            roi = it.get("roi_percent")
            if roi is None:
                roi = round(float(it.get("roi", 0.0)) * 100, 1)

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
            name = str(it.get("name") or f"Item #{it.get('item_id', 'unknown')}")
            z = float(it.get("z_score", it.get("z", 0.0)))
            curr = int(it.get("current_price", it.get("price", 0)))
            mean = float(it.get("mean_price", it.get("mean", 0.0)))
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

        actions: list[dict] = []
        for i, item in enumerate(raw_actions, start=1):
            if isinstance(item, dict):
                actions.append({
                    "step": int(item.get("step") or i),
                    "category": str(item.get("category") or "market_action"),
                    "action": str(item.get("action") or item.get("text") or ""),
                    "target": str(item.get("target") or item.get("name") or "Рынок"),
                    "expected_profit": str(item.get("expected_profit") or item.get("profit") or "—"),
                    "priority": str(item.get("priority") or "MEDIUM").upper(),
                })
            elif isinstance(item, str):
                actions.append({
                    "step": i,
                    "category": "market_action",
                    "action": item,
                    "target": "Рынок",
                    "expected_profit": "—",
                    "priority": "MEDIUM",
                })

        checklist = data.get("checklist")
        if not checklist or not isinstance(checklist, list):
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
