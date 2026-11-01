#!/usr/bin/env python3
"""Run file watcher, ETL pipeline and dashboard generator for World of Warcraft: Forever."""

import argparse
import logging
import os
import queue
import signal
import sqlite3
import threading
import time
from typing import List, Optional

from src.database import init_db, load_catalog, load_recipes, prune_snapshots
from src.etl import (
    ingest_price_snapshot,
    normalize_item_id,
    parse_auctionator_data,
    start_watcher,
)
from src.dashboard import build_standalone_dashboard, notify_high_value_arbitrage
from src.analytics import get_crafting_margins, get_vendor_arbitrage, get_zscore_anomalies
from src.ai_advisor import build_compact_context, generate_market_advice, AntigravityManagerClient

logger = logging.getLogger("watcher")


def _load_dotenv(filepath: str = ".env"):
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        if k and k not in os.environ:
                            os.environ[k] = v
        except Exception:
            pass


_load_dotenv()


def _build_dashboard(
    conn: sqlite3.Connection,
    output_path: str = "portal.html",
    ai_advice: Optional[dict] = None,
    realm: Optional[str] = None,
    has_bartering: bool = False,
    has_master_chef: bool = False,
    max_age_hours: Optional[float] = None,
) -> str:
    """Safely build standalone dashboard with perk and TTL parameters if supported."""
    try:
        return build_standalone_dashboard(
            conn,
            output_path=output_path,
            ai_advice=ai_advice,
            realm=realm,
            has_bartering=has_bartering,
            has_master_chef=has_master_chef,
            max_age_hours=max_age_hours,
        )
    except TypeError:
        try:
            return build_standalone_dashboard(
                conn,
                output_path=output_path,
                ai_advice=ai_advice,
                realm=realm,
                has_bartering=has_bartering,
                has_master_chef=has_master_chef,
            )
        except TypeError:
            return build_standalone_dashboard(
                conn,
                output_path=output_path,
                ai_advice=ai_advice,
                realm=realm,
            )


def initial_ingest(conn: sqlite3.Connection, watch_dir: str, realm: str) -> int:
    """Perform initial import from existing Auctionator.lua if present on disk."""
    if watch_dir.lower().endswith(".lua"):
        lua_path = watch_dir
    else:
        lua_path = os.path.join(watch_dir, "Auctionator.lua")

    if not os.path.exists(lua_path):
        return 0

    try:
        raw_data = parse_auctionator_data(lua_path)
    except Exception as e:
        print(f"[!] Initial ingest failed from '{lua_path}': {e}")
        logger.error("Initial ingest failed from '%s': %s", lua_path, e, exc_info=True)
        return 0

    # Validate realm existence in parsed Lua database
    db_table = raw_data.get("AUCTIONATOR_PRICE_DATABASE", raw_data) if isinstance(raw_data, dict) else {}
    available_realms: List[str] = [
        str(k) for k, v in db_table.items()
        if not str(k).startswith("_") and isinstance(v, dict) and normalize_item_id(k) is None
    ] if isinstance(db_table, dict) else []

    realm_candidates = [realm]
    alt_space = realm.replace("_", " ")
    if alt_space not in realm_candidates:
        realm_candidates.append(alt_space)
    alt_underscore = realm.replace(" ", "_")
    if alt_underscore not in realm_candidates:
        realm_candidates.append(alt_underscore)

    if available_realms and not any(cand in available_realms for cand in realm_candidates):
        warning_msg = (
            f"Realm '{realm}' not found in '{lua_path}'. "
            f"Available realms: {', '.join(sorted(available_realms))}"
        )
        print(f"[!] Warning: {warning_msg}")
        logger.warning(warning_msg)

    source_mtime = raw_data.get("_source_mtime") if isinstance(raw_data, dict) else None
    count = ingest_price_snapshot(conn, raw_data, realm, source_mtime=source_mtime)
    print(f"[*] Initial ingest from existing '{lua_path}': {count} snapshots for realm '{realm}'.")
    logger.info("Initial ingest from existing '%s': %d snapshots for realm '%s'.", lua_path, count, realm)
    return count


def main(argv=None):
    parser = argparse.ArgumentParser(description="Forever Trade Watcher & Analytical Agent")
    parser.add_argument("--watch-dir", default=os.getenv("WOW_WTF_PATH", "data"),
                        help="Directory containing Auctionator.lua or WoW WTF SavedVariables path")
    parser.add_argument("--db", default="market.db", help="Path to SQLite database")
    parser.add_argument("--realm", default="ForeverRealm_Alliance", help="Realm name in Auctionator database")
    parser.add_argument("--dashboard-out", default="portal.html", help="Path for generated standalone dashboard")
    parser.add_argument("--model", default="gemini-3.8-flash-high", help="Antigravity Manager model name")
    parser.add_argument("--bartering", action="store_true", default=False,
                        help="Enable Bartering perk (5%% discount on vendor/NPC purchases)")
    parser.add_argument("--master-chef", action="store_true", default=False,
                        help="Enable Master Chef perk (1.1x yield on cooking recipes)")
    parser.add_argument("--max-age-hours", type=float, default=48.0,
                        help="Filter market prices older than max age in hours")
    args = parser.parse_args(argv)

    print(f"[*] Initializing Forever Trade database: {args.db}")
    conn = init_db(args.db)
    try:
        load_catalog(conn)
        load_recipes(conn)
        prune_snapshots(conn, max_days=30)

        initial_ingest(conn, args.watch_dir, args.realm)

        print("[*] Generating baseline AI market advice...")
        arb = get_vendor_arbitrage(conn, realm=args.realm, max_age_hours=args.max_age_hours)
        craft = get_crafting_margins(
            conn,
            has_bartering=args.bartering,
            has_master_chef=args.master_chef,
            realm=args.realm,
            max_age_hours=args.max_age_hours,
        )
        anom = get_zscore_anomalies(conn, realm=args.realm, max_age_hours=args.max_age_hours)

        context = build_compact_context(
            conn,
            realm=args.realm,
            vendor_arbitrage=arb,
            crafting_margins=craft,
            zscore_anomalies=anom,
        )
        client = AntigravityManagerClient(model=args.model)
        advice = generate_market_advice(context, client=client)

        print(f"[*] Building initial standalone portal: {args.dashboard_out}")
        _build_dashboard(
            conn,
            output_path=args.dashboard_out,
            ai_advice=advice,
            realm=args.realm,
            has_bartering=args.bartering,
            has_master_chef=args.master_chef,
            max_age_hours=args.max_age_hours,
        )

        notify_high_value_arbitrage(arb)
    finally:
        conn.close()

    task_queue: queue.Queue = queue.Queue()
    stop_event = threading.Event()

    def _worker():
        while not stop_event.is_set():
            try:
                task = task_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                file_path, count = task
                print(f"[*] New market data ingested ({count} snapshots from '{file_path}'). Auto-rebuilding portal...")
                cb_conn = init_db(args.db)
                try:
                    # Single calculation of analytics per snapshot
                    arb_items = get_vendor_arbitrage(cb_conn, realm=args.realm, max_age_hours=args.max_age_hours)
                    craft_items = get_crafting_margins(
                        cb_conn,
                        has_bartering=args.bartering,
                        has_master_chef=args.master_chef,
                        realm=args.realm,
                        max_age_hours=args.max_age_hours,
                    )
                    anom_items = get_zscore_anomalies(cb_conn, realm=args.realm, max_age_hours=args.max_age_hours)

                    cb_context = build_compact_context(
                        cb_conn,
                        realm=args.realm,
                        vendor_arbitrage=arb_items,
                        crafting_margins=craft_items,
                        zscore_anomalies=anom_items,
                    )
                    cb_client = AntigravityManagerClient(model=args.model)
                    cb_advice = generate_market_advice(cb_context, client=cb_client)

                    _build_dashboard(
                        cb_conn,
                        output_path=args.dashboard_out,
                        ai_advice=cb_advice,
                        realm=args.realm,
                        has_bartering=args.bartering,
                        has_master_chef=args.master_chef,
                        max_age_hours=args.max_age_hours,
                    )
                    notify_high_value_arbitrage(arb_items)
                    print(f"[*] Standalone portal updated: {args.dashboard_out}")
                finally:
                    cb_conn.close()
            except Exception as e:
                print(f"[!] Auto-rebuild portal failed: {e}")
            finally:
                task_queue.task_done()

    worker_thread = threading.Thread(target=_worker, daemon=True)
    worker_thread.start()

    def handle_signal(sig, frame):
        print("\n[*] Stopping watcher...")
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    def on_snapshot_ingested(file_path: str, count: int):
        task_queue.put((file_path, count))

    print(f"[*] Starting Watcher Engine on: {args.watch_dir}")
    print("[*] Press Ctrl+C to terminate.")
    observer = start_watcher(args.watch_dir, args.db, args.realm, callback=on_snapshot_ingested)

    try:
        while not stop_event.is_set():
            time.sleep(1)
    finally:
        observer.stop()
        observer.join()
        worker_thread.join(timeout=2.0)


if __name__ == "__main__":
    main()
