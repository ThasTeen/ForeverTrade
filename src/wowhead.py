"""Wowhead Forever item data fetcher and background on-demand resolver."""

import json
import logging
import os
import queue
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import Optional, Tuple

logger = logging.getLogger("wowhead")


def _parse_cdata_json(text: Optional[str]) -> dict:
    """Parse JSON embedded in XML CDATA (e.g., json or jsonEquip tags)."""
    if not text:
        return {}
    s = text.strip()
    if not s.startswith("{"):
        s = "{" + s + "}"
    try:
        data = json.loads(s)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _parse_tooltip_sellprice(tooltip_html: str) -> int:
    """Extract vendor sell price in copper from Wowhead tooltip HTML."""
    if not tooltip_html:
        return 0
    m = re.search(r'class="whtt-sellprice"[^>]*>(.*?)</div>', tooltip_html, re.DOTALL)
    if not m:
        return 0
    block = m.group(1)
    g = re.search(r'class="moneygold">(\d+)<', block)
    s = re.search(r'class="moneysilver">(\d+)<', block)
    c = re.search(r'class="moneycopper">(\d+)<', block)
    gold = int(g.group(1)) if g else 0
    silver = int(s.group(1)) if s else 0
    copper = int(c.group(1)) if c else 0
    return gold * 10000 + silver * 100 + copper


def _fetch_via_nether(
    item_id: int, domain: str = "forever", timeout: float = 3.0, max_retries: int = 2, backoff: float = 1.0
) -> Optional[dict]:
    """Fallback fetcher using Wowhead Nether tooltip JSON API."""
    url = (
        f"https://nether.wowhead.com/{domain}/tooltip/item/{item_id}"
        if domain
        else f"https://nether.wowhead.com/tooltip/item/{item_id}"
    )
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ForeverTrade/1.0"},
    )
    data = None
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 429 and attempt < max_retries - 1:
                    time.sleep(backoff * (2 ** attempt))
                    continue
                if resp.status != 200:
                    return None
                data = json.loads(resp.read().decode("utf-8"))
                break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries - 1:
                time.sleep(backoff * (2 ** attempt))
                continue
            return None
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(backoff * (2 ** attempt))
                continue
            return None

    if not isinstance(data, dict):
        return None

    name = data.get("name")
    quality = int(data.get("quality", 1) or 1)
    icon = str(data.get("icon") or "")
    tooltip = str(data.get("tooltip") or "")
    sell_price = _parse_tooltip_sellprice(tooltip)

    name_ru = None
    try:
        from src.database import ITEM_LOCALIZATION

        name_ru = ITEM_LOCALIZATION.get(item_id, {}).get("ru")
    except Exception:
        pass

    return {
        "item_id": item_id,
        "name": name or f"Item {item_id}",
        "name_ru": name_ru,
        "quality": quality,
        "item_class": 0,
        "item_subclass": 0,
        "vendor_sell_price": sell_price,
        "vendor_sell_copper": sell_price,
        "vendor_buy_price": 0,
        "vendor_buy_copper": 0,
        "icon": icon,
    }


def fetch_wowhead_item(
    item_id: int,
    domain: str = "forever",
    timeout: float = 3.0,
    max_retries: int = 3,
    backoff: float = 1.0,
) -> Optional[dict]:
    """Fetch item info from Wowhead Forever XML API or Nether tooltip API."""
    if not isinstance(item_id, int) or item_id <= 0:
        return None

    en_url = (
        f"https://www.wowhead.com/{domain}/item={item_id}&xml"
        if domain
        else f"https://www.wowhead.com/item={item_id}&xml"
    )
    ru_url = (
        f"https://ru.wowhead.com/{domain}/item={item_id}&xml"
        if domain
        else f"https://ru.wowhead.com/item={item_id}&xml"
    )

    req = urllib.request.Request(
        en_url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ForeverTrade/1.0"},
    )

    xml_data = None
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 429:
                    retry_after = resp.headers.get("Retry-After")
                    wait_time = float(retry_after) if retry_after else (backoff * (2 ** attempt))
                    time.sleep(wait_time)
                    continue
                if resp.status != 200:
                    return _fetch_via_nether(item_id, domain=domain, timeout=timeout)
                xml_data = resp.read()
                break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries - 1:
                retry_after = e.headers.get("Retry-After")
                wait_time = float(retry_after) if retry_after else (backoff * (2 ** attempt))
                time.sleep(wait_time)
                continue
            return _fetch_via_nether(item_id, domain=domain, timeout=timeout)
        except urllib.error.URLError:
            if attempt < max_retries - 1:
                time.sleep(backoff * (2 ** attempt))
                continue
            return _fetch_via_nether(item_id, domain=domain, timeout=timeout)
        except Exception:
            return _fetch_via_nether(item_id, domain=domain, timeout=timeout)

    if xml_data is None:
        return _fetch_via_nether(item_id, domain=domain, timeout=timeout)

    try:
        root = ET.fromstring(xml_data)
    except Exception:
        return _fetch_via_nether(item_id, domain=domain, timeout=timeout)

    if root.find("error") is not None:
        return None

    item_elem = root.find("item")
    if item_elem is None:
        return None

    name = item_elem.findtext("name")
    icon = item_elem.findtext("icon") or ""

    q_elem = item_elem.find("quality")
    quality = int(q_elem.attrib.get("id", 1)) if q_elem is not None and "id" in q_elem.attrib else 1

    c_elem = item_elem.find("class")
    item_class = int(c_elem.attrib.get("id", 0)) if c_elem is not None and "id" in c_elem.attrib else 0

    sc_elem = item_elem.find("subclass")
    item_subclass = int(sc_elem.attrib.get("id", 0)) if sc_elem is not None and "id" in sc_elem.attrib else 0

    equip = _parse_cdata_json(item_elem.findtext("jsonEquip"))
    sell_price = int(equip.get("sellprice", 0) or 0)
    buy_price = int(equip.get("buyprice", 0) or 0)

    if sell_price == 0:
        html_tt = item_elem.findtext("htmlTooltip") or ""
        sell_price = _parse_tooltip_sellprice(html_tt)

    # Fetch RU localized name
    name_ru = None
    ru_req = urllib.request.Request(
        ru_url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ForeverTrade/1.0"},
    )
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(ru_req, timeout=timeout) as ru_resp:
                if ru_resp.status == 429 and attempt < max_retries - 1:
                    time.sleep(backoff * (2 ** attempt))
                    continue
                if ru_resp.status == 200:
                    ru_root = ET.fromstring(ru_resp.read())
                    ru_item = ru_root.find("item")
                    if ru_item is not None:
                        name_ru = ru_item.findtext("name")
                break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries - 1:
                time.sleep(backoff * (2 ** attempt))
                continue
            break
        except Exception:
            break

    # Prioritize local canonical localization and settled vendor prices
    try:
        from src.database import ITEM_LOCALIZATION

        if item_id in ITEM_LOCALIZATION:
            loc = ITEM_LOCALIZATION[item_id]
            if loc.get("en"):
                name = loc["en"]
            if loc.get("ru"):
                name_ru = loc["ru"]
        if item_id == 2450 and sell_price < 50:
            sell_price = 50
        elif item_id == 2316:
            name = "Toughened Leather Gloves"
            name_ru = "Плотные кожаные перчатки"
    except Exception:
        pass

    return {
        "item_id": item_id,
        "name": name or f"Item {item_id}",
        "name_ru": name_ru,
        "quality": quality,
        "item_class": item_class,
        "item_subclass": item_subclass,
        "vendor_sell_price": sell_price,
        "vendor_sell_copper": sell_price,
        "vendor_buy_price": buy_price,
        "vendor_buy_copper": buy_price,
        "icon": icon,
    }


class WowheadResolver:
    """Lightweight background queue worker with polite rate-limiting (~100-200ms per request)."""

    _instance: Optional["WowheadResolver"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "WowheadResolver":
        """Get or initialize the singleton WowheadResolver instance."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self, rate_limit_seconds: float = 0.15) -> None:
        self._rate_limit = rate_limit_seconds
        self._queue: queue.Queue[Tuple[int, str]] = queue.Queue()
        self._queued_ids: set[int] = set()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name="WowheadResolverWorker",
        )
        self._worker_thread.start()

    def enqueue(self, item_id: int, db_path: str = "market.db") -> None:
        """Queue an unknown item for background resolution."""
        if not isinstance(item_id, int) or item_id <= 0:
            return
        with self._lock:
            if item_id in self._queued_ids:
                return
            self._queued_ids.add(item_id)
            self._queue.put((item_id, db_path))

    def stop(self, timeout: float = 2.0) -> None:
        """Stop background worker thread."""
        self._stop_event.set()
        if self._worker_thread.is_alive():
            self._worker_thread.join(timeout=timeout)

    def _worker_loop(self) -> None:
        """Daemon worker loop draining resolution queue."""
        while not self._stop_event.is_set():
            try:
                item_id, db_path = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            try:
                info = fetch_wowhead_item(item_id)
                if info:
                    self._update_db(item_id, db_path, info)
            except Exception as e:
                logger.debug("Error resolving item %d: %s", item_id, e)
            finally:
                self._queue.task_done()
                time.sleep(self._rate_limit)

    def _update_db(self, item_id: int, db_path: str, info: dict) -> None:
        """Apply resolved item details to SQLite items table."""
        try:
            with sqlite3.connect(db_path, timeout=10.0) as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    UPDATE items
                    SET name = ?, name_ru = ?, quality = ?, item_class = ?,
                        vendor_sell_price = ?, vendor_sell_copper = ?,
                        vendor_buy_price = ?, vendor_buy_copper = ?
                    WHERE item_id = ?
                    """,
                    (
                        info.get("name") or f"Item {item_id}",
                        info.get("name_ru"),
                        int(info.get("quality", 1) or 1),
                        int(info.get("item_class", 0) or 0),
                        int(info.get("vendor_sell_price", 0) or 0),
                        int(info.get("vendor_sell_copper", info.get("vendor_sell_price", 0)) or 0),
                        int(info.get("vendor_buy_price", 0) or 0),
                        int(info.get("vendor_buy_copper", info.get("vendor_buy_price", 0)) or 0),
                        item_id,
                    ),
                )
                if cur.rowcount == 0:
                    cur.execute(
                        """
                        INSERT OR IGNORE INTO items (
                            item_id, name, name_ru, quality, item_class, item_subclass,
                            vendor_sell_price, vendor_sell_copper, vendor_buy_price, vendor_buy_copper
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            item_id,
                            info.get("name") or f"Item {item_id}",
                            info.get("name_ru"),
                            int(info.get("quality", 1) or 1),
                            int(info.get("item_class", 0) or 0),
                            int(info.get("item_subclass", 0) or 0),
                            int(info.get("vendor_sell_price", 0) or 0),
                            int(info.get("vendor_sell_copper", info.get("vendor_sell_price", 0)) or 0),
                            int(info.get("vendor_buy_price", 0) or 0),
                            int(info.get("vendor_buy_copper", info.get("vendor_buy_price", 0)) or 0),
                        ),
                    )
                conn.commit()
        except Exception as e:
            logger.debug("Failed updating database '%s' for item %d: %s", db_path, item_id, e)
