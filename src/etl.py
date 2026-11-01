"""File system watcher and ETL pipeline for Auctionator SavedVariables."""

import hashlib
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import luadata
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from src.database import init_db

# Configure structured logging to watcher.log
LOG_FILE = "watcher.log"
logger = logging.getLogger("watcher")
logger.setLevel(logging.INFO)

if not any(
    isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", "").endswith(LOG_FILE)
    for h in logger.handlers
):
    try:
        file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s")
        )
        logger.addHandler(file_handler)
    except Exception:
        # Fallback to standard handler if file cannot be opened
        pass


def normalize_item_id(raw_id: Any) -> Optional[int]:
    """Extract integer item ID from string or numeric signature (e.g., '2589:0:0:0:0' -> 2589, 'g:1234:567' -> 1234, 'p:3858' -> 3858)."""
    if raw_id is None or isinstance(raw_id, bool):
        return None
    if isinstance(raw_id, int):
        return raw_id if raw_id > 0 else None

    try:
        s = str(raw_id).strip()
    except Exception:
        return None

    if not s:
        return None

    skip_prefixes = ("item", "gr", "g", "p", "i")
    parts = s.split(":")
    for part in parts:
        token = part.strip()
        if not token:
            continue
        low = token.lower()
        if low in skip_prefixes:
            continue
        for pfx in skip_prefixes:
            if low.startswith(pfx) and low[len(pfx) :].isdigit():
                token = low[len(pfx) :]
                break
        try:
            val = int(token)
            if val > 0:
                return val
        except (ValueError, TypeError):
            continue
    return None


def _extract_lua_table(content: str, var_name: str = "AUCTIONATOR_PRICE_DATABASE") -> Optional[str]:
    """Extract table literal assigned to var_name using brace balance matching."""
    idx = content.find(var_name)
    if idx == -1:
        return None
    eq_idx = content.find("=", idx + len(var_name))
    if eq_idx == -1:
        return None
    start = content.find("{", eq_idx)
    if start == -1:
        return None

    depth = 0
    in_str = False
    escape = False
    quote_char = None
    i = start
    n = len(content)

    while i < n:
        c = content[i]
        if escape:
            escape = False
            i += 1
            continue
        if c == "\\":
            escape = True
            i += 1
            continue
        if in_str:
            if c == quote_char:
                in_str = False
            i += 1
            continue
        if c in ('"', "'"):
            in_str = True
            quote_char = c
            i += 1
            continue
        # Check Lua comments: -- or --[[ ... ]]
        if c == "-" and i + 1 < n and content[i + 1] == "-":
            if i + 3 < n and content[i + 2 : i + 4] == "[[":
                comment_end = content.find("]]", i + 4)
                if comment_end != -1:
                    i = comment_end + 2
                    continue
                break
            else:
                line_end = content.find("\n", i + 2)
                if line_end != -1:
                    i = line_end + 1
                    continue
                break
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return content[start : i + 1]
        i += 1
    return None


def _parse_single_lua_file(path: str) -> dict:
    """Read and safely unserialize Lua table without eval()."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {path}")

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    if not content.strip():
        raise ValueError(f"Lua data file '{path}' is empty")

    # 1. Try luadata.read directly
    try:
        data = luadata.read(path)
        if isinstance(data, dict):
            if "AUCTIONATOR_PRICE_DATABASE" in data and isinstance(
                data["AUCTIONATOR_PRICE_DATABASE"], dict
            ):
                return data["AUCTIONATOR_PRICE_DATABASE"]
            return data
    except Exception:
        pass

    # 2. Extract AUCTIONATOR_PRICE_DATABASE table if embedded
    extracted = _extract_lua_table(content, "AUCTIONATOR_PRICE_DATABASE")
    if extracted:
        data = luadata.unserialize(extracted)
        if isinstance(data, dict):
            return data

    # 3. Fallback to luadata.unserialize on full content
    data = luadata.unserialize(content)
    if isinstance(data, dict):
        if "AUCTIONATOR_PRICE_DATABASE" in data and isinstance(
            data["AUCTIONATOR_PRICE_DATABASE"], dict
        ):
            return data["AUCTIONATOR_PRICE_DATABASE"]
        return data

    raise ValueError(f"Unserialized data from '{path}' is not a valid dictionary")


def _compute_file_meta(path: str) -> Tuple[int, str]:
    """Compute file modification time and sha256 content hash."""
    mtime = int(os.path.getmtime(path))
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return mtime, hasher.hexdigest()


_IN_MEMORY_ETL_META: Dict[Tuple[int, str], Dict[str, Any]] = {}


def parse_auctionator_data(file_path: str) -> dict:
    """Safely parse AUCTIONATOR_PRICE_DATABASE via luadata with automatic fallback to .bak."""
    if file_path.endswith(".bak") and os.path.isfile(file_path):
        bak_mtime = int(os.path.getmtime(file_path))
        if time.time() - bak_mtime > 7200:
            logger.warning(
                "Backup file '%s' is older than 2 hours (age: %d seconds). Discarding to prevent time-series poisoning.",
                file_path,
                int(time.time() - bak_mtime),
            )
            raise ValueError(f"Backup file '{file_path}' is older than 2 hours")
        data = _parse_single_lua_file(file_path)
        if isinstance(data, dict):
            mtime, content_hash = _compute_file_meta(file_path)
            data["_source_mtime"] = mtime
            data["_content_hash"] = content_hash
        return data

    bak_candidates = [f"{file_path}.bak"]
    if file_path.endswith(".lua"):
        bak_candidates.append(f"{file_path[:-4]}.bak")

    primary_error: Optional[Exception] = None
    try:
        data = _parse_single_lua_file(file_path)
        if isinstance(data, dict):
            mtime, content_hash = _compute_file_meta(file_path)
            data["_source_mtime"] = mtime
            data["_content_hash"] = content_hash
        return data
    except Exception as e:
        primary_error = e
        logger.warning(
            "Primary file '%s' failed to parse (%s: %s). Attempting fallback to backup...",
            file_path,
            type(e).__name__,
            e,
        )

    for bak_path in bak_candidates:
        if os.path.isfile(bak_path):
            bak_mtime = int(os.path.getmtime(bak_path))
            if time.time() - bak_mtime > 7200:
                logger.warning(
                    "Backup file '%s' is older than 2 hours (age: %d seconds). Discarding to prevent time-series poisoning.",
                    bak_path,
                    int(time.time() - bak_mtime),
                )
                continue
            try:
                data = _parse_single_lua_file(bak_path)
                if isinstance(data, dict):
                    mtime, content_hash = _compute_file_meta(bak_path)
                    data["_source_mtime"] = mtime
                    data["_content_hash"] = content_hash
                logger.info(
                    "Successfully loaded fallback backup file '%s' (mtime=%d)",
                    bak_path,
                    bak_mtime,
                )
                return data
            except Exception as bak_err:
                logger.warning(
                    "Fallback backup '%s' failed to parse: %s", bak_path, bak_err
                )

    logger.error(
        "Failed to load '%s' and all backup candidates: %s", file_path, primary_error
    )
    if primary_error is not None:
        raise primary_error
    raise FileNotFoundError(f"Neither '{file_path}' nor any backup file exists.")


def ingest_price_snapshot(
    conn: sqlite3.Connection,
    raw_data: dict,
    realm: str,
    dedupe_window_minutes: int = 10,
    source_mtime: Optional[int] = None,
    current_time: Optional[int] = None,
    content_hash: Optional[str] = None,
    db_path: Optional[str] = None,
) -> int:
    """Ingest price snapshot for the given realm with 10-minute deduplication and batch insertion."""
    resolved_db_path = db_path
    if not resolved_db_path:
        try:
            cur_meta = conn.cursor()
            cur_meta.execute("PRAGMA database_list")
            for r in cur_meta.fetchall():
                if r[1] == "main" and r[2]:
                    resolved_db_path = r[2]
                    break
        except Exception:
            pass
    if not resolved_db_path:
        resolved_db_path = getattr(conn, "_db_path", None)

    if source_mtime is None and isinstance(raw_data, dict):
        source_mtime = raw_data.get("_source_mtime")

    if content_hash is None and isinstance(raw_data, dict):
        content_hash = raw_data.get("_content_hash")

    effective_time = source_mtime if source_mtime is not None else current_time
    now = int(time.time()) if effective_time is None else int(effective_time)
    window_seconds = int(dedupe_window_minutes * 60)

    # Resolve realm dictionary (support space and underscore variants)
    realm_candidates = [realm]
    alt_space = realm.replace("_", " ")
    if alt_space not in realm_candidates:
        realm_candidates.append(alt_space)
    alt_underscore = realm.replace(" ", "_")
    if alt_underscore not in realm_candidates:
        realm_candidates.append(alt_underscore)

    target_realm = realm
    realm_data: Optional[dict] = None
    db_dict = raw_data
    if isinstance(raw_data, dict):
        if (
            "AUCTIONATOR_PRICE_DATABASE" in raw_data
            and isinstance(raw_data["AUCTIONATOR_PRICE_DATABASE"], dict)
        ):
            db_dict = raw_data["AUCTIONATOR_PRICE_DATABASE"]

        for cand in realm_candidates:
            if cand in db_dict and isinstance(db_dict[cand], dict):
                realm_data = db_dict[cand]
                target_realm = cand
                break
            elif cand in raw_data and isinstance(raw_data[cand], dict):
                realm_data = raw_data[cand]
                target_realm = cand
                break

        # Realm auto-discovery: if realm not found among candidates and realm dictionaries exist
        if realm_data is None and isinstance(db_dict, dict):
            potential_realms = {}
            for k, v in db_dict.items():
                k_str = str(k)
                if k_str.startswith("_") or k_str.lower() == "__dbversion":
                    continue
                if normalize_item_id(k) is not None:
                    continue
                if isinstance(v, dict):
                    potential_realms[k_str] = v

            if potential_realms:
                discovered_realm = max(
                    potential_realms.keys(),
                    key=lambda r: len(potential_realms[r]),
                )
                realm_data = potential_realms[discovered_realm]
                target_realm = discovered_realm
                logger.warning(
                    "Target realm '%s' not found in Auctionator data. Auto-discovered and selected realm '%s' with %d items.",
                    realm,
                    discovered_realm,
                    len(realm_data),
                )
                if isinstance(raw_data, dict):
                    raw_data["_discovered_realm"] = discovered_realm

        if realm_data is None and any(normalize_item_id(k) is not None for k in raw_data.keys()):
            realm_data = raw_data

    if source_mtime is None and isinstance(realm_data, dict):
        source_mtime = realm_data.get("_source_mtime")
    if content_hash is None and isinstance(realm_data, dict):
        content_hash = realm_data.get("_content_hash")

    if not realm_data or not isinstance(realm_data, dict):
        logger.info("Ingested 0 price snapshots for realm '%s' (no realm data found)", realm)
        return 0

    cur = conn.cursor()
    last_hash: Optional[str] = None
    last_mtime: Optional[int] = None
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS _etl_metadata (
                realm TEXT PRIMARY KEY,
                content_hash TEXT,
                source_mtime INTEGER,
                last_imported_time INTEGER
            )
            """
        )
        cur.execute(
            "SELECT content_hash, source_mtime FROM _etl_metadata WHERE realm = ?",
            (target_realm,),
        )
        row = cur.fetchone()
        if row:
            last_hash, last_mtime = row[0], row[1]
    except sqlite3.Error:
        pass

    mem_key = (id(conn), target_realm)
    if last_hash is None and last_mtime is None and mem_key in _IN_MEMORY_ETL_META:
        last_hash = _IN_MEMORY_ETL_META[mem_key].get("content_hash")
        last_mtime = _IN_MEMORY_ETL_META[mem_key].get("source_mtime")

    # Query latest snapshot for each item in the realm for deduplication
    existing_latest: Dict[int, Tuple[int, int]] = {}
    try:
        cur.execute(
            "SELECT item_id, price, MAX(timestamp) FROM price_snapshots WHERE realm = ? GROUP BY item_id",
            (target_realm,),
        )
        for row in cur.fetchall():
            existing_latest[int(row[0])] = (int(row[1]), int(row[2]))
    except sqlite3.Error as e:
        logger.warning("Could not query existing snapshots for deduplication: %s", e)

    batch_to_insert: List[Tuple[int, str, int, int]] = []

    for raw_key, val in realm_data.items():
        if raw_key in ("_source_mtime", "_content_hash"):
            continue
        item_id = normalize_item_id(raw_key)
        if item_id is None and isinstance(val, dict):
            item_id = normalize_item_id(val.get("id") or val.get("item_id"))
        if item_id is None:
            continue

        price: Optional[int] = None
        if isinstance(val, dict):
            # 1. Auctionator v341: buyout price in val["m"]
            if "m" in val and val["m"] is not None:
                m_val = val["m"]
                if not isinstance(m_val, bool):
                    try:
                        price = int(m_val)
                    except (ValueError, TypeError):
                        pass

            # 2. Fallback to legacy fields mr / price
            if price is None and "mr" in val and val["mr"] is not None:
                mr_val = val["mr"]
                if not isinstance(mr_val, bool):
                    try:
                        price = int(mr_val)
                    except (ValueError, TypeError):
                        pass
            if price is None and "price" in val and val["price"] is not None:
                p_val = val["price"]
                if not isinstance(p_val, bool):
                    try:
                        price = int(p_val)
                    except (ValueError, TypeError):
                        pass

            # 3. Extremums and historical tables (nested h/l or flat H/L)
            if price is None:
                for k, v in val.items():
                    k_str = str(k)
                    k_lower = k_str.lower()
                    if k_lower in ("l", "h") and isinstance(v, dict):
                        for sub_k, sub_v in v.items():
                            if not isinstance(sub_v, bool):
                                try:
                                    price = int(sub_v)
                                    break
                                except (ValueError, TypeError):
                                    pass
                        if price is not None:
                            break
                    elif k_lower in ("l", "h") and isinstance(v, (list, tuple)) and v:
                        for sub_v in v:
                            if not isinstance(sub_v, bool):
                                try:
                                    price = int(sub_v)
                                    break
                                except (ValueError, TypeError):
                                    pass
                        if price is not None:
                            break
                    elif (k_str.startswith("L") or k_str.startswith("H")) and isinstance(
                        v, (int, float, str)
                    ):
                        if not isinstance(v, bool):
                            try:
                                price = int(v)
                                break
                            except (ValueError, TypeError):
                                pass
        elif isinstance(val, (int, float)) and not isinstance(val, bool):
            price = int(val)
        elif isinstance(val, str) and val.isdigit():
            price = int(val)

        if isinstance(price, bool):
            price = None

        if price is None or price <= 0:
            continue

        # Deduplication: identical price within dedupe window or unchanged file cache is skipped
        if item_id in existing_latest:
            prev_price, prev_time = existing_latest[item_id]
            if prev_price == price:
                if (
                    (source_mtime is not None and prev_time == source_mtime)
                    or (content_hash is not None and last_hash is not None and content_hash == last_hash)
                    or (source_mtime is not None and last_mtime is not None and source_mtime == last_mtime)
                    or abs(now - prev_time) < window_seconds
                ):
                    continue

        existing_latest[item_id] = (price, now)
        batch_to_insert.append((item_id, target_realm, price, now))

    if not batch_to_insert:
        if content_hash is not None or source_mtime is not None:
            _IN_MEMORY_ETL_META[mem_key] = {
                "content_hash": content_hash,
                "source_mtime": source_mtime,
                "last_imported_time": now,
            }
            try:
                with conn:
                    cur.execute(
                        "INSERT OR REPLACE INTO _etl_metadata (realm, content_hash, source_mtime, last_imported_time) VALUES (?, ?, ?, ?)",
                        (target_realm, content_hash, source_mtime, now),
                    )
            except sqlite3.Error:
                pass
        logger.info(
            "Ingested 0 price snapshots for realm '%s' (all lots deduplicated or empty)",
            target_realm,
        )
        return 0

    try:
        # Ensure foreign key constraints succeed if items table exists
        items_to_resolve: List[int] = []
        try:
            cur.execute("SELECT 1 FROM items LIMIT 1")
            unique_item_ids = list({item[0] for item in batch_to_insert})
            for i in range(0, len(unique_item_ids), 400):
                chunk = unique_item_ids[i : i + 400]
                placeholders = ",".join("?" for _ in chunk)
                cur.execute(
                    f"SELECT item_id, name FROM items WHERE item_id IN ({placeholders})",
                    chunk,
                )
                existing = dict(cur.fetchall())
                for i_id in chunk:
                    nm = existing.get(i_id)
                    if nm is None or not nm or nm.startswith("Item ") or nm.startswith("Item #"):
                        items_to_resolve.append(i_id)

            cur.executemany(
                "INSERT OR IGNORE INTO items (item_id, name) VALUES (?, ?)",
                [(item[0], f"Item {item[0]}") for item in batch_to_insert],
            )
        except sqlite3.Error:
            pass

        # Background Wowhead resolution for unresolved items if db_path is available
        if items_to_resolve and resolved_db_path:
            try:
                from src.wowhead import WowheadResolver

                resolver = WowheadResolver.get_instance()
                for i_id in items_to_resolve:
                    resolver.enqueue(i_id, resolved_db_path)
            except Exception:
                pass

        with conn:
            cur.executemany(
                "INSERT INTO price_snapshots (item_id, realm, price, timestamp) VALUES (?, ?, ?, ?)",
                batch_to_insert,
            )
            if content_hash is not None or source_mtime is not None:
                _IN_MEMORY_ETL_META[mem_key] = {
                    "content_hash": content_hash,
                    "source_mtime": source_mtime,
                    "last_imported_time": now,
                }
                try:
                    cur.execute(
                        "INSERT OR REPLACE INTO _etl_metadata (realm, content_hash, source_mtime, last_imported_time) VALUES (?, ?, ?, ?)",
                        (target_realm, content_hash, source_mtime, now),
                    )
                except sqlite3.Error:
                    pass

        inserted_count = len(batch_to_insert)
        logger.info("Ingested %d price snapshots for realm '%s'", inserted_count, target_realm)
        return inserted_count
    except sqlite3.Error as e:
        logger.error(
            "Failed to insert price snapshots for realm '%s': %s", target_realm, e, exc_info=True
        )
        return 0


class AuctionatorEventHandler(FileSystemEventHandler):
    """File system event handler monitoring modifications to Auctionator.lua."""

    def __init__(
        self,
        target_filename: str,
        full_target_path: str,
        db_path: str,
        realm: str,
        debounce_seconds: float = 0.5,
        callback: Optional[Callable[[str, int], None]] = None,
    ):
        super().__init__()
        self.target_filename = target_filename.lower()
        self.full_target_path = full_target_path
        self.db_path = db_path
        self.realm = realm
        self.debounce_seconds = debounce_seconds
        self.callback = callback
        self._last_processed = 0.0
        self._lock = threading.Lock()

    def _should_handle(self, path: Optional[str]) -> bool:
        if not path:
            return False
        return os.path.basename(path).lower() == self.target_filename

    def on_modified(self, event):
        if not event.is_directory and self._should_handle(event.src_path):
            self._trigger_etl(event.src_path)

    def on_created(self, event):
        if not event.is_directory and self._should_handle(event.src_path):
            self._trigger_etl(event.src_path)

    def on_moved(self, event):
        dest = getattr(event, "dest_path", "")
        if not event.is_directory and self._should_handle(dest):
            self._trigger_etl(dest)

    def _wait_for_file_ready(self, file_path: str) -> bool:
        """Wait for trailing-edge debounce, size stabilization, and file completeness ending with '}'."""
        if self.debounce_seconds > 0:
            time.sleep(self.debounce_seconds)

        if not os.path.isfile(file_path):
            return False

        check_interval = min(0.2, self.debounce_seconds) if self.debounce_seconds > 0 else 0.05
        try:
            size1 = os.path.getsize(file_path)
            time.sleep(check_interval)
            size2 = os.path.getsize(file_path)
            retries = 0
            while size1 != size2 and retries < 5:
                size1 = size2
                time.sleep(check_interval)
                size2 = os.path.getsize(file_path)
                retries += 1
        except OSError:
            time.sleep(check_interval)

        def _ends_with_brace(path: str) -> bool:
            try:
                with open(path, "rb") as f:
                    f.seek(0, os.SEEK_END)
                    size = f.tell()
                    if size == 0:
                        return False
                    f.seek(max(0, size - 1024))
                    tail = f.read().decode("utf-8", errors="replace").rstrip()
                    return tail.endswith("}")
            except OSError:
                return False

        if not _ends_with_brace(file_path):
            retry_delay = 0.2 if self.debounce_seconds >= 0.2 else self.debounce_seconds
            for _ in range(5):
                time.sleep(retry_delay)
                if _ends_with_brace(file_path):
                    break

        return True

    def _trigger_etl(self, file_path: str):
        with self._lock:
            target_file = file_path if os.path.isfile(file_path) else self.full_target_path

            self._wait_for_file_ready(target_file)

            now = time.time()
            self._last_processed = now

            logger.info(
                "Watcher detected update in '%s', starting ETL ingestion...", target_file
            )
            try:
                raw_data = parse_auctionator_data(target_file)
                src_mtime = raw_data.get("_source_mtime") if isinstance(raw_data, dict) else None
                conn = init_db(self.db_path)
                try:
                    count = ingest_price_snapshot(
                        conn,
                        raw_data,
                        self.realm,
                        source_mtime=src_mtime,
                        db_path=self.db_path,
                    )
                    if isinstance(raw_data, dict) and "_discovered_realm" in raw_data:
                        self.realm = raw_data["_discovered_realm"]
                    logger.info(
                        "Watcher ETL finished: %d snapshots ingested from '%s'",
                        count,
                        target_file,
                    )
                    if count > 0 and self.callback is not None:
                        try:
                            self.callback(target_file, count)
                        except Exception as cb_err:
                            logger.error("Watcher callback failed: %s", cb_err, exc_info=True)
                finally:
                    conn.close()
            except Exception as e:
                logger.error(
                    "Watcher ETL failed for '%s': %s", target_file, e, exc_info=True
                )


def start_watcher(
    watch_path: str,
    db_path: str,
    realm: str,
    stop_event: Optional[threading.Event] = None,
    callback: Optional[Callable[[str, int], None]] = None,
    on_snapshot_callback: Optional[Callable[[str, int], None]] = None,
) -> Observer:
    """Start background file watcher monitoring Auctionator.lua modifications."""
    cb = callback or on_snapshot_callback
    clean_path = os.path.abspath(watch_path)

    if watch_path.lower().endswith(".lua"):
        full_target_path = clean_path
        watch_dir = os.path.dirname(full_target_path) or "."
        target_filename = os.path.basename(full_target_path)
        os.makedirs(watch_dir, exist_ok=True)
    elif os.path.isdir(watch_path):
        watch_dir = clean_path
        target_filename = "Auctionator.lua"
        full_target_path = os.path.join(watch_dir, target_filename)
        os.makedirs(watch_dir, exist_ok=True)
    else:
        # Directory does not exist yet: create it and force target_filename = "Auctionator.lua"
        os.makedirs(clean_path, exist_ok=True)
        watch_dir = clean_path
        target_filename = "Auctionator.lua"
        full_target_path = os.path.join(watch_dir, target_filename)

    event_handler = AuctionatorEventHandler(
        target_filename=target_filename,
        full_target_path=full_target_path,
        db_path=db_path,
        realm=realm,
        callback=cb,
    )

    observer = Observer()
    observer.schedule(event_handler, path=watch_dir, recursive=True)
    observer.start()
    logger.info(
        "Watcher started monitoring '%s' for '%s' (realm: '%s', recursive=True)",
        watch_dir,
        target_filename,
        realm,
    )

    if stop_event is not None:
        try:
            while not stop_event.is_set():
                stop_event.wait(timeout=0.2)
        finally:
            observer.stop()
            observer.join()

    return observer
