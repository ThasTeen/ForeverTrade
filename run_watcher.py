#!/usr/bin/env python3
"""Run file watcher, ETL pipeline and dashboard generator for World of Warcraft: Forever."""

import argparse
import os
import queue
import signal
import threading
import time

from src.database import init_db, load_catalog, load_recipes, prune_snapshots
from src.etl import start_watcher
from src.dashboard import build_standalone_dashboard, notify_high_value_arbitrage
from src.analytics import get_crafting_margins, get_vendor_arbitrage, get_zscore_anomalies
from src.ai_advisor import build_compact_context, generate_market_advice, AntigravityManagerClient


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


def main():
    parser = argparse.ArgumentParser(description="Forever Trade Watcher & Analytical Agent")
    parser.add_argument("--watch-dir", default=os.getenv("WOW_WTF_PATH", "data"),
                        help="Directory containing Auctionator.lua or WoW WTF SavedVariables path")
    parser.add_argument("--db", default="market.db", help="Path to SQLite database")
    parser.add_argument("--realm", default="ForeverRealm_Alliance", help="Realm name in Auctionator database")
    parser.add_argument("--dashboard-out", default="portal.html", help="Path for generated standalone dashboard")
    parser.add_argument("--model", default="gemini-3.8-flash-high", help="Antigravity Manager model name")
    args = parser.parse_args()

    print(f"[*] Initializing Forever Trade database: {args.db}")
    conn = init_db(args.db)
    try:
        load_catalog(conn)
        load_recipes(conn)
        prune_snapshots(conn, max_days=30)

        print("[*] Generating baseline AI market advice...")
        arb = get_vendor_arbitrage(conn, realm=args.realm)
        craft = get_crafting_margins(conn, realm=args.realm)
        anom = get_zscore_anomalies(conn, realm=args.realm)

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
        build_standalone_dashboard(conn, output_path=args.dashboard_out, ai_advice=advice, realm=args.realm)

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
                    arb_items = get_vendor_arbitrage(cb_conn, realm=args.realm)
                    craft_items = get_crafting_margins(cb_conn, realm=args.realm)
                    anom_items = get_zscore_anomalies(cb_conn, realm=args.realm)

                    cb_context = build_compact_context(
                        cb_conn,
                        realm=args.realm,
                        vendor_arbitrage=arb_items,
                        crafting_margins=craft_items,
                        zscore_anomalies=anom_items,
                    )
                    cb_client = AntigravityManagerClient(model=args.model)
                    cb_advice = generate_market_advice(cb_context, client=cb_client)

                    build_standalone_dashboard(
                        cb_conn,
                        output_path=args.dashboard_out,
                        ai_advice=cb_advice,
                        realm=args.realm,
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
