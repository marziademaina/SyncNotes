import json
from pathlib import Path


def _sidecar_path(local_path: str) -> Path:
    path = Path(local_path)
    return path.with_name(f".{path.name}.syncnotes.json")


def read_base_version(local_path: str, name: str) -> int | None:
    path = _sidecar_path(local_path)
    if not path.exists():
        return None

    try:
        state = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if state.get("name") != name:
        return None
    return state.get("version")


def write_state(local_path: str, name: str, version: int) -> None:
    _sidecar_path(local_path).write_text(json.dumps({"name": name, "version": version}))


def clear_state(local_path: str) -> None:
    _sidecar_path(local_path).unlink(missing_ok=True)
