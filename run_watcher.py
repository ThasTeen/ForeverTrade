#!/usr/bin/env python3
"""Run file watcher, ETL pipeline and dashboard generator for World of Warcraft: Forever."""

import argparse
import os
import signal
import sys
import threading
import time

from src.database import init_db, load_catalog, load_recipes
from src.etl import start_watcher
from src.dashboard import build_standalone_dashboard, notify_high_value_arbitrage
from src.analytics import get_vendor_arbitrage
from src.ai_advisor import build_compact_context, generate_market_advice, AntigravityManagerClient


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
    load_catalog(conn)
    load_recipes(conn)

    print("[*] Generating baseline AI market advice...")
    context = build_compact_context(conn, realm=args.realm)
    client = AntigravityManagerClient(model=args.model)
    advice = generate_market_advice(context, client=client)

    print(f"[*] Building initial standalone portal: {args.dashboard_out}")
    build_standalone_dashboard(conn, output_path=args.dashboard_out, ai_advice=advice, realm=args.realm)

    arb = get_vendor_arbitrage(conn, realm=args.realm)
    notify_high_value_arbitrage(arb)

    stop_event = threading.Event()

    def handle_signal(sig, frame):
        print("\n[*] Stopping watcher...")
        stop_event.set()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    print(f"[*] Starting Watcher Engine on: {args.watch_dir}")
    print("[*] Press Ctrl+C to terminate.")
    observer = start_watcher(args.watch_dir, args.db, args.realm, stop_event=stop_event)

    try:
        while not stop_event.is_set():
            time.sleep(1)
    finally:
        stop_event.set()
        observer.stop()
        observer.join()


if __name__ == "__main__":
    main()
