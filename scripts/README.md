# Chaos scripts

Reproducible fault-injection scripts for the deliverable in `docs/propose.txt`
("Scripts to inject node crashes and network partitions reproducibly"). Each
script is self-contained: it brings the full `docker-compose.yml` cluster up
from a clean slate, injects one specific fault, asserts the system recovers
the way the proposal promises, prints `[OK]`/`[FAIL]` for every check, and
tears the cluster back down (`docker compose down -v`) whether it passed or
failed. Exit code is `0` on pass, `1` on the first failed assertion.

Run from the repository root, with the venv active:

```bash
source venv/bin/activate
python scripts/chaos_kill_leader.py        # kills the Raft leader; gateway must reroute writes
python scripts/chaos_network_partition.py  # isolates a follower; no split brain, self-heals on rejoin
python scripts/chaos_corruption.py         # bit-rots a follower's SQLite row; reconciliation repairs it
```

Requires Docker (`docker compose up --build` must work on its own first) and
takes roughly a minute per script, mostly image build + Raft election waits.

`chaos_lib.py` holds the shared helpers (compose up/down, health checks via
`docker exec`, gateway HTTP calls, polling). It is not a script on its own.

## What each one proves

- **`chaos_kill_leader.py`** — the propose.txt scenario "a server goes
  offline while a client is working with it": `docker kill` the current
  leader mid-session, then keep writing through the gateway. A new leader is
  elected and the gateway reroutes automatically, no manual reconfiguration.
- **`chaos_network_partition.py`** — `docker network disconnect` a follower
  (it keeps running, it just can't reach anyone). The majority side keeps
  serving; the isolated replica correctly drops `has_quorum` on its own
  (no split brain — the CP guarantee). On reconnect it converges back to the
  latest content.
- **`chaos_corruption.py`** — overwrites a follower's SQLite content directly
  without touching the stored hash, simulating bit rot. Raft has no idea this
  happened (it's not a log event); only the hash self-check in
  `server/reconciliation.py` catches and repairs it. Deliberately targets a
  follower, not the leader — a corrupted leader has no self-repair path yet
  (see the main README's known gaps).
