#!/usr/bin/env python3

from chaos_lib import SERVERS, db_files

CONTENT_PREVIEW_LIMIT = 60


def _preview(content: str) -> str:
    if len(content) <= CONTENT_PREVIEW_LIMIT:
        return repr(content)
    return repr(content[:CONTENT_PREVIEW_LIMIT] + "...")


def main() -> None:
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


if __name__ == "__main__":
    main()
