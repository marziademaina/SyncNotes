import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient


def _parse_event(chunk: str) -> list | None:
    # Every chunk from the /events generator is one complete SSE message:
    # either "data: <json>\n\n" or a ": ping\n\n" heartbeat comment.
    if not chunk.startswith("data: "):
        return None
    return json.loads(chunk[len("data: ") :])


def _file_response(version: int, content: str, content_hash: str) -> dict:
    return {
        "name": "notes.md",
        "version": version,
        "content": content,
        "content_hash": content_hash,
        "updated_at": "2026-01-01T00:00:00Z",
    }


def test_health_discovers_the_current_leader(gateway_app, mock_async_client):
    main_module = gateway_app("http://server-1:8000,http://server-2:8000,http://server-3:8000")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(200, json={"status": "ok", "leader": "server-2:9000"})

    mock_async_client(handler)

    with TestClient(main_module.app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["leader"] == "http://server-2:8000"
    assert body["replicas"] == ["http://server-1:8000", "http://server-2:8000", "http://server-3:8000"]


def test_download_is_routed_to_the_leader(gateway_app, mock_async_client):
    main_module = gateway_app("http://server-1:8000,http://server-2:8000,http://server-3:8000")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "leader": "server-2:9000"})
        assert request.url.host == "server-2"
        return httpx.Response(200, json=_file_response(1, "hello", "h1"))

    mock_async_client(handler)

    with TestClient(main_module.app) as client:
        response = client.get("/files/notes.md")

    assert response.status_code == 200
    assert response.json()["content"] == "hello"


def test_list_notes_is_routed_to_the_leader(gateway_app, mock_async_client):
    main_module = gateway_app("http://server-1:8000,http://server-2:8000,http://server-3:8000")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "leader": "server-2:9000"})
        assert request.url.path == "/files"
        assert request.url.host == "server-2"
        return httpx.Response(200, json=[{"name": "notes.md", "version": 1, "content_hash": "h1", "updated_at": "2026-01-01T00:00:00Z"}])

    mock_async_client(handler)

    with TestClient(main_module.app) as client:
        response = client.get("/files")

    assert response.status_code == 200
    body = response.json()
    assert body == [{"name": "notes.md", "version": 1, "content_hash": "h1", "updated_at": "2026-01-01T00:00:00Z"}]


def test_upload_is_routed_to_the_leader(gateway_app, mock_async_client):
    main_module = gateway_app("http://server-1:8000,http://server-2:8000,http://server-3:8000")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "leader": "server-3:9000"})
        assert request.url.host == "server-3"
        return httpx.Response(200, json=_file_response(2, "new content", "h2"))

    mock_async_client(handler)

    with TestClient(main_module.app) as client:
        response = client.post("/files/notes.md", json={"content": "new content"})

    assert response.status_code == 200
    assert response.json()["version"] == 2


def test_delete_is_routed_to_the_leader(gateway_app, mock_async_client):
    main_module = gateway_app("http://server-1:8000,http://server-2:8000,http://server-3:8000")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "leader": "server-3:9000"})
        assert request.method == "DELETE"
        assert request.url.host == "server-3"
        return httpx.Response(200, json={**_file_response(2, "hello", "h1"), "deleted": True})

    mock_async_client(handler)

    with TestClient(main_module.app) as client:
        response = client.delete("/files/notes.md")

    assert response.status_code == 200
    assert response.json()["deleted"] is True


def test_download_of_a_tombstoned_note_is_a_404(gateway_app, mock_async_client):
    main_module = gateway_app("http://server-1:8000")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "leader": "server-1:9000"})
        return httpx.Response(200, json={**_file_response(2, "hello", "h1"), "deleted": True})

    mock_async_client(handler)

    with TestClient(main_module.app) as client:
        response = client.get("/files/notes.md")

    assert response.status_code == 404


def test_upload_logs_the_note_name_and_new_version(gateway_app, mock_async_client, caplog):
    main_module = gateway_app("http://server-1:8000")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "leader": "server-1:9000"})
        return httpx.Response(200, json=_file_response(2, "new content", "h2"))

    mock_async_client(handler)

    with TestClient(main_module.app) as client, caplog.at_level("INFO"):
        client.post("/files/notes.md", json={"content": "new content"})

    assert any("notes.md" in r.message and "2" in r.message for r in caplog.records)


def test_returns_503_when_no_leader_is_discoverable(gateway_app, mock_async_client):
    main_module = gateway_app("http://server-1:8000,http://server-2:8000")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(200, json={"status": "ok", "leader": None})

    mock_async_client(handler)

    with TestClient(main_module.app) as client:
        response = client.get("/files/notes.md")

    assert response.status_code == 503


def test_upload_reroutes_after_a_stale_leader_rejects_the_write(gateway_app, mock_async_client):
    main_module = gateway_app("http://server-1:8000,http://server-2:8000")
    state = {"server_1_rejected": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            leader = "server-2:9000" if state["server_1_rejected"] else "server-1:9000"
            return httpx.Response(200, json={"status": "ok", "leader": leader})
        if request.url.host == "server-1":
            state["server_1_rejected"] = True
            return httpx.Response(503, json={"detail": "replication failed: NOT_LEADER"})
        assert request.url.host == "server-2"
        return httpx.Response(200, json=_file_response(3, "reelected", "h3"))

    mock_async_client(handler)

    with TestClient(main_module.app) as client:
        response = client.post("/files/notes.md", json={"content": "reelected"})

    assert response.status_code == 200
    assert response.json()["content"] == "reelected"


def test_upload_reroutes_when_the_leader_is_unreachable(gateway_app, mock_async_client):
    main_module = gateway_app("http://server-1:8000,http://server-2:8000")
    state = {"server_1_down": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            leader = "server-2:9000" if state["server_1_down"] else "server-1:9000"
            return httpx.Response(200, json={"status": "ok", "leader": leader})
        if request.url.host == "server-1":
            state["server_1_down"] = True
            raise httpx.ConnectError("connection refused", request=request)
        assert request.url.host == "server-2"
        return httpx.Response(200, json=_file_response(4, "failed over", "h4"))

    mock_async_client(handler)

    with TestClient(main_module.app) as client:
        response = client.post("/files/notes.md", json={"content": "failed over"})

    assert response.status_code == 200
    assert response.json()["content"] == "failed over"


def _note(version: int) -> dict:
    return {"name": "notes.md", "version": version, "content_hash": f"h{version}", "updated_at": "2026-01-01T00:00:00Z"}


# The installed TestClient doesn't support true incremental streaming (it
# drains a request to completion before returning it), which is fatal for an
# endpoint whose generator never ends. So these tests drive the /events
# async generator directly instead of going through TestClient/HTTP.


@pytest.mark.asyncio
async def test_events_sends_the_current_list_on_connect(gateway_app, mock_async_client, monkeypatch):
    monkeypatch.setenv("EVENTS_POLL_INTERVAL_SECONDS", "100")
    main_module = gateway_app("http://server-1:8000")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "leader": "server-1:9000"})
        return httpx.Response(200, json=[_note(1)])

    mock_async_client(handler)

    response = await main_module.events()
    chunk = await asyncio.wait_for(anext(response.body_iterator), timeout=1)

    assert _parse_event(chunk) == [_note(1)]


@pytest.mark.asyncio
async def test_events_logs_connect_and_disconnect_with_subscriber_count(gateway_app, mock_async_client, monkeypatch, caplog):
    monkeypatch.setenv("EVENTS_POLL_INTERVAL_SECONDS", "100")
    main_module = gateway_app("http://server-1:8000")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "leader": "server-1:9000"})
        return httpx.Response(200, json=[_note(1)])

    mock_async_client(handler)

    with caplog.at_level("INFO"):
        response = await main_module.events()
        await asyncio.wait_for(anext(response.body_iterator), timeout=1)
        await response.body_iterator.aclose()

    messages = [r.message for r in caplog.records]
    assert any("connected" in m and "1 subscriber" in m for m in messages)
    assert any("disconnected" in m and "0 subscriber" in m for m in messages)


@pytest.mark.asyncio
async def test_events_broadcasts_a_change_detected_by_polling(gateway_app, mock_async_client, monkeypatch):
    monkeypatch.setenv("EVENTS_POLL_INTERVAL_SECONDS", "0.05")
    main_module = gateway_app("http://server-1:8000")
    calls = {"files": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "leader": "server-1:9000"})
        calls["files"] += 1
        return httpx.Response(200, json=[_note(1 if calls["files"] <= 1 else 2)])

    mock_async_client(handler)

    response = await main_module.events()
    first = await asyncio.wait_for(anext(response.body_iterator), timeout=1)
    second = await asyncio.wait_for(anext(response.body_iterator), timeout=1)

    assert _parse_event(first) == [_note(1)]
    assert _parse_event(second) == [_note(2)]


@pytest.mark.asyncio
async def test_events_keeps_polling_for_a_subscriber_that_reconnects(gateway_app, mock_async_client, monkeypatch):
    # Regression test for a stop/start race: the poll loop tears itself down
    # once the last subscriber leaves, and must not leave polling dead if a
    # new subscriber arrives while that teardown is still in flight.
    monkeypatch.setenv("EVENTS_POLL_INTERVAL_SECONDS", "0.05")
    main_module = gateway_app("http://server-1:8000")
    calls = {"files": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "leader": "server-1:9000"})
        calls["files"] += 1
        return httpx.Response(200, json=[_note(1 if calls["files"] <= 1 else 2)])

    mock_async_client(handler)

    first_response = await main_module.events()
    await asyncio.wait_for(anext(first_response.body_iterator), timeout=1)
    await first_response.body_iterator.aclose()  # the only subscriber disconnects

    # The leader's content may have already moved on to version 2 by the
    # time we reconnect (a poll tick could land in the gap) - what matters
    # here isn't the exact sequence, only that polling is still alive at all.
    second_response = await main_module.events()
    seen_versions = set()
    for _ in range(10):
        chunk = await asyncio.wait_for(anext(second_response.body_iterator), timeout=1)
        event = _parse_event(chunk)
        if event is not None:
            seen_versions.add(event[0]["version"])
        if 2 in seen_versions:
            break

    assert 2 in seen_versions


@pytest.mark.asyncio
async def test_upload_broadcasts_immediately_without_waiting_for_the_poll(gateway_app, mock_async_client, monkeypatch):
    # A very long poll interval means only the post-upload fast path can
    # possibly deliver the second event within the test's lifetime.
    monkeypatch.setenv("EVENTS_POLL_INTERVAL_SECONDS", "100")
    main_module = gateway_app("http://server-1:8000")
    calls = {"files": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "leader": "server-1:9000"})
        if request.method == "GET":
            calls["files"] += 1
            return httpx.Response(200, json=[_note(1 if calls["files"] <= 1 else 2)])
        return httpx.Response(200, json=_file_response(2, "new content", "h2"))

    mock_async_client(handler)

    response = await main_module.events()
    first = await asyncio.wait_for(anext(response.body_iterator), timeout=1)
    assert _parse_event(first) == [_note(1)]

    result = await main_module.upload("notes.md", {"content": "new content", "base_version": None})
    assert result["version"] == 2

    second = await asyncio.wait_for(anext(response.body_iterator), timeout=1)
    assert _parse_event(second) == [_note(2)]


@pytest.mark.asyncio
async def test_events_sends_a_heartbeat_when_idle(gateway_app, mock_async_client, monkeypatch):
    monkeypatch.setenv("EVENTS_POLL_INTERVAL_SECONDS", "100")
    monkeypatch.setenv("EVENTS_HEARTBEAT_SECONDS", "0.05")
    main_module = gateway_app("http://server-1:8000")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "leader": "server-1:9000"})
        return httpx.Response(200, json=[_note(1)])

    mock_async_client(handler)

    response = await main_module.events()
    first = await asyncio.wait_for(anext(response.body_iterator), timeout=1)
    assert _parse_event(first) == [_note(1)]

    heartbeat = await asyncio.wait_for(anext(response.body_iterator), timeout=1)
    assert heartbeat == ": ping\n\n"
