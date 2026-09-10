#!/usr/bin/env python3

from chaos_lib import (
    SERVERS,
    compose_down,
    compose_up,
    container,
    gateway_get,
    gateway_post,
    get_file,
    health,
    leader_replica_id,
    ok,
    fail,
    run,
    wait_for_cluster_quorum,
    wait_until,
)

FILENAME = "chaos-restart.md"


def main() -> None:
    compose_up()
    try:
        wait_for_cluster_quorum()
        ok("cluster formed with quorum on all 3 replicas")

        leader = leader_replica_id()
        victim = next(s for s in SERVERS if s != leader)
        ok(f"leader is {leader}; will recreate follower {victim}")

        before = gateway_post(FILENAME, "before the restart")
        ok(f"wrote version {before['version']} through the gateway")
        
        run(["docker", "compose", "up", "-d", "--force-recreate", "--no-deps", victim])
        ok(f"{victim} recreated (new container, new IP)")

        wait_until(
            lambda: health(victim) is None or not health(victim).get("has_quorum"),
            timeout=15.0,
            description=f"{victim} briefly drops out while restarting",
        )

        during = gateway_post(FILENAME, "written while the node was recreated", base_version=before["version"])
        if during["version"] != before["version"] + 1:
            fail(f"expected version {before['version'] + 1}, got {during['version']}")
        ok(f"the majority kept serving writes (now version {during['version']})")

        def whole_cluster_back() -> bool:
            healths = [health(s) for s in SERVERS]
            return all(h and h.get("has_quorum") and h.get("peers_connected") == 2 for h in healths)

        wait_until(whole_cluster_back, timeout=60.0, description="all 3 replicas report quorum and 2 connected peers")
        ok(f"{victim} reconnected to both peers after being recreated")

        wait_until(
            lambda: (f := get_file(victim, FILENAME)) is not None and f["version"] == during["version"],
            timeout=60.0,
            description=f"{victim} converges to version {during['version']} (Raft catch-up or reconciliation)",
        )
        ok(f"{victim} caught up to the writes it missed while it was gone")

        final = gateway_get(FILENAME)
        if final["content"] != "written while the node was recreated":
            fail(f"unexpected final content: {final['content']!r}")

        print("\nPASS: a recreated node rejoined the Raft cluster on its own and converged to the latest content.")
    finally:
        compose_down()


if __name__ == "__main__":
    main()
