import asyncio
import contextlib
import json
import logging
import os

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING) 
logger = logging.getLogger(__name__)

REPLICA_URLS = [u.strip() for u in os.environ.get("REPLICA_URLS", "http://server-1:8000").split(",") if u.strip()]
REQUEST_TIMEOUT = 5.0
EVENTS_POLL_INTERVAL_SECONDS = float(os.environ.get("EVENTS_POLL_INTERVAL_SECONDS", "1"))
EVENTS_HEARTBEAT_SECONDS = float(os.environ.get("EVENTS_HEARTBEAT_SECONDS", "15"))

app = FastAPI(title="SyncNotes gateway")

_subscribers: set[asyncio.Queue] = set()
_poll_task: asyncio.Task | None = None
_poll_lock = asyncio.Lock()
_last_snapshot: list | None = None


async def _get_health(http_client: httpx.AsyncClient, replica_url: str) -> dict | None:
    try:
        response = await http_client.get(f"{replica_url}/health")
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError:
        return None


async def _discover_leader(http_client: httpx.AsyncClient) -> str | None:
    for replica_url in REPLICA_URLS:
        health = await _get_health(http_client, replica_url)
        if health is None or not health.get("leader"):
            continue
        leader_host = health["leader"].rsplit(":", 1)[0]
        for candidate in REPLICA_URLS:
            if httpx.URL(candidate).host == leader_host:
                return candidate
    return None


@app.get("/health")
async def health() -> dict:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as http_client:
        leader = await _discover_leader(http_client)
    return {"status": "ok", "replicas": REPLICA_URLS, "leader": leader}


async def _forward_to_leader(method: str, path: str, json_body: dict | None) -> dict | list:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as http_client:
        last_error = "no cluster leader available"
        for _ in range(2):
            leader = await _discover_leader(http_client)
            if leader is None:
                continue

            try:
                response = await http_client.request(method, f"{leader}{path}", json=json_body)
            except httpx.RequestError as exc:
                last_error = f"leader {leader} unreachable: {exc}"
                continue

            if response.status_code == 503:
                last_error = response.json().get("detail", response.text)
                continue
            if response.status_code >= 400:
                raise HTTPException(status_code=response.status_code, detail=response.json().get("detail", response.text))
            return response.json()

    logger.warning("no leader available for %s %s: %s", method, path, last_error)
    raise HTTPException(status_code=503, detail=last_error)


@app.get("/files")
async def list_notes() -> list:
    return await _forward_to_leader("GET", "/files", None)


@app.get("/files/{name}")
async def download(name: str) -> dict:
    result = await _forward_to_leader("GET", f"/files/{name}", None)
    if isinstance(result, dict) and result.get("deleted"):
        raise HTTPException(status_code=404, detail="file not found")
    return result


@app.post("/files/{name}")
async def upload(name: str, body: dict) -> dict:
    result = await _forward_to_leader("POST", f"/files/{name}", body)
    logger.info("note sent: %r -> version %s", name, result.get("version"))
    if _subscribers:
        await _refresh_and_broadcast()
    return result


@app.delete("/files/{name}")
async def delete(name: str, body: dict | None = None) -> dict:
    result = await _forward_to_leader("DELETE", f"/files/{name}", body)
    logger.info("note deleted: %r -> version %s", name, result.get("version"))
    if _subscribers:
        await _refresh_and_broadcast()
    return result


def _put_latest(queue: asyncio.Queue, item: list) -> None:
    if queue.full():
        with contextlib.suppress(asyncio.QueueEmpty):
            queue.get_nowait()
    queue.put_nowait(item)


async def _fetch_files() -> list | None:
    try:
        return await _forward_to_leader("GET", "/files", None)
    except HTTPException:
        return None


async def _refresh_and_broadcast() -> None:
    global _last_snapshot
    files = await _fetch_files()
    if files is not None and files != _last_snapshot:
        _last_snapshot = files
        logger.info("note list updated (%d note(s)) - broadcasting to %d subscriber(s)", len(files), len(_subscribers))
        for queue in list(_subscribers):
            _put_latest(queue, files)


async def _poll_loop() -> None:
    global _poll_task
    while True:
        while _subscribers:
            await asyncio.sleep(EVENTS_POLL_INTERVAL_SECONDS)
            if _subscribers:
                await _refresh_and_broadcast()

        async with _poll_lock:
            if not _subscribers:
                _poll_task = None
                return


@app.get("/events")
async def events() -> StreamingResponse:
    async def stream():
        queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        global _poll_task, _last_snapshot
        async with _poll_lock:
            _subscribers.add(queue)
            if _poll_task is None or _poll_task.done():
                _poll_task = asyncio.create_task(_poll_loop())
        logger.info("client connected to /events (%d subscriber(s))", len(_subscribers))
        try:
            initial = await _fetch_files()
            if initial is None:
                initial = _last_snapshot or []
            elif _last_snapshot is None:
                _last_snapshot = initial
            yield f"data: {json.dumps(initial)}\n\n"
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=EVENTS_HEARTBEAT_SECONDS)
                    yield f"data: {json.dumps(item)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            _subscribers.discard(queue)
            logger.info("client disconnected from /events (%d subscriber(s) left)", len(_subscribers))

    return StreamingResponse(stream(), media_type="text/event-stream")
