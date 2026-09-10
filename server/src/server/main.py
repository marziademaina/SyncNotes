import asyncio
import contextlib
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pysyncobj import FAIL_REASON, SyncObjConf

from server.cluster import NotesStore, call_replicated, fail_reason_name, leader_http_url
from server.db import FileRecord, get_session, init_db, list_files, list_files_summary
from server.models import DeleteRequest, FileResponse, FileSummary, HealthResponse, UploadRequest
from server.reconciliation import reconcile_with_peer, reconciliation_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  
logging.getLogger("pysyncobj.dns_resolver").setLevel(logging.ERROR)
logger = logging.getLogger(__name__)

REPLICA_ID = os.environ.get("REPLICA_ID", "server-1")
RAFT_SELF_ADDR = os.environ.get("RAFT_SELF_ADDR", "localhost:9000")
RAFT_PEER_ADDRS = [p.strip() for p in os.environ.get("RAFT_PEER_ADDRS", "").split(",") if p.strip()]
RAFT_JOURNAL_FILE = os.environ.get("RAFT_JOURNAL_FILE", "./data/raft.journal")
RAFT_DUMP_FILE = os.environ.get("RAFT_DUMP_FILE", "./data/raft.dump")
HTTP_PORT = os.environ.get("HTTP_PORT", "8000")
RECONCILE_INTERVAL_SECONDS = float(os.environ.get("RECONCILE_INTERVAL_SECONDS", "30"))

CLUSTER_HEARTBEAT_SECONDS = float(os.environ.get("CLUSTER_HEARTBEAT_SECONDS", "15"))

store: NotesStore | None = None

_RAFT_STATE_NAMES = {0: "FOLLOWER", 1: "CANDIDATE", 2: "LEADER"}


def _leader_http_url() -> str | None:
    return leader_http_url(store, RAFT_SELF_ADDR, HTTP_PORT)


def _peers_connected(status: dict) -> int:
    return sum(1 for key, value in status.items() if key.startswith("partner_node_status_") and value == 2)


def _on_raft_state_changed(old_state: int, new_state: int) -> None:
    logger.info(
        "raft state: %s -> %s",
        _RAFT_STATE_NAMES.get(old_state, old_state),
        _RAFT_STATE_NAMES.get(new_state, new_state),
    )


def _raft_conf() -> SyncObjConf:
    return SyncObjConf(
        journalFile=RAFT_JOURNAL_FILE,
        fullDumpFile=RAFT_DUMP_FILE,
        dynamicMembershipChange=False,
        dnsCacheTime=0,
        dnsFailCacheTime=5.0,
        onStateChanged=_on_raft_state_changed,
    )


def _cluster_summary(status: dict) -> tuple:
    return (
        _RAFT_STATE_NAMES.get(status["state"], status["state"]),
        status["leader"],
        status["has_quorum"],
        _peers_connected(status),
        status["partner_nodes_count"],
    )


async def _cluster_heartbeat(interval_seconds: float, keepalive_ticks: int = 20) -> None:
    last: tuple | None = None
    ticks = 0
    while True:
        await asyncio.sleep(interval_seconds)
        summary = _cluster_summary(store.getStatus())
        ticks += 1
        if summary != last or ticks >= keepalive_ticks:
            logger.info("cluster: state=%s leader=%s quorum=%s peers=%d/%d", *summary)
            last, ticks = summary, 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    global store
    init_db()
    store = NotesStore(RAFT_SELF_ADDR, RAFT_PEER_ADDRS, _raft_conf())
    reconcile_task = asyncio.create_task(reconciliation_loop(_leader_http_url, RECONCILE_INTERVAL_SECONDS))
    heartbeat_task = asyncio.create_task(_cluster_heartbeat(CLUSTER_HEARTBEAT_SECONDS))
    yield
    for task in (reconcile_task, heartbeat_task):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    store.destroy()


app = FastAPI(title=f"SyncNotes server ({REPLICA_ID})", lifespan=lifespan)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    status = store.getStatus()
    leader = status["leader"]
    return HealthResponse(
        status="ok",
        replica_id=REPLICA_ID,
        state=status["state"],
        leader=str(leader) if leader is not None else None,
        has_quorum=status["has_quorum"],
        peers_connected=_peers_connected(status),
        peers_total=status["partner_nodes_count"],
    )


@app.get("/files", response_model=list[FileSummary])
def list_notes() -> list[dict]:
    notes = list_files_summary()
    logger.debug("notes found: %d", len(notes))  # polled ~every 3s by the gateway - not INFO-worthy
    return notes


@app.get("/files/{name}", response_model=FileResponse)
def download(name: str) -> FileResponse:
    session = get_session()
    try:
        record = session.get(FileRecord, name)
        if record is None:
            logger.info("note not found: %r", name)
            raise HTTPException(status_code=404, detail="file not found")
        logger.info("note found: %r (v%d)%s", name, record.version, " (deleted)" if record.deleted else "")
        return FileResponse(
            name=record.name,
            version=record.version,
            content=record.content,
            content_hash=record.content_hash,
            updated_at=record.updated_at,
            deleted=record.deleted,
        )
    finally:
        session.close()


@app.post("/files/{name}", response_model=FileResponse)
async def upload(name: str, body: UploadRequest) -> FileResponse:
    loop = asyncio.get_running_loop()
    op_id = body.op_id or str(uuid.uuid4())
    updated_at_iso = datetime.now(timezone.utc).isoformat()
    result, fail_reason = await call_replicated(
        loop, store.commit_upload, op_id, name, body.content, updated_at_iso, body.base_version
    )

    if fail_reason != FAIL_REASON.SUCCESS:
        logger.warning("note update failed for %r: %s", name, fail_reason_name(fail_reason))
        raise HTTPException(status_code=503, detail=f"replication failed: {fail_reason_name(fail_reason)}")

    logger.info(
        "note updated: %r -> version %d%s", name, result["version"], " (conflict)" if result["had_conflict"] else ""
    )
    return FileResponse(
        name=result["name"],
        version=result["version"],
        content=result["content"],
        content_hash=result["content_hash"],
        updated_at=datetime.fromisoformat(result["updated_at"]),
        had_conflict=result["had_conflict"],
        deleted=result.get("deleted", False),
    )


@app.delete("/files/{name}", response_model=FileResponse)
async def delete(name: str, body: DeleteRequest | None = None) -> FileResponse:
    loop = asyncio.get_running_loop()
    op_id = (body.op_id if body else None) or str(uuid.uuid4())
    updated_at_iso = datetime.now(timezone.utc).isoformat()
    result, fail_reason = await call_replicated(loop, store.commit_deletion, op_id, name, updated_at_iso)

    if fail_reason != FAIL_REASON.SUCCESS:
        logger.warning("note delete failed for %r: %s", name, fail_reason_name(fail_reason))
        raise HTTPException(status_code=503, detail=f"replication failed: {fail_reason_name(fail_reason)}")

    logger.info("note deleted: %r -> version %d", name, result["version"])
    return FileResponse(
        name=result["name"],
        version=result["version"],
        content=result["content"],
        content_hash=result["content_hash"],
        updated_at=datetime.fromisoformat(result["updated_at"]),
        had_conflict=result["had_conflict"],
        deleted=result.get("deleted", True),
    )


@app.get("/internal/manifest")
def manifest() -> dict[str, dict]:
    return list_files()


@app.post("/internal/reconcile")
async def reconcile() -> dict:
    peer_url = _leader_http_url()
    if peer_url is None:
        return {"fixed": [], "peer": None}
    fixed = await reconcile_with_peer(peer_url)
    return {"fixed": fixed, "peer": peer_url}
