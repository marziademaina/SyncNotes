import requests


def download_file(gateway_url: str, name: str) -> dict:
    response = requests.get(f"{gateway_url}/files/{name}", timeout=10)
    response.raise_for_status()
    return response.json()


def upload_file(gateway_url: str, name: str, content: str) -> dict:
    response = requests.post(f"{gateway_url}/files/{name}", json={"content": content}, timeout=10)
    response.raise_for_status()
    return response.json()
