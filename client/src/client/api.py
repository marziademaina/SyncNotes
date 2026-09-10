import hashlib
import json
from collections.abc import Iterator

import requests


def watch_events(gateway_url: str, timeout: float = 20.0) -> Iterator[list[dict]]:
    with requests.get(f"{gateway_url}/events", stream=True, timeout=timeout) as response:
        response.raise_for_status()
        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            yield json.loads(line[len("data: ") :])


def list_files(gateway_url: str) -> list[dict]:
    response = requests.get(f"{gateway_url}/files", timeout=10)
    response.raise_for_status()
    return response.json()


def download_file(gateway_url: str, name: str) -> dict:
    response = requests.get(f"{gateway_url}/files/{name}", timeout=10)
    response.raise_for_status()
    return response.json()


def _idempotency_key(name: str, base_version: int | None, content: str) -> str:
    return hashlib.sha256(f"{name}\x00{base_version}\x00{content}".encode()).hexdigest()


def upload_file(gateway_url: str, name: str, content: str, base_version: int | None = None) -> dict:
    body = {
        "content": content,
        "base_version": base_version,
        "op_id": _idempotency_key(name, base_version, content),
    }
    response = requests.post(f"{gateway_url}/files/{name}", json=body, timeout=10)
    response.raise_for_status()
    return response.json()


def _delete_key(name: str, version: int | None) -> str:
    return hashlib.sha256(f"delete\x00{name}\x00{version}".encode()).hexdigest()


def delete_file(gateway_url: str, name: str, version: int | None = None) -> dict:
    body = {"op_id": _delete_key(name, version)}
    response = requests.delete(f"{gateway_url}/files/{name}", json=body, timeout=10)
    response.raise_for_status()
    return response.json()
