import importlib

import pytest
from fastapi.testclient import TestClient


def _build_client(tmp_path, monkeypatch, free_port, replica_id="test-server"):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("REPLICA_ID", replica_id)
    monkeypatch.setenv("RAFT_SELF_ADDR", f"127.0.0.1:{free_port()}")
    monkeypatch.setenv("RAFT_PEER_ADDRS", "")
    monkeypatch.setenv("RAFT_JOURNAL_FILE", str(tmp_path / "raft.journal"))
    monkeypatch.setenv("RAFT_DUMP_FILE", str(tmp_path / "raft.dump"))

    import server.cluster as cluster_module
    import server.db as db_module
    import server.main as main_module

    importlib.reload(db_module)
    importlib.reload(cluster_module)
    importlib.reload(main_module)

    return TestClient(main_module.app)


@pytest.fixture()
def client(tmp_path, monkeypatch, free_port, wait_for_leader):
    with _build_client(tmp_path, monkeypatch, free_port) as test_client:
        import server.main as main_module

        wait_for_leader(main_module.store)
        yield test_client


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["has_quorum"] is True


def test_download_missing_file_returns_404(client):
    response = client.get("/files/notes.md")
    assert response.status_code == 404


def test_upload_then_download_roundtrip(client):
    upload = client.post("/files/notes.md", json={"content": "hello"})
    assert upload.status_code == 200
    body = upload.json()
    assert body["version"] == 1
    assert body["content"] == "hello"

    download = client.get("/files/notes.md")
    assert download.status_code == 200
    assert download.json()["content"] == "hello"


def test_second_upload_increments_version(client):
    client.post("/files/notes.md", json={"content": "v1"})
    second = client.post("/files/notes.md", json={"content": "v2"})
    assert second.json()["version"] == 2


def test_manifest_reflects_local_files(client):
    client.post("/files/notes.md", json={"content": "hello"})
    response = client.get("/internal/manifest")
    assert response.status_code == 200
    body = response.json()
    assert body["notes.md"]["version"] == 1


def test_second_upload_without_base_version_is_conservative_and_keeps_server_content(client):
    # No base_version means the server can't tell which part of the upload is
    # a genuine edit vs. a stale copy, so the whole upload is treated as one
    # conflict and the existing authoritative content wins.
    client.post("/files/notes.md", json={"content": "v1"})
    second = client.post("/files/notes.md", json={"content": "v2"})
    assert second.status_code == 200
    assert second.json()["content"] == "v1"


def test_upload_with_correct_base_version_applies_cleanly(client):
    first = client.post("/files/notes.md", json={"content": "line1\nline2\nline3\n"})
    base_version = first.json()["version"]

    second = client.post(
        "/files/notes.md", json={"content": "line1\nline2-edited\nline3\n", "base_version": base_version}
    )

    assert second.status_code == 200
    assert second.json()["content"] == "line1\nline2-edited\nline3\n"


def test_two_clients_editing_disjoint_lines_from_the_same_base_both_survive(client):
    base = client.post("/files/notes.md", json={"content": "line1\nline2\nline3\n"})
    base_version = base.json()["version"]

    client_a = client.post(
        "/files/notes.md", json={"content": "line1\nA-edit\nline3\n", "base_version": base_version}
    )
    assert client_a.json()["content"] == "line1\nA-edit\nline3\n"

    # client_b started from the same base_version, unaware that client_a's
    # write already landed and changed line2.
    client_b = client.post(
        "/files/notes.md", json={"content": "line1\nline2\nB-edit\n", "base_version": base_version}
    )

    assert client_b.status_code == 200
    assert client_b.json()["content"] == "line1\nA-edit\nB-edit\n"
