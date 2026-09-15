#!/usr/bin/env python3

import argparse
import sys
import time
from datetime import datetime

from chaos_lib import SERVERS, db_files

CONTENT_PREVIEW_LIMIT = 60
CLEAR_SCREEN = "\033[H\033[J"


def _preview(content: str) -> str:
    if len(content) <= CONTENT_PREVIEW_LIMIT:
        return repr(content)
    return repr(content[:CONTENT_PREVIEW_LIMIT] + "...")


def _dump() -> None:
    for replica in SERVERS:
        print(f"\n=== {replica}  (/data/server.db) ===")
        rows = db_files(replica)
        if rows is None:
            print("  unreachable - is the cluster up? (`docker compose up -d`)")
            continue
        if not rows:
            print("  (no files yet)")
            continue
        for row in rows:
            flag = "  [deleted]" if row.get("deleted") else ""
            print(
                f"  {row['name']:<20} v{row['version']:<3} {row['content_hash'][:12]}  "
                f"{_preview(row['content'])}{flag}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Dump the three replicas' databases side by side.")
    parser.add_argument(
        "--watch",
        nargs="?",
        type=float,
        const=1.5,
        default=None,
        metavar="SECONDS",
        help="keep re-dumping every SECONDS (default 1.5) until Ctrl+C - for showing the db live during a demo",
    )
    args = parser.parse_args()

    if args.watch is None:
        _dump()
        return

    try:
        while True:
            print(CLEAR_SCREEN, end="")
            print(f"inspect_db.py --watch {args.watch}s  (Ctrl+C to stop)  {datetime.now():%H:%M:%S}")
            _dump()
            time.sleep(args.watch)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
