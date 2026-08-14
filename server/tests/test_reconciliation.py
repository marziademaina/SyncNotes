import hashlib

import httpx
import pytest

from server.reconciliation import reconcile_with_peer


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _file_response(version: int, content: str, content_hash: str) -> dict:
    return {
        "name": "notes.md",
        "version": version,
        "content": content,
        "content_hash": content_hash,
        "updated_at": "2026-01-01T00:00:00Z",
    }


@pytest.mark.asyncio
async def test_reconcile_pulls_missing_file(local_db, mock_async_client):
    h1 = _hash("hello")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/internal/manifest":
            return httpx.Response(200, json={"notes.md": {"version": 1, "content_hash": h1}})
        return httpx.Response(200, json=_file_response(1, "hello", h1))

    mock_async_client(handler)

    fixed = await reconcile_with_peer("http://primary:8000")

    assert fixed == ["notes.md"]
    assert local_db.list_files() == {"notes.md": {"version": 1, "content_hash": h1}}


@pytest.mark.asyncio
async def test_reconcile_pulls_stale_file(local_db, mock_async_client):
    local_db.apply_if_newer("notes.md", 1, "old", _hash("old"))
    h2 = _hash("new")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/internal/manifest":
            return httpx.Response(200, json={"notes.md": {"version": 2, "content_hash": h2}})
        return httpx.Response(200, json=_file_response(2, "new", h2))

    mock_async_client(handler)

    fixed = await reconcile_with_peer("http://primary:8000")

    assert fixed == ["notes.md"]
    assert local_db.list_files()["notes.md"] == {"version": 2, "content_hash": h2}


@pytest.mark.asyncio
async def test_reconcile_repairs_corrupted_file_at_same_version(local_db, mock_async_client):
    local_db.apply_if_newer("notes.md", 1, "corrupted", _hash("corrupted"))
    good_hash = _hash("correct")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/internal/manifest":
            return httpx.Response(200, json={"notes.md": {"version": 1, "content_hash": good_hash}})
        return httpx.Response(200, json=_file_response(1, "correct", good_hash))

    mock_async_client(handler)

    fixed = await reconcile_with_peer("http://primary:8000")

    assert fixed == ["notes.md"]
    assert local_db.list_files()["notes.md"]["content_hash"] == good_hash


@pytest.mark.asyncio
async def test_reconcile_repairs_local_bit_rot_even_when_hash_column_still_matches_peer(
    local_db, mock_async_client
):
    h1 = _hash("hello")
    local_db.apply_if_newer("notes.md", 1, "hello", h1)
    session = local_db.get_session()
    record = session.get(local_db.FileRecord, "notes.md")
    record.content = "corrupted in place"
    session.commit()
    session.close()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/internal/manifest":
            return httpx.Response(200, json={"notes.md": {"version": 1, "content_hash": h1}})
        return httpx.Response(200, json=_file_response(1, "hello", h1))

    mock_async_client(handler)

    fixed = await reconcile_with_peer("http://primary:8000")

    assert fixed == ["notes.md"]
    session = local_db.get_session()
    record = session.get(local_db.FileRecord, "notes.md")
    assert record.content == "hello"
    session.close()


@pytest.mark.asyncio
async def test_reconcile_is_noop_when_already_up_to_date(local_db, mock_async_client):
    h3 = _hash("current")
    local_db.apply_if_newer("notes.md", 3, "current", h3)
    requested_paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        return httpx.Response(200, json={"notes.md": {"version": 3, "content_hash": h3}})

    mock_async_client(handler)

    fixed = await reconcile_with_peer("http://primary:8000")

    assert fixed == []
    assert requested_paths == ["/internal/manifest"]
