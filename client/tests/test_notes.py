import json

import pytest
import requests

import client.notes as notes


def test_fetch_notes_returns_the_server_list(monkeypatch):
    monkeypatch.setattr(notes, "list_files", lambda gateway: [{"name": "a.md", "version": 1}])

    assert notes.fetch_notes("http://gateway") == [{"name": "a.md", "version": 1}]


def test_fetch_notes_wraps_connection_errors_in_a_friendly_message(monkeypatch):
    def boom(gateway):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(notes, "list_files", boom)

    with pytest.raises(notes.SyncNotesError, match="Cannot reach SyncNotes"):
        notes.fetch_notes("http://gateway")


def test_open_note_writes_the_file_and_sync_state(tmp_path, monkeypatch):
    monkeypatch.setattr(
        notes, "download_file", lambda gateway, name: {"content": "hello", "version": 3}
    )

    local_path = notes.open_note("http://gateway", tmp_path, "notes.md")

    assert local_path == tmp_path / "notes.md"
    assert local_path.read_text() == "hello"
    sidecar = json.loads((tmp_path / ".notes.md.syncnotes.json").read_text())
    assert sidecar == {"name": "notes.md", "version": 3}


def test_open_note_wraps_a_404_in_a_friendly_message(tmp_path, monkeypatch):
    response = requests.Response()
    response.status_code = 404

    def boom(gateway, name):
        raise requests.HTTPError(response=response)

    monkeypatch.setattr(notes, "download_file", boom)

    with pytest.raises(notes.SyncNotesError, match="no longer exists"):
        notes.open_note("http://gateway", tmp_path, "notes.md")


def test_save_note_sends_the_base_version_from_the_last_open(tmp_path, monkeypatch):
    (tmp_path / ".notes.md.syncnotes.json").write_text(json.dumps({"name": "notes.md", "version": 4}))
    captured = {}

    def fake_upload(gateway, name, content, base_version):
        captured["base_version"] = base_version
        return {"version": 5, "content": content, "content_hash": "abc"}

    monkeypatch.setattr(notes, "upload_file", fake_upload)

    notes.save_note("http://gateway", tmp_path, "notes.md", "edited")

    assert captured["base_version"] == 4


def test_save_note_writes_back_server_merged_content(tmp_path, monkeypatch):
    monkeypatch.setattr(
        notes,
        "upload_file",
        lambda gateway, name, content, base_version: {
            "version": 6,
            "content": "server merged content",
            "content_hash": "def",
        },
    )

    notes.save_note("http://gateway", tmp_path, "notes.md", "my edit")

    local_path = tmp_path / "notes.md"
    assert local_path.read_text() == "server merged content"
    sidecar = json.loads((tmp_path / ".notes.md.syncnotes.json").read_text())
    assert sidecar == {"name": "notes.md", "version": 6}


def test_open_note_wraps_local_write_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "download_file", lambda gateway, name: {"content": "hi", "version": 1})
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")

    with pytest.raises(notes.SyncNotesError, match="Could not save"):
        notes.open_note("http://gateway", blocked, "notes.md")


def test_save_note_wraps_local_write_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(
        notes,
        "upload_file",
        lambda gateway, name, content, base_version: {"version": 1, "content": content, "content_hash": "abc"},
    )
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")

    with pytest.raises(notes.SyncNotesError, match="Could not save"):
        notes.save_note("http://gateway", blocked, "notes.md", "hi")


def test_describe_upload_outcome_is_silent_when_the_server_kept_the_upload_as_is():
    result = {"content": "my edit", "version": 2}
    assert notes.describe_upload_outcome(3, "my edit", result) == ""


def test_describe_upload_outcome_reports_a_partial_merge_when_base_version_is_known():
    result = {"content": "combined content", "version": 5}
    message = notes.describe_upload_outcome(4, "my edit", result)
    assert "merged in changes" in message


def test_describe_upload_outcome_reports_a_full_discard_when_base_version_is_missing():
    result = {"content": "server's untouched content", "version": 2}
    message = notes.describe_upload_outcome(None, "my edit", result)
    assert "none of your changes were applied" in message
    assert "merged" not in message


def test_create_note_uploads_empty_content(tmp_path, monkeypatch):
    captured = {}

    def fake_upload(gateway, name, content, base_version):
        captured["content"] = content
        return {"version": 1, "content": content, "content_hash": "abc"}

    monkeypatch.setattr(notes, "upload_file", fake_upload)

    local_path = notes.create_note("http://gateway", tmp_path, "new.md")

    assert captured["content"] == ""
    assert local_path == tmp_path / "new.md"
    assert local_path.read_text() == ""
