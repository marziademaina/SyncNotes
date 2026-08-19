#!/usr/bin/env python3
"""Chaos scenario: kill the current Raft leader outright and verify the
gateway automatically reroutes writes to the newly elected leader with no
manual reconfiguration -- the "a server goes offline while a client is
working with it" / fault-tolerance scenario from propose.txt.

Usage: python scripts/chaos_kill_leader.py
"""
import subprocess

from chaos_lib import (
    SERVERS,
    compose_down,
    compose_up,
    container,
    gateway_get,
    gateway_post,
    leader_replica_id,
    ok,
    fail,
    wait_for_cluster_quorum,
    wait_until,
)

FILENAME = "chaos-kill-leader.md"


def main() -> None:
    compose_up()
    try:
        wait_for_cluster_quorum()
        ok("cluster formed with quorum on all 3 replicas")

        leader = leader_replica_id()
        ok(f"current leader is {leader}")

        before = gateway_post(FILENAME, "before killing the leader")
        ok(f"wrote version {before['version']} through the gateway")

        print(f"$ docker kill {container(leader)}")
        subprocess.run(["docker", "kill", container(leader)], check=True)
        ok(f"killed {leader}")

        survivors = [s for s in SERVERS if s != leader]

        def new_leader_elected() -> bool:
            from chaos_lib import health

            return any(
                (h := health(s)) and h.get("has_quorum") and h.get("leader") and leader not in h["leader"]
                for s in survivors
            )

        wait_until(new_leader_elected, timeout=30.0, description="a new leader is elected among the survivors")
        ok("a new leader was elected without the killed replica")

        after = gateway_post(FILENAME, "after the leader died", base_version=before["version"])
        if after["version"] != before["version"] + 1:
            fail(f"expected version {before['version'] + 1} after failover, got {after['version']}")
        ok(f"gateway rerouted the write to the new leader (now version {after['version']})")

        read_back = gateway_get(FILENAME)
        if read_back["content"] != "after the leader died":
            fail(f"unexpected content after failover: {read_back['content']!r}")
        ok("read-after-write through the gateway is consistent post-failover")

        print("\nPASS: the gateway kept the system available and consistent through a leader crash.")
    finally:
        compose_down()


if __name__ == "__main__":
    main()
