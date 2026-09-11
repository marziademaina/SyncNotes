import argparse
import os
import sys

from client.api import delete_file, download_file, list_files, upload_file
from client.notes import describe_upload_outcome
from client.sync_state import clear_state, read_base_version, write_state

DEFAULT_GATEWAY = "http://localhost:8080"


def main() -> None:
    parser = argparse.ArgumentParser(prog="syncnotes-client")
    parser.add_argument("--gateway", default=DEFAULT_GATEWAY, help="Gateway base URL")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List the notes available on the cluster")

    download_parser = subparsers.add_parser("download", help="Download a file from the cluster")
    download_parser.add_argument("name")
    download_parser.add_argument("--out", help="Local path to save the file (defaults to stdout)")

    upload_parser = subparsers.add_parser("upload", help="Upload a local file to the cluster")
    upload_parser.add_argument("name")
    upload_parser.add_argument("path", help="Local file path to upload")

    delete_parser = subparsers.add_parser("delete", help="Delete a note on the cluster")
    delete_parser.add_argument("name")
    delete_parser.add_argument("--local", help="Also remove this local copy and its sidecar")

    tui_parser = subparsers.add_parser(
        "tui", help="Open the interactive note browser (no commands or paths to remember)"
    )
    tui_parser.add_argument("--dir", help="Local folder to keep note copies in (default: ~/SyncNotes)")
    tui_parser.add_argument("--name", help="Name shown in the top corner of the screen (default: your OS username)")

    args = parser.parse_args()

    if args.command == "list":
        notes = list_files(args.gateway)
        if not notes:
            print("no notes on the server yet")
        for note in notes:
            print(f"{note['name']}\tv{note['version']}\t{note['updated_at']} UTC")
    elif args.command == "download":
        result = download_file(args.gateway, args.name)
        if args.out:
            with open(args.out, "w") as f:
                f.write(result["content"])
            write_state(args.out, args.name, result["version"])
            print(f"saved {args.name} (version {result['version']}) to {args.out}")
        else:
            sys.stdout.write(result["content"])
    elif args.command == "upload":
        with open(args.path) as f:
            content = f.read()

        base_version = read_base_version(args.path, args.name)
        result = upload_file(args.gateway, args.name, content, base_version)

        note = describe_upload_outcome(base_version, content, result)
        if result["content"] != content:
            with open(args.path, "w") as f:
                f.write(result["content"])
        write_state(args.path, args.name, result["version"])
        print(f"uploaded {args.name}: now version {result['version']} ({result['content_hash'][:12]}){note}")
    elif args.command == "delete":
        version = read_base_version(args.local, args.name) if args.local else None
        result = delete_file(args.gateway, args.name, version)
        if args.local:
            try:
                os.unlink(args.local)
            except FileNotFoundError:
                pass
            clear_state(args.local)
        print(f"deleted {args.name} (tombstoned at version {result['version']})")
    elif args.command == "tui":
        from client.tui import DEFAULT_SYNC_DIR, main as tui_main

        tui_main(args.gateway, args.dir or DEFAULT_SYNC_DIR, args.name)


if __name__ == "__main__":
    main()
