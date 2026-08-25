from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from .config import AppConfig, load_config, save_config
from .fast_status import prepare_fast_status, read_fast_status
from .raw import RawArchive
from .savant import SavantClient
from .storage import StatcastStore
from .sync import SyncEngine

DEFAULT_CONFIG = Path("config.json")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="treepolo-mlb", description="Baseball Savant local pitch-data mirror")
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    b = sub.add_parser("backfill")
    b.add_argument("--start", default=None); b.add_argument("--end", default=None)
    b.add_argument("--fail-fast", action="store_true")
    b.add_argument("--resume", action="store_true", help="Skip exact chunks already completed successfully")
    u = sub.add_parser("update"); u.add_argument("--through", default=None)
    sub.add_parser("verify"); sub.add_parser("status"); sub.add_parser("retry-failed")
    a = sub.add_parser("auto-update")
    g = a.add_mutually_exclusive_group(required=True); g.add_argument("--enable", action="store_true"); g.add_argument("--disable", action="store_true")
    s = sub.add_parser("scheduler"); s.add_argument("--once", action="store_true")
    r = sub.add_parser("rebuild"); r.add_argument("--yes", action="store_true", help="Required: recreates normalized DB from raw snapshots")
    ui = sub.add_parser("ui", help="Open the local bilingual Windows XP/7-style analysis interface")
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--no-browser", action="store_true", help="Do not open the default browser automatically")
    return p


def _engine(config: AppConfig):
    store = StatcastStore(config.database_path)
    archive = RawArchive(config.root)
    client = SavantClient(config.request_timeout_seconds, config.request_retries, config.request_backoff_seconds, config.request_pause_seconds)
    return store, SyncEngine(config, store, client, archive)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    if args.command == "init":
        if not args.config.exists(): save_config(args.config, config)
        config.root.mkdir(parents=True, exist_ok=True)
        with StatcastStore(config.database_path): pass
        prepare_fast_status(config.database_path)
        print(f"Initialized {config.database_path}")
        return 0
    if args.command == "ui":
        from .webapp import serve
        serve(config, host=args.host, port=args.port, open_browser=not args.no_browser)
        return 0
    store, engine = _engine(config)
    try:
        if args.command == "backfill":
            start = date.fromisoformat(args.start or config.earliest_date)
            end = date.fromisoformat(args.end) if args.end else date.today()
            print(engine.backfill(start, end, continue_on_error=not args.fail_fast, resume=args.resume))
        elif args.command == "update":
            through = date.fromisoformat(args.through) if args.through else None
            print(engine.update(through))
        elif args.command == "retry-failed":
            print(engine.retry_failed())
        elif args.command == "verify":
            print(json.dumps(store.verify(), indent=2, ensure_ascii=False))
        elif args.command == "status":
            prepare_fast_status(config.database_path)
            print(json.dumps(read_fast_status(config.database_path), indent=2, ensure_ascii=False))
        elif args.command == "auto-update":
            value = "true" if args.enable else "false"
            store.set_setting("auto_update_enabled", value)
            print(f"auto_update_enabled={value}")
        elif args.command == "scheduler":
            engine.scheduler(stop_after_one=args.once)
        elif args.command == "rebuild":
            if not args.yes: raise SystemExit("rebuild requires --yes")
            store.close()
            db = config.database_path
            for suffix in ("", "-wal", "-shm"):
                p = Path(str(db) + suffix)
                if p.exists(): p.unlink()
            store = StatcastStore(db)
            prepare_fast_status(db)
            engine = SyncEngine(config, store, engine.fetcher, engine.archive)
            print(f"Reingested {engine.rebuild_from_raw()} raw snapshots")
        return 0
    finally:
        try: store.close()
        except Exception: pass


if __name__ == "__main__":
    raise SystemExit(main())
