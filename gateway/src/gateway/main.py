import os

import httpx
from fastapi import FastAPI, HTTPException

REPLICA_URLS = [u.strip() for u in os.environ.get("REPLICA_URLS", "http://server-1:8000").split(",") if u.strip()]
REQUEST_TIMEOUT = 5.0

app = FastAPI(title="SyncNotes gateway")


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


async def _forward_to_leader(method: str, name: str, json_body: dict | None) -> dict:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as http_client:
        last_error = "no cluster leader available"
        for _ in range(2):
            leader = await _discover_leader(http_client)
            if leader is None:
                continue

            try:
                response = await http_client.request(method, f"{leader}/files/{name}", json=json_body)
            except httpx.RequestError as exc:
                last_error = f"leader {leader} unreachable: {exc}"
                continue

            if response.status_code == 503:
                last_error = response.json().get("detail", response.text)
                continue
            if response.status_code >= 400:
                raise HTTPException(status_code=response.status_code, detail=response.json().get("detail", response.text))
            return response.json()

    raise HTTPException(status_code=503, detail=last_error)


@app.get("/files/{name}")
async def download(name: str) -> dict:
    return await _forward_to_leader("GET", name, None)


@app.post("/files/{name}")
async def upload(name: str, body: dict) -> dict:
    return await _forward_to_leader("POST", name, body)
