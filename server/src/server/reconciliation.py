import asyncio
import logging

import httpx

from server.db import apply_if_newer, find_locally_corrupted_files, list_files

logger = logging.getLogger("server.reconciliation")


async def fetch_manifest(peer_url: str) -> dict[str, dict]:
    async with httpx.AsyncClient(timeout=5.0) as http_client:
        response = await http_client.get(f"{peer_url}/internal/manifest")
        response.raise_for_status()
        return response.json()


async def fetch_file(peer_url: str, name: str) -> dict:
    async with httpx.AsyncClient(timeout=5.0) as http_client:
        response = await http_client.get(f"{peer_url}/files/{name}")
        response.raise_for_status()
        return response.json()


async def reconcile_with_peer(peer_url: str) -> list[str]:
    local_manifest = list_files()
    locally_corrupted = find_locally_corrupted_files()
    remote_manifest = await fetch_manifest(peer_url)

    fixed = []
    for name, remote in remote_manifest.items():
        local = local_manifest.get(name)
        needs_fetch = (
            local is None
            or name in locally_corrupted
            or remote["version"] > local["version"]
            or (remote["version"] == local["version"] and remote["content_hash"] != local["content_hash"])
        )
        if needs_fetch:
            file_data = await fetch_file(peer_url, name)
            status, _ = apply_if_newer(
                name,
                file_data["version"],
                file_data["content"],
                file_data["content_hash"],
                force=name in locally_corrupted,
            )
            if status == "applied":
                fixed.append(name)
    return fixed


async def reconciliation_loop(peer_url: str, interval_seconds: float) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await reconcile_with_peer(peer_url)
        except httpx.HTTPError as exc:
            logger.warning("reconciliation against %s failed: %s", peer_url, exc)
