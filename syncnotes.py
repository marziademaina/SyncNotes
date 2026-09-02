#!/usr/bin/env python3

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "client" / "src"))

from client.tui import DEFAULT_GATEWAY, DEFAULT_SYNC_DIR, main  


def _cli() -> None:
    parser = argparse.ArgumentParser(description="Open the SyncNotes note browser")
    parser.add_argument("--gateway", default=DEFAULT_GATEWAY, help=f"Gateway address (default: {DEFAULT_GATEWAY})")
    parser.add_argument(
        "--dir", default=str(DEFAULT_SYNC_DIR), help=f"Folder to keep note copies in (default: {DEFAULT_SYNC_DIR})"
    )
    parser.add_argument(
        "--name", help="Name shown in the top corner of the screen (default: your OS username)"
    )
    args = parser.parse_args()
    main(args.gateway, args.dir, args.name)


if __name__ == "__main__":
    _cli()
