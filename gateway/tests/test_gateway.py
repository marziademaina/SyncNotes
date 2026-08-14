import httpx
from fastapi.testclient import TestClient


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
