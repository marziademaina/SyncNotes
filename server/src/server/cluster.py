import asyncio
from datetime import datetime

from pysyncobj import FAIL_REASON, SyncObj, SyncObjConf, replicated

from server.db import commit_write


class NotesStore(SyncObj):
    def __init__(self, self_addr: str, other_addrs: list[str], conf: SyncObjConf):
        super().__init__(self_addr, other_addrs, conf)

    @replicated
    def commit_upload(self, op_id: str, name: str, content: str, updated_at_iso: str) -> dict:
        return commit_write(op_id, name, content, datetime.fromisoformat(updated_at_iso))


TIMEOUT_FAIL_REASON = -1


async def call_replicated(loop: asyncio.AbstractEventLoop, method, *args, timeout: float = 10.0) -> tuple[dict | None, int]:
    future: asyncio.Future = loop.create_future()

    def callback(result, error):
        loop.call_soon_threadsafe(_resolve, future, result, error)

    method(*args, callback=callback)
    try:
        return await asyncio.wait_for(future, timeout=timeout)
    except asyncio.TimeoutError:
        return None, TIMEOUT_FAIL_REASON


def _resolve(future: asyncio.Future, result, error) -> None:
    if not future.done():
        future.set_result((result, error))


def fail_reason_name(code: int) -> str:
    if code == TIMEOUT_FAIL_REASON:
        return "TIMEOUT"
    return {
        FAIL_REASON.SUCCESS: "SUCCESS",
        FAIL_REASON.QUEUE_FULL: "QUEUE_FULL",
        FAIL_REASON.MISSING_LEADER: "MISSING_LEADER",
        FAIL_REASON.DISCARDED: "DISCARDED",
        FAIL_REASON.NOT_LEADER: "NOT_LEADER",
        FAIL_REASON.LEADER_CHANGED: "LEADER_CHANGED",
        FAIL_REASON.REQUEST_DENIED: "REQUEST_DENIED",
    }.get(code, f"UNKNOWN({code})")
