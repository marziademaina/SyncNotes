import asyncio
import time

from pysyncobj import SyncObjConf

from server.cluster import TIMEOUT_FAIL_REASON, NotesStore, call_replicated


def _wait_until(predicate, timeout: float, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class _Cluster:
    def __init__(self, tmp_path, free_port):
        self._tmp_path = tmp_path
        self._next_index = 0
        self.addrs = [f"127.0.0.1:{free_port()}" for _ in range(3)]
        self.stores: list[NotesStore] = [self._new_store(addr) for addr in self.addrs]

    def _new_store(self, addr: str) -> NotesStore:
        index = self._next_index
        self._next_index += 1
        others = [a for a in self.addrs if a != addr]
        conf = SyncObjConf(
            journalFile=str(self._tmp_path / f"raft-{index}.journal"),
            fullDumpFile=str(self._tmp_path / f"raft-{index}.dump"),
            dynamicMembershipChange=False,
            dnsCacheTime=0,
        )
        return NotesStore(addr, others, conf)

    def restart(self, addr: str) -> NotesStore:
        store = self._new_store(addr)
        self.stores.append(store)
        return store

    def leader_store(self) -> NotesStore | None:
        for store in self.stores:
            status = store.getStatus()
            if status.get("leader") == store.selfNode:
                return store
        return None

    def has_quorum_everywhere(self) -> bool:
        return all(s.getStatus().get("has_quorum") for s in self.stores)

    def kill(self, store: NotesStore) -> None:
        store.destroy()
        self.stores.remove(store)

    def destroy_all(self) -> None:
        for store in self.stores:
            store.destroy()


def _wait_for_full_quorum(cluster: _Cluster, timeout: float = 10.0) -> None:
    assert _wait_until(cluster.has_quorum_everywhere, timeout=timeout), "cluster never reached quorum on all nodes"


def _wait_for_leader(cluster: _Cluster, timeout: float = 10.0) -> NotesStore:
    leader = None
    assert _wait_until(lambda: (leader := cluster.leader_store()) is not None, timeout=timeout), (
        "no leader elected within timeout"
    )
    return leader


def test_losing_majority_drops_quorum_on_the_survivor(tmp_path, free_port):
    cluster = _Cluster(tmp_path, free_port)
    try:
        _wait_for_full_quorum(cluster)
        leader = _wait_for_leader(cluster)
        survivor = next(s for s in cluster.stores if s is not leader)
        for store in [s for s in cluster.stores if s is not survivor]:
            cluster.kill(store)

        assert _wait_until(
            lambda: survivor.getStatus().get("has_quorum") is False, timeout=10.0
        ), "a lone survivor should report has_quorum=False once the other two are gone"
    finally:
        cluster.destroy_all()


def test_quorum_returns_once_the_stopped_replicas_come_back(tmp_path, free_port):
    cluster = _Cluster(tmp_path, free_port)
    try:
        _wait_for_full_quorum(cluster)
        leader = _wait_for_leader(cluster)

        survivor = next(s for s in cluster.stores if s is not leader)
        stopped_addrs = [s.selfNode.id for s in cluster.stores if s is not survivor]
        for store in [s for s in cluster.stores if s is not survivor]:
            cluster.kill(store)

        assert _wait_until(lambda: survivor.getStatus().get("has_quorum") is False, timeout=10.0)

        for addr in stopped_addrs:
            cluster.restart(addr)

        _wait_for_full_quorum(cluster, timeout=10.0)
    finally:
        cluster.destroy_all()


async def test_write_never_succeeds_while_quorum_is_lost(tmp_path, free_port):
    cluster = _Cluster(tmp_path, free_port)
    try:
        _wait_for_full_quorum(cluster)
        leader = _wait_for_leader(cluster)

        survivor = next(s for s in cluster.stores if s is not leader)
        for store in [s for s in cluster.stores if s is not survivor]:
            cluster.kill(store)

        assert _wait_until(lambda: survivor.getStatus().get("has_quorum") is False, timeout=10.0)

        loop = asyncio.get_running_loop()
        result, fail_reason = await call_replicated(
            loop,
            survivor.commit_upload,
            "test-op-id",
            "quorum-loss.md",
            "should not be committed",
            "2026-01-01T00:00:00+00:00",
            None,
            timeout=2.0,
        )

        assert result is None
        assert fail_reason == TIMEOUT_FAIL_REASON
    finally:
        cluster.destroy_all()
