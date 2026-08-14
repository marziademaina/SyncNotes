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
