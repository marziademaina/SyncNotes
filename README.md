# SyncNotes

A collaborative note-taking system, built as a project for the Distributed
Software Systems course (A.A. 2025/2026).

Shared plain-text notes live on a cluster of three Raft-replicated server
nodes (the sole source of truth); clients never talk to a replica directly,
only to a single stateless gateway that hides which replica is leader and
how many replicas exist. The system is deliberately **CP** in CAP terms: a
minority partition refuses writes rather than diverge. The only user-facing
interface is a full-screen terminal UI (curses) that needs no commands, note
names or file paths to operate.

## Prerequisites

| Software | Version | Purpose |
|---|---|---|
| Docker Engine | 20.10+ | Runs the 3-replica cluster and the gateway |
| Docker Compose | v2 | Brings the whole system up from one file |
| Python | 3.12+ | Runs the client and the chaos/demo scripts on the host |

Docker Desktop (or an equivalent running daemon) must already be started
before any `docker compose` command below — otherwise the first one fails
immediately with `Cannot connect to the Docker daemon`.

## Quick start

```bash
git clone https://github.com/marziademaina/SyncNotes.git
cd SyncNotes
```

**Terminal 1** — repository root, brings up the cluster + gateway (no venv
needed here, everything runs inside the containers):

```bash
docker compose up --build -d           # build images, start detached
docker compose logs -f                 # follow logs (Ctrl+C to stop following)
curl -s http://localhost:8080/health   # {"status":"ok", "leader": "..."}
```

Expect three replicas to elect a leader within a few seconds (`raft state:`
and `cluster: ... leader=... quorum=True peers=2/2` log lines) and the
gateway to answer on `:8080`.

**Terminal 2** — also from the repository root, once the cluster above is
up (separate venv from the containers, since the client has its own,
smaller set of dependencies):

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r client/requirements.txt
python3 syncnotes.py                   # launches the full-screen note browser (F7)
```

On later runs, just `source venv/bin/activate && python3 syncnotes.py` — no
need to reinstall unless dependencies changed.

## Using the app

| Key | Action |
|---|---|
| `↑` / `↓` (or `k` / `j`) | Move the selection |
| `Enter` | Open the selected note for editing |
| `n` | Create a new note (you are asked for a name) |
| `d` | Delete the selected note (`y`/`n` confirmation) |
| `r` | Refresh the list now |
| `q` / `Esc` | Quit |

Inside the editor: type normally, `Ctrl+G` saves and exits, `Esc` discards.
After a save, the status line reports the outcome (saved as-is, server
merged in changes since your last download, or your edit to a
concurrently-edited line was dropped).

## Running the tests

167 automated tests across server, gateway and client (own venv, separate
from the one above):

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r server/requirements-dev.txt -r client/requirements-dev.txt
cd server  && python -m pytest
cd ../gateway && python -m pytest
cd ../client  && python -m pytest
```

## Chaos scenarios and demo

With the client venv active, from the repository root — each script brings
up its own clean cluster with `docker compose up --build`, tears it down
with `docker compose down -v`, and exits non-zero on the first failure:

```bash
python3 scripts/demo_collaborative_edit.py   # ordinary use case, no faults injected
python3 scripts/chaos_kill_leader.py
python3 scripts/chaos_network_partition.py
python3 scripts/chaos_restart_node.py
python3 scripts/chaos_corruption.py
python3 scripts/chaos_delete_converges.py
python3 scripts/inspect_db.py                # dump the three replicas' databases side by side
```

## Project layout

```
client/src/client/   tui.py, cli.py, api.py, notes.py, sync_state.py
client/tests/
gateway/src/gateway/  main.py (leader discovery, forwarding, SSE)
gateway/tests/
server/src/server/    main.py, db.py, merge.py, cluster.py, reconciliation.py, models.py
server/tests/
scripts/              chaos_*.py, demo_collaborative_edit.py, inspect_db.py
docker-compose.yml     3 replicas + gateway, one bridge network
syncnotes.py           repo-root launcher for the TUI (F7)
```
