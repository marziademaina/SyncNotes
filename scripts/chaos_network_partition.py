#!/usr/bin/env python3

import subprocess

from chaos_lib import (
    SERVERS,
    compose_down,
    compose_up,
    container,
    container_network,
    gateway_get,
    gateway_post,
    get_file,
    health,
    leader_replica_id,
    ok,
    fail,
    wait_for_cluster_quorum,
    wait_until,
)

FILENAME = "chaos-partition.md"


def main() -> None:
    compose_up()
    try:
        wait_for_cluster_quorum()
        ok("cluster formed with quorum on all 3 replicas")

        leader = leader_replica_id()
        victim = next(s for s in SERVERS if s != leader)
        ok(f"leader is {leader}; partitioning follower {victim} away from the cluster")

        before = gateway_post(FILENAME, "before the partition")

        network = container_network(victim)
        print(f"$ docker network disconnect {network} {container(victim)}")
        subprocess.run(["docker", "network", "disconnect", network, container(victim)], check=True)
        ok(f"{victim} is now isolated")

        majority = [s for s in SERVERS if s != victim]

        def majority_still_has_quorum() -> bool:
            return all((h := health(s)) and h.get("has_quorum") for s in majority)

        wait_until(
            majority_still_has_quorum, timeout=20.0, description="majority side keeps quorum without the isolated follower"
        )
        ok("majority side (2 of 3) kept quorum through the partition")

        during = gateway_post(FILENAME, "written while partitioned", base_version=before["version"])
        if during["version"] != before["version"] + 1:
            fail(f"expected version {before['version'] + 1}, got {during['version']}")
        ok(f"gateway kept serving writes through the majority side (now version {during['version']})")

        def isolated_replica_lost_quorum() -> bool:
            h = health(victim)
            return h is not None and not h.get("has_quorum")

        # TCP takes a moment to notice the socket is gone; has_quorum only
        # flips once the isolated node's own connection count drops below a
        # majority, not the instant the network is cut.
        wait_until(
            isolated_replica_lost_quorum, timeout=30.0, description=f"{victim} detects it has lost quorum (no split brain)"
        )
        ok(f"{victim}, cut off alone, correctly dropped to has_quorum=false (no split brain)")

        print(f"$ docker network connect {network} {container(victim)}")
        subprocess.run(["docker", "network", "connect", network, container(victim)], check=True)
        ok(f"{victim} reconnected to the network")

        wait_for_cluster_quorum(timeout=30.0)
        ok("all 3 replicas report quorum again")

        def victim_caught_up() -> bool:
            f = get_file(victim, FILENAME)
            return f is not None and f["version"] == during["version"]

        wait_until(
            victim_caught_up,
            timeout=60.0,
            description=f"{victim} converges to version {during['version']} (Raft catch-up or reconciliation)",
        )
        ok(f"{victim} converged to the latest content after rejoining")

        final = gateway_get(FILENAME)
        if final["content"] != "written while partitioned":
            fail(f"unexpected final content: {final['content']!r}")

        print("\nPASS: the majority stayed available and consistent through the partition; the isolated replica self-healed after rejoining.")
    finally:
        compose_down()


if __name__ == "__main__":
    main()
