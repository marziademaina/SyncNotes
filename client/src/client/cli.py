import argparse
import sys

from client.api import download_file, upload_file
from client.sync_state import read_base_version, write_state

DEFAULT_GATEWAY = "http://localhost:8080"


def main() -> None:
    parser = argparse.ArgumentParser(prog="syncnotes-client")
    parser.add_argument("--gateway", default=DEFAULT_GATEWAY, help="Gateway base URL")
    subparsers = parser.add_subparsers(dest="command", required=True)

    download_parser = subparsers.add_parser("download", help="Download a file from the cluster")
    download_parser.add_argument("name")
    download_parser.add_argument("--out", help="Local path to save the file (defaults to stdout)")

    upload_parser = subparsers.add_parser("upload", help="Upload a local file to the cluster")
    upload_parser.add_argument("name")
    upload_parser.add_argument("path", help="Local file path to upload")

    args = parser.parse_args()

    if args.command == "download":
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
        write_state(args.path, args.name, result["version"])

        note = " (server merged in changes since your last download)" if result["content"] != content else ""
        print(f"uploaded {args.name}: now version {result['version']} ({result['content_hash'][:12]}){note}")


if __name__ == "__main__":
    main()
