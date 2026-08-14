import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pysyncobj import FAIL_REASON, SyncObjConf

from server.cluster import NotesStore, call_replicated, fail_reason_name
from server.db import FileRecord, get_session, init_db, list_files
from server.models import FileResponse, HealthResponse, UploadRequest

REPLICA_ID = os.environ.get("REPLICA_ID", "server-1")
RAFT_SELF_ADDR = os.environ.get("RAFT_SELF_ADDR", "localhost:9000")
RAFT_PEER_ADDRS = [p.strip() for p in os.environ.get("RAFT_PEER_ADDRS", "").split(",") if p.strip()]
RAFT_JOURNAL_FILE = os.environ.get("RAFT_JOURNAL_FILE", "./data/raft.journal")
RAFT_DUMP_FILE = os.environ.get("RAFT_DUMP_FILE", "./data/raft.dump")

store: NotesStore | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global store
    init_db()
    conf = SyncObjConf(journalFile=RAFT_JOURNAL_FILE, fullDumpFile=RAFT_DUMP_FILE, dynamicMembershipChange=False)
    store = NotesStore(RAFT_SELF_ADDR, RAFT_PEER_ADDRS, conf)
    yield
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
    )


@app.get("/files/{name}", response_model=FileResponse)
def download(name: str) -> FileResponse:
    session = get_session()
    try:
        record = session.get(FileRecord, name)
        if record is None:
            raise HTTPException(status_code=404, detail="file not found")
        return FileResponse(
            name=record.name,
            version=record.version,
            content=record.content,
            content_hash=record.content_hash,
            updated_at=record.updated_at,
        )
    finally:
        session.close()


@app.post("/files/{name}", response_model=FileResponse)
async def upload(name: str, body: UploadRequest) -> FileResponse:
    loop = asyncio.get_running_loop()
    op_id = str(uuid.uuid4())
    updated_at_iso = datetime.now(timezone.utc).isoformat()
    result, fail_reason = await call_replicated(loop, store.commit_upload, op_id, name, body.content, updated_at_iso)

    if fail_reason != FAIL_REASON.SUCCESS:
        raise HTTPException(status_code=503, detail=f"replication failed: {fail_reason_name(fail_reason)}")

    return FileResponse(
        name=result["name"],
        version=result["version"],
        content=result["content"],
        content_hash=result["content_hash"],
        updated_at=datetime.fromisoformat(result["updated_at"]),
    )


@app.get("/internal/manifest")
def manifest() -> dict[str, dict]:
    return list_files()
