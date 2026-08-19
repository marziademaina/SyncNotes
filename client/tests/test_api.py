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
    assert captured["json"] == {"content": "hi", "base_version": 1}
    assert result["version"] == 2


def test_upload_file_defaults_base_version_to_none(monkeypatch):
    captured = {}

    def fake_post(url, json, timeout):
        captured["json"] = json
        return _FakeResponse({"name": "notes.md", "version": 1, "content": "hi", "content_hash": "h1"})

    monkeypatch.setattr(api.requests, "post", fake_post)

    api.upload_file("http://gateway", "notes.md", "hi")

    assert captured["json"]["base_version"] is None
