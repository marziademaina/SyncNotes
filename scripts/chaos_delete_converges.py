#!/usr/bin/env python3
import urllib.error

from chaos_lib import (
    SERVERS,
    compose_down,
    compose_up,
    db_files,
    fail,
    gateway_delete,
    gateway_get,
    gateway_post,
    get_file,
    health,
    leader_replica_id,
    ok,
    run,
    wait_for_cluster_quorum,
    wait_until,
)

FILENAME = "chaos-delete.md"


def _tombstoned_everywhere() -> bool:
    for replica in SERVERS:
        rows = db_files(replica)
        if rows is None:
            return False
        row = next((r for r in rows if r["name"] == FILENAME), None)
        if row is None or not row["deleted"]:
            return False
    return True


def main() -> None:
    compose_up()
    try:
        wait_for_cluster_quorum()
        ok("cluster formed with quorum on all 3 replicas")

        leader = leader_replica_id()
        victim = next(s for s in SERVERS if s != leader)
        ok(f"leader is {leader}; will take follower {victim} offline before the delete")

        created = gateway_post(FILENAME, "this note will be deleted")
        ok(f"created {FILENAME} at version {created['version']}")

        wait_until(
            lambda: (f := get_file(victim, FILENAME)) is not None and f["version"] == created["version"],
            timeout=30.0,
            description=f"{victim} has the note before it goes offline",
        )
        ok(f"{victim} replicated the note (it will hold a stale, live copy)")

        run(["docker", "compose", "stop", victim])
        wait_until(lambda: health(victim) is None, timeout=15.0, description=f"{victim} is down")
        ok(f"{victim} stopped")

        deleted = gateway_delete(FILENAME)
        ok(f"deleted {FILENAME} through the gateway (tombstone at version {deleted['version']})")

        try:
            gateway_get(FILENAME)
            fail("gateway still serves the deleted note")
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
        ok("gateway returns 404 for the deleted note")

        run(["docker", "compose", "start", victim])

        def whole_cluster_back() -> bool:
            healths = [health(s) for s in SERVERS]
            return all(h and h.get("has_quorum") and h.get("peers_connected") == 2 for h in healths)

        wait_until(whole_cluster_back, timeout=60.0, description="all 3 replicas report quorum and 2 connected peers")
        ok(f"{victim} rejoined the cluster")

        wait_until(
            _tombstoned_everywhere,
            timeout=90.0,
            description=f"all 3 replicas show {FILENAME} tombstoned (Raft catch-up or reconciliation)",
        )
        ok(f"{victim} applied the deletion it missed - the note was not resurrected")

        try:
            gateway_get(FILENAME)
            fail("note came back after the node rejoined")
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
        ok("gateway still returns 404 after the offline node rejoined")

        print("\nPASS: a delete issued while a node was offline converged everywhere with no resurrection.")
    finally:
        compose_down()


if __name__ == "__main__":
    main()
