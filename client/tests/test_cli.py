import json
import sys

import client.cli as cli


def test_download_command_writes_file_and_sidecar(tmp_path, monkeypatch, capsys):
    out_path = tmp_path / "notes.md"
    monkeypatch.setattr(cli, "download_file", lambda gateway, name: {"content": "hello", "version": 1})
    monkeypatch.setattr(sys, "argv", ["syncnotes-client", "download", "notes.md", "--out", str(out_path)])

    cli.main()

    assert out_path.read_text() == "hello"
    sidecar = tmp_path / ".notes.md.syncnotes.json"
    assert json.loads(sidecar.read_text()) == {"name": "notes.md", "version": 1}
    assert "saved notes.md (version 1)" in capsys.readouterr().out


def test_download_command_without_out_writes_to_stdout(monkeypatch, capsys):
    monkeypatch.setattr(cli, "download_file", lambda gateway, name: {"content": "hello", "version": 1})
    monkeypatch.setattr(sys, "argv", ["syncnotes-client", "download", "notes.md"])

    cli.main()

    assert capsys.readouterr().out == "hello"


def test_upload_command_sends_base_version_from_the_previous_download(tmp_path, monkeypatch, capsys):
    local_path = tmp_path / "notes.md"
    local_path.write_text("edited content")
    (tmp_path / ".notes.md.syncnotes.json").write_text(json.dumps({"name": "notes.md", "version": 4}))

    captured = {}

    def fake_upload(gateway, name, content, base_version):
        captured["base_version"] = base_version
        captured["content"] = content
        return {"version": 5, "content": content, "content_hash": "abcdef123456"}

    monkeypatch.setattr(cli, "upload_file", fake_upload)
    monkeypatch.setattr(sys, "argv", ["syncnotes-client", "upload", "notes.md", str(local_path)])

    cli.main()

    assert captured["base_version"] == 4
    assert captured["content"] == "edited content"
    sidecar = json.loads((tmp_path / ".notes.md.syncnotes.json").read_text())
    assert sidecar == {"name": "notes.md", "version": 5}
    assert "now version 5" in capsys.readouterr().out


def test_upload_command_without_a_prior_download_sends_no_base_version(tmp_path, monkeypatch):
    local_path = tmp_path / "notes.md"
    local_path.write_text("brand new content")

    captured = {}

    def fake_upload(gateway, name, content, base_version):
        captured["base_version"] = base_version
        return {"version": 1, "content": content, "content_hash": "abcdef123456"}

    monkeypatch.setattr(cli, "upload_file", fake_upload)
    monkeypatch.setattr(sys, "argv", ["syncnotes-client", "upload", "notes.md", str(local_path)])

    cli.main()

    assert captured["base_version"] is None


def test_upload_command_notes_when_the_server_merged_something_different(tmp_path, monkeypatch, capsys):
    local_path = tmp_path / "notes.md"
    local_path.write_text("my edit")
    (tmp_path / ".notes.md.syncnotes.json").write_text(json.dumps({"name": "notes.md", "version": 4}))

    monkeypatch.setattr(
        cli,
        "upload_file",
        lambda gateway, name, content, base_version: {
            "version": 5,
            "content": "server merged content",
            "content_hash": "abcdef123456",
        },
    )
    monkeypatch.setattr(sys, "argv", ["syncnotes-client", "upload", "notes.md", str(local_path)])

    cli.main()

    assert "merged in changes" in capsys.readouterr().out
    assert local_path.read_text() == "server merged content"
    sidecar = json.loads((tmp_path / ".notes.md.syncnotes.json").read_text())
    assert sidecar == {"name": "notes.md", "version": 5}


def test_upload_command_warns_when_a_missing_base_version_discards_the_edit(tmp_path, monkeypatch, capsys):
    local_path = tmp_path / "notes.md"
    local_path.write_text("my edit, never saved anywhere")

    monkeypatch.setattr(
        cli,
        "upload_file",
        lambda gateway, name, content, base_version: {
            "version": 2,
            "content": "whatever the server already had",
            "content_hash": "abcdef123456",
        },
    )
    monkeypatch.setattr(sys, "argv", ["syncnotes-client", "upload", "notes.md", str(local_path)])

    cli.main()

    out = capsys.readouterr().out
    assert "none of your changes were applied" in out
    assert "merged in changes" not in out


def test_list_command_prints_the_notes_on_the_server(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "list_files",
        lambda gateway: [
            {"name": "notes.md", "version": 3, "updated_at": "2026-01-01T00:00:00"},
            {"name": "todo.md", "version": 1, "updated_at": "2026-01-02T00:00:00"},
        ],
    )
    monkeypatch.setattr(sys, "argv", ["syncnotes-client", "list"])

    cli.main()

    out = capsys.readouterr().out
    assert "notes.md" in out and "v3" in out
    assert "todo.md" in out and "v1" in out


def test_list_command_reports_when_there_are_no_notes_yet(monkeypatch, capsys):
    monkeypatch.setattr(cli, "list_files", lambda gateway: [])
    monkeypatch.setattr(sys, "argv", ["syncnotes-client", "list"])

    cli.main()

    assert "no notes" in capsys.readouterr().out
