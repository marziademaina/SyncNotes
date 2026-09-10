import pytest

import client.api as api


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeStreamResponse:
    def __init__(self, lines, status_code=200):
        self._lines = lines
        self.status_code = status_code

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_lines(self, decode_unicode=True):
        yield from self._lines


def test_list_files_calls_the_expected_url(monkeypatch):
    captured = {}

    def fake_get(url, timeout):
        captured["url"] = url
        return _FakeResponse([{"name": "notes.md", "version": 1, "content_hash": "h1", "updated_at": "2026-01-01"}])

    monkeypatch.setattr(api.requests, "get", fake_get)

    result = api.list_files("http://gateway")

    assert captured["url"] == "http://gateway/files"
    assert result == [{"name": "notes.md", "version": 1, "content_hash": "h1", "updated_at": "2026-01-01"}]


def test_download_file_calls_the_expected_url(monkeypatch):
    captured = {}

    def fake_get(url, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return _FakeResponse({"name": "notes.md", "version": 1, "content": "hello", "content_hash": "h1"})

    monkeypatch.setattr(api.requests, "get", fake_get)

    result = api.download_file("http://gateway", "notes.md")

    assert captured["url"] == "http://gateway/files/notes.md"
    assert result["content"] == "hello"


def test_upload_file_sends_content_and_base_version(monkeypatch):
    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse({"name": "notes.md", "version": 2, "content": "hi", "content_hash": "h2"})

    monkeypatch.setattr(api.requests, "post", fake_post)

    result = api.upload_file("http://gateway", "notes.md", "hi", base_version=1)

    assert captured["url"] == "http://gateway/files/notes.md"
    assert captured["json"]["content"] == "hi"
    assert captured["json"]["base_version"] == 1
    assert captured["json"]["op_id"]  # a content-derived idempotency key
    assert result["version"] == 2


def test_upload_file_defaults_base_version_to_none(monkeypatch):
    captured = {}

    def fake_post(url, json, timeout):
        captured["json"] = json
        return _FakeResponse({"name": "notes.md", "version": 1, "content": "hi", "content_hash": "h1"})

    monkeypatch.setattr(api.requests, "post", fake_post)

    api.upload_file("http://gateway", "notes.md", "hi")

    assert captured["json"]["base_version"] is None


def test_upload_file_uses_a_stable_content_derived_op_id(monkeypatch):
    keys = []

    def fake_post(url, json, timeout):
        keys.append(json["op_id"])
        return _FakeResponse({"name": "notes.md", "version": 1, "content": "x", "content_hash": "h"})

    monkeypatch.setattr(api.requests, "post", fake_post)

    api.upload_file("http://gateway", "notes.md", "same", base_version=2)
    api.upload_file("http://gateway", "notes.md", "same", base_version=2)
    api.upload_file("http://gateway", "notes.md", "different", base_version=2)

    assert keys[0] == keys[1]
    assert keys[0] != keys[2]


def test_delete_file_calls_delete_with_an_op_id(monkeypatch):
    captured = {}

    def fake_delete(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse({"name": "notes.md", "version": 2, "content": "", "content_hash": "h", "deleted": True})

    monkeypatch.setattr(api.requests, "delete", fake_delete)

    result = api.delete_file("http://gateway", "notes.md", version=1)

    assert captured["url"] == "http://gateway/files/notes.md"
    assert captured["json"]["op_id"]
    assert result["deleted"] is True


def test_delete_key_changes_with_the_version(monkeypatch):
    keys = []

    def fake_delete(url, json, timeout):
        keys.append(json["op_id"])
        return _FakeResponse({"name": "notes.md", "version": 2, "content": "", "content_hash": "h", "deleted": True})

    monkeypatch.setattr(api.requests, "delete", fake_delete)

    api.delete_file("http://gateway", "notes.md", version=1)
    api.delete_file("http://gateway", "notes.md", version=1)
    api.delete_file("http://gateway", "notes.md", version=5)

    assert keys[0] == keys[1]
    assert keys[0] != keys[2]


def test_watch_events_yields_each_data_line_and_skips_blanks_and_pings(monkeypatch):
    lines = [
        'data: [{"name": "notes.md", "version": 1}]',
        "",
        ": ping",
        "",
        'data: [{"name": "notes.md", "version": 2}]',
        "",
    ]
    captured = {}

    def fake_get(url, stream, timeout):
        captured["url"] = url
        captured["stream"] = stream
        return _FakeStreamResponse(lines)

    monkeypatch.setattr(api.requests, "get", fake_get)

    events = list(api.watch_events("http://gateway"))

    assert captured["url"] == "http://gateway/events"
    assert captured["stream"] is True
    assert events == [[{"name": "notes.md", "version": 1}], [{"name": "notes.md", "version": 2}]]


def test_watch_events_raises_on_an_error_response(monkeypatch):
    def fake_get(url, stream, timeout):
        return _FakeStreamResponse([], status_code=503)

    monkeypatch.setattr(api.requests, "get", fake_get)

    with pytest.raises(RuntimeError):
        next(api.watch_events("http://gateway"))
