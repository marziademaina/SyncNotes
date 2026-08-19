import json
from pathlib import Path


def _sidecar_path(local_path: str) -> Path:
    return Path(f"{local_path}.syncnotes.json")


def read_base_version(local_path: str, name: str) -> int | None:
    path = _sidecar_path(local_path)
    if not path.exists():
        return None

    state = json.loads(path.read_text())
    if state.get("name") != name:
        return None
    return state.get("version")


def write_state(local_path: str, name: str, version: int) -> None:
    _sidecar_path(local_path).write_text(json.dumps({"name": name, "version": version}))
