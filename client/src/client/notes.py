from collections.abc import Iterator
from datetime import datetime, tzinfo
from pathlib import Path

import requests

from client.api import delete_file, download_file, list_files, upload_file, watch_events
from client.sync_state import clear_state, read_base_version, write_state


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


def watch_notes(gateway: str) -> Iterator[list[dict]]:
    try:
        yield from watch_events(gateway)
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


def delete_note(gateway: str, sync_dir: Path, name: str) -> None:
    local_path = sync_dir / name
    version = read_base_version(str(local_path), name)
    if version is None:
        version = next((n["version"] for n in fetch_notes(gateway) if n["name"] == name), None)

    try:
        delete_file(gateway, name, version)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status != 404:
            raise SyncNotesError(friendly_error(exc)) from exc
    except requests.RequestException as exc:
        raise SyncNotesError(friendly_error(exc)) from exc

    local_path.unlink(missing_ok=True)
    clear_state(str(local_path))


def describe_delete_outcome(name: str) -> str:
    return f"deleted '{name}'"


def note_exists(gateway: str, name: str) -> bool:
    return any(note["name"] == name for note in fetch_notes(gateway))


def create_note(gateway: str, sync_dir: Path, name: str) -> dict:
    if note_exists(gateway, name):
        raise SyncNotesError(f"a note named '{name}' already exists - open it instead")
    return save_note(gateway, sync_dir, name, "")


def describe_create_outcome(name: str, result: dict) -> str:
    if result.get("content", "") == "":
        return f"created '{name}'"
    return (
        f"'{name}' already existed on the server (version {result['version']}) - "
        f"nothing was created, open it instead"
    )


def format_updated_at(raw: str, tz: tzinfo | None = None) -> str:
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return f"{raw} UTC"
    local = dt.astimezone(tz)
    offset = local.strftime("%z") or "+0000"
    return f"{local.strftime('%Y-%m-%d')} T: {local.strftime('%H:%M:%S')}{offset[:3]}:{offset[3:]}"


def describe_upload_outcome(base_version: int | None, submitted_content: str, result: dict) -> str:

    if result["content"] == submitted_content:
        return " (no conflicting changes - saved as-is)"
    if base_version is None:
        return (
            " (none of your changes were applied, there was no saved version to compare "
            "against, so the server kept its own content; download the note again before editing)"
        )
    if result.get("had_conflict"):
        return (
            " (someone else edited the same lines - your changes to those lines were dropped, "
            "the rest of your edit was kept)"
        )
    return " (server merged in changes since your last download)"
