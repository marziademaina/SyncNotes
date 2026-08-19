import requests


def download_file(gateway_url: str, name: str) -> dict:
    response = requests.get(f"{gateway_url}/files/{name}", timeout=10)
    response.raise_for_status()
    return response.json()


def upload_file(gateway_url: str, name: str, content: str, base_version: int | None = None) -> dict:
    body = {"content": content, "base_version": base_version}
    response = requests.post(f"{gateway_url}/files/{name}", json=body, timeout=10)
    response.raise_for_status()
    return response.json()
