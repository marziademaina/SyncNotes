#!/usr/bin/env python3
import tempfile
from pathlib import Path

from chaos_lib import compose_down, compose_up, fail, gateway_get, ok, run_cli, wait_for_cluster_quorum

FILENAME = "demo-notes.md"
BASE_CONTENT = "riga1\nriga2\nriga3\n"


def write(path: Path, content: str) -> None:
    path.write_text(content)


def read(path: Path) -> str:
    return path.read_text()


def main() -> None:
    compose_up()
    try:
        wait_for_cluster_quorum()
        ok("cluster formed with quorum on all 3 replicas")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client_a, client_b, client_c, client_d = (root / d for d in ("client-a", "client-b", "client-c", "client-d"))
            for d in (client_a, client_b, client_c, client_d):
                d.mkdir()

            seed = client_a / "seed.md"
            write(seed, BASE_CONTENT)
            run_cli("upload", FILENAME, str(seed), cwd=client_a)
            ok(f"seeded {FILENAME} with the base content")

            print("\n--- Scene 1: two clients edit different lines from the same base ---")

            note_a = client_a / FILENAME
            note_b = client_b / FILENAME
            run_cli("download", FILENAME, "--out", str(note_a), cwd=client_a)
            run_cli("download", FILENAME, "--out", str(note_b), cwd=client_b)
            ok("both clients downloaded the same version into their own directory (same filename, different dirs)")

            write(note_a, BASE_CONTENT.replace("riga2", "A-EDIT"))
            write(note_b, BASE_CONTENT.replace("riga3", "B-EDIT"))

            run_cli("upload", FILENAME, str(note_a), cwd=client_a)
            out_b = run_cli("upload", FILENAME, str(note_b), cwd=client_b)
            if "merged in changes" not in out_b:
                fail("expected client B to be told the server merged in changes since its download")
            ok("client B was told its upload landed on top of a change it hadn't seen")

            merged = gateway_get(FILENAME)
            if merged["content"] != "riga1\nA-EDIT\nB-EDIT\n":
                fail(f"expected both disjoint edits to survive, got: {merged['content']!r}")
            ok(f"both edits survived: {merged['content']!r}")

            print("\n--- Scene 2: two clients edit the SAME line from the same base ---")

            note_c = client_c / FILENAME
            note_d = client_d / FILENAME
            run_cli("download", FILENAME, "--out", str(note_c), cwd=client_c)
            run_cli("download", FILENAME, "--out", str(note_d), cwd=client_d)

            write(note_c, read(note_c).replace("A-EDIT", "C-WINS"))
            write(note_d, read(note_d).replace("A-EDIT", "D-LOSES"))

            run_cli("upload", FILENAME, str(note_c), cwd=client_c)
            out_d = run_cli("upload", FILENAME, str(note_d), cwd=client_d)
            if "were dropped" not in out_d:
                fail("expected client D to be told its conflicting edit did not survive")
            ok("client D was told its edit didn't survive the conflict")

            final = gateway_get(FILENAME)
            if "C-WINS" not in final["content"] or "D-LOSES" in final["content"]:
                fail(f"expected C's edit to win the conflict, got: {final['content']!r}")
            ok(f"server-always-wins held on the conflicting line: {final['content']!r}")

        print("\nPASS: disjoint edits both survived; a genuine conflict was resolved deterministically and the losing client was told.")
    finally:
        compose_down()


if __name__ == "__main__":
    main()
