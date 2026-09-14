import json
from datetime import timedelta, timezone

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


def test_watch_notes_yields_each_list_from_watch_events(monkeypatch):
    updates = [[{"name": "a.md", "version": 1}], [{"name": "a.md", "version": 2}]]
    monkeypatch.setattr(notes, "watch_events", lambda gateway: iter(updates))

    assert list(notes.watch_notes("http://gateway")) == updates


def test_watch_notes_wraps_connection_errors_in_a_friendly_message(monkeypatch):
    def boom(gateway):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(notes, "watch_events", boom)

    with pytest.raises(notes.SyncNotesError, match="Cannot reach SyncNotes"):
        list(notes.watch_notes("http://gateway"))


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


def test_describe_upload_outcome_confirms_a_clean_save_when_the_server_kept_the_upload_as_is():
    result = {"content": "my edit", "version": 2}
    message = notes.describe_upload_outcome(3, "my edit", result)
    assert message.strip() != ""
    assert "no conflicting changes" in message


def test_describe_upload_outcome_reports_a_partial_merge_when_base_version_is_known():
    result = {"content": "combined content", "version": 5}
    message = notes.describe_upload_outcome(4, "my edit", result)
    assert "merged in changes" in message


def test_describe_upload_outcome_reports_a_dropped_edit_on_a_real_conflict():
    result = {"content": "combined content", "version": 5, "had_conflict": True}
    message = notes.describe_upload_outcome(4, "my edit", result)
    assert "same lines" in message
    assert "dropped" in message
    assert "merged in changes" not in message


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

    monkeypatch.setattr(notes, "list_files", lambda gateway: [])
    monkeypatch.setattr(notes, "upload_file", fake_upload)

    result = notes.create_note("http://gateway", tmp_path, "new.md")

    assert captured["content"] == ""
    assert result["version"] == 1
    assert (tmp_path / "new.md").read_text() == ""


def test_create_note_rejects_a_name_that_already_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "list_files", lambda gateway: [{"name": "ciao", "version": 2}])

    def fail_upload(*args, **kwargs):
        raise AssertionError("create_note must not upload over an existing note")

    monkeypatch.setattr(notes, "upload_file", fail_upload)

    with pytest.raises(notes.SyncNotesError, match="already exists"):
        notes.create_note("http://gateway", tmp_path, "ciao")


def test_delete_note_calls_the_api_and_removes_local_file_and_sidecar(tmp_path, monkeypatch):
    (tmp_path / "notes.md").write_text("hello")
    (tmp_path / ".notes.md.syncnotes.json").write_text(json.dumps({"name": "notes.md", "version": 4}))
    captured = {}

    def fake_delete(gateway, name, version):
        captured["name"] = name
        captured["version"] = version
        return {"version": 5, "deleted": True}

    monkeypatch.setattr(notes, "delete_file", fake_delete)

    notes.delete_note("http://gateway", tmp_path, "notes.md")

    assert captured == {"name": "notes.md", "version": 4}
    assert not (tmp_path / "notes.md").exists()
    assert not (tmp_path / ".notes.md.syncnotes.json").exists()


def test_delete_note_treats_a_404_as_already_deleted(tmp_path, monkeypatch):
    (tmp_path / "notes.md").write_text("hello")
    (tmp_path / ".notes.md.syncnotes.json").write_text(json.dumps({"name": "notes.md", "version": 4}))
    response = requests.Response()
    response.status_code = 404

    def boom(gateway, name, version):
        raise requests.HTTPError(response=response)

    monkeypatch.setattr(notes, "delete_file", boom)

    notes.delete_note("http://gateway", tmp_path, "notes.md")  # no exception

    assert not (tmp_path / "notes.md").exists()


def test_delete_note_wraps_other_errors(tmp_path, monkeypatch):
    (tmp_path / ".notes.md.syncnotes.json").write_text(json.dumps({"name": "notes.md", "version": 4}))

    def boom(gateway, name, version):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(notes, "delete_file", boom)

    with pytest.raises(notes.SyncNotesError, match="Cannot reach SyncNotes"):
        notes.delete_note("http://gateway", tmp_path, "notes.md")


def test_describe_create_outcome_reports_a_fresh_creation():
    result = {"name": "todo.md", "version": 1, "content": ""}
    assert notes.describe_create_outcome("todo.md", result) == "created 'todo.md'"


def test_describe_create_outcome_warns_when_the_note_already_existed():
    result = {"name": "todo.md", "version": 3, "content": "someone else's note"}
    message = notes.describe_create_outcome("todo.md", result)
    assert "already existed" in message
    assert "nothing was created" in message


def test_format_updated_at_converts_utc_to_the_given_local_offset():
    ahead = timezone(timedelta(hours=5, minutes=30))
    assert notes.format_updated_at("2026-01-01T00:00:00+00:00", tz=ahead) == "2026-01-01 T: 05:30:00+05:30"


def test_format_updated_at_handles_a_negative_offset_crossing_midnight():
    behind = timezone(timedelta(hours=-8))
    assert notes.format_updated_at("2026-01-01T02:00:00+00:00", tz=behind) == "2025-12-31 T: 18:00:00-08:00"


def test_format_updated_at_is_a_no_op_when_already_in_utc():
    assert notes.format_updated_at("2026-01-01T00:00:00+00:00", tz=timezone.utc) == "2026-01-01 T: 00:00:00+00:00"


def test_format_updated_at_falls_back_to_the_raw_string_on_bad_input():
    assert notes.format_updated_at("not-a-timestamp") == "not-a-timestamp UTC"
