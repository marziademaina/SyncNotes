#!/usr/bin/env python3
"""Chaos scenario: corrupt a follower's on-disk content directly (bit rot --
content changes, stored hash column doesn't), bypassing Raft entirely, and
verify reconciliation's hash self-check (propose.txt: "servers reconcile the
files and check their hashes every once in a while") detects and repairs it.

Deliberately targets a FOLLOWER, not the leader: reconciliation only pulls
from "the leader" as the trusted source, so a corrupted leader has no
mechanism today to detect or repair itself -- see README's known gaps.

Usage: python scripts/chaos_corruption.py
"""
from chaos_lib import (
    SERVERS,
    compose_down,
    compose_up,
    corrupt_content,
    fail,
    gateway_post,
    get_file,
    leader_replica_id,
    ok,
    trigger_reconcile,
    wait_for_cluster_quorum,
)

FILENAME = "chaos-corruption.md"


def main() -> None:
    compose_up()
    try:
        wait_for_cluster_quorum()
        ok("cluster formed with quorum on all 3 replicas")

        leader = leader_replica_id()
        victim = next(s for s in SERVERS if s != leader)

        written = gateway_post(FILENAME, "clean content")
        ok(f"wrote version {written['version']} through the gateway")

        corrupt_content(victim, FILENAME, "CORRUPTED BY CHAOS SCRIPT")
        ok(f"corrupted {victim}'s on-disk content directly (bypassing Raft entirely)")

        corrupted_view = get_file(victim, FILENAME)
        if corrupted_view["content"] != "CORRUPTED BY CHAOS SCRIPT":
            fail(f"corruption did not take effect on {victim}")
        ok(f"confirmed {victim} is serving corrupted content")

        result = trigger_reconcile(victim)
        if not result or FILENAME not in result.get("fixed", []):
            fail(f"POST /internal/reconcile on {victim} did not report {FILENAME} as fixed: {result}")
        ok(f"POST /internal/reconcile on {victim} repaired it against {result.get('peer')}")

        healed_view = get_file(victim, FILENAME)
        if healed_view["content"] != "clean content":
            fail(f"{victim} is still corrupted after reconcile: {healed_view['content']!r}")
        ok(f"{victim} now serves the correct content again")

        print("\nPASS: local corruption (invisible to Raft) was found and repaired by reconciliation's hash self-check.")
    finally:
        compose_down()


if __name__ == "__main__":
    main()
