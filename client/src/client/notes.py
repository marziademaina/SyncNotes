# Business logic shared by the TUI and the CLI: fetching, opening, saving and creating notes.

from pathlib import Path

import requests

from client.api import download_file, list_files, upload_file
from client.sync_state import read_base_version, write_state


class SyncNotesError(Exception):
    """A user-facing error message, already worded for someone who is not a
    programmer, safe to show as-is in the TUI."""


def friendly_error(exc: Exception) -> str:
    if isinstance(exc, requests.ConnectionError):
        return "Cannot reach SyncNotes, is the server running?"
    if isinstance(exc, requests.Timeout):
        return "SyncNotes is not responding, try again in a moment."
    if isinstance(exc, requests.HTTPError):
        status = exc.response.status_code if exc.response is not None else None
        if status == 503:
            return "The cluster has no leader right now, retry in a moment."
        if status == 404:
            return "That note no longer exists on the server."
        detail = ""
        try:
            detail = exc.response.json().get("detail", "")
        except Exception:
            pass
        return f"SyncNotes reported an error{f': {detail}' if detail else '.'}"
    return f"Unexpected error: {exc}"


def fetch_notes(gateway: str) -> list[dict]:
    try:
        return list_files(gateway)
    except requests.RequestException as exc:
        raise SyncNotesError(friendly_error(exc)) from exc


def open_note(gateway: str, sync_dir: Path, name: str) -> Path:
    try:
        result = download_file(gateway, name)
    except requests.RequestException as exc:
        raise SyncNotesError(friendly_error(exc)) from exc

    try:
        sync_dir.mkdir(parents=True, exist_ok=True)
        local_path = sync_dir / name
        local_path.write_text(result["content"])
        write_state(str(local_path), name, result["version"])
    except OSError as exc:
        raise SyncNotesError(f"Could not save '{name}' locally: {exc}") from exc
    return local_path


def save_note(gateway: str, sync_dir: Path, name: str, content: str) -> dict:
    local_path = sync_dir / name
    base_version = read_base_version(str(local_path), name)
    try:
        result = upload_file(gateway, name, content, base_version)
    except requests.RequestException as exc:
        raise SyncNotesError(friendly_error(exc)) from exc

    try:
        sync_dir.mkdir(parents=True, exist_ok=True)
        local_path.write_text(result["content"])
        write_state(str(local_path), name, result["version"])
    except OSError as exc:
        raise SyncNotesError(f"Could not save '{name}' locally: {exc}") from exc
    return result


def create_note(gateway: str, sync_dir: Path, name: str) -> Path:
    save_note(gateway, sync_dir, name, "")
    return sync_dir / name


def describe_upload_outcome(base_version: int | None, submitted_content: str, result: dict) -> str:

    if result["content"] == submitted_content:
        return ""
    if base_version is None:
        return (
            " (none of your changes were applied, there was no saved version to compare "
            "against, so the server kept its own content; download the note again before editing)"
        )
    return " (server merged in changes since your last download)"
