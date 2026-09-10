import importlib

import pytest
from fastapi.testclient import TestClient

import server.main as main_module


def test_raft_conf_disables_dns_caching_so_recreated_peers_can_rejoin():
    conf = main_module._raft_conf()
    assert conf.dnsCacheTime == 0
    assert conf.onStateChanged is main_module._on_raft_state_changed


def test_on_raft_state_changed_logs_the_transition(caplog):
    with caplog.at_level("INFO", logger="server.main"):
        main_module._on_raft_state_changed(1, 2)
    assert any("CANDIDATE -> LEADER" in r.message for r in caplog.records)


def test_peers_connected_counts_only_connected_partners():
    status = {
        "partner_nodes_count": 2,
        "partner_node_status_server_server-2:9000": 2,
        "partner_node_status_server_server-3:9000": 0,
    }
    assert main_module._peers_connected(status) == 1


def test_cluster_summary_extracts_state_leader_quorum_and_peers():
    status = {
        "state": 2,
        "leader": "server-1:9000",
        "has_quorum": True,
        "partner_nodes_count": 2,
        "partner_node_status_server_server-2:9000": 2,
        "partner_node_status_server_server-3:9000": 0,
    }
    assert main_module._cluster_summary(status) == ("LEADER", "server-1:9000", True, 1, 2)


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


def test_a_repeated_upload_with_the_same_op_id_is_not_reapplied(client):
    first = client.post("/files/notes.md", json={"content": "hello", "op_id": "op-1"})
    assert first.json()["version"] == 1

    again = client.post("/files/notes.md", json={"content": "changed", "op_id": "op-1"})
    assert again.status_code == 200
    assert again.json()["version"] == 1
    assert again.json()["content"] == "hello"


def test_list_notes_is_empty_before_any_upload(client):
    response = client.get("/files")
    assert response.status_code == 200
    assert response.json() == []


def test_list_notes_reflects_uploaded_files(client):
    client.post("/files/notes.md", json={"content": "hello"})

    response = client.get("/files")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "notes.md"
    assert body[0]["version"] == 1
    assert "content" not in body[0]


def test_manifest_reflects_local_files(client):
    client.post("/files/notes.md", json={"content": "hello"})
    response = client.get("/internal/manifest")
    assert response.status_code == 200
    body = response.json()
    assert body["notes.md"]["version"] == 1


def test_second_upload_without_base_version_is_conservative_and_keeps_server_content(client):
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

    client_b = client.post(
        "/files/notes.md", json={"content": "line1\nline2\nB-edit\n", "base_version": base_version}
    )

    assert client_b.status_code == 200
    assert client_b.json()["content"] == "line1\nA-edit\nB-edit\n"
    assert client_b.json()["had_conflict"] is False


def test_two_clients_editing_the_same_line_reports_the_conflict(client):
    base = client.post("/files/notes.md", json={"content": "line1\nline2\nline3\n"})
    base_version = base.json()["version"]

    client_a = client.post(
        "/files/notes.md", json={"content": "line1\nA-edit\nline3\n", "base_version": base_version}
    )
    assert client_a.json()["had_conflict"] is False

    client_b = client.post(
        "/files/notes.md", json={"content": "line1\nB-edit\nline3\n", "base_version": base_version}
    )

    assert client_b.status_code == 200
    assert client_b.json()["content"] == "line1\nA-edit\nline3\n"  # the server's edit wins
    assert client_b.json()["had_conflict"] is True


def test_delete_hides_the_note_from_list_and_download(client):
    client.post("/files/notes.md", json={"content": "hello"})

    deleted = client.delete("/files/notes.md")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert deleted.json()["version"] == 2

    assert client.get("/files").json() == []
    tombstone = client.get("/files/notes.md")
    assert tombstone.status_code == 200
    assert tombstone.json()["deleted"] is True


def test_deleting_twice_does_not_bump_the_version_again(client):
    client.post("/files/notes.md", json={"content": "hello"})
    client.delete("/files/notes.md")
    again = client.delete("/files/notes.md")
    assert again.json()["version"] == 2


def test_delete_then_recreate_brings_the_note_back(client):
    client.post("/files/notes.md", json={"content": "original"})
    client.delete("/files/notes.md")

    recreated = client.post("/files/notes.md", json={"content": "fresh start"})
    assert recreated.status_code == 200
    assert recreated.json()["content"] == "fresh start"

    listing = client.get("/files").json()
    assert [n["name"] for n in listing] == ["notes.md"]


def test_deleting_an_unknown_note_succeeds(client):
    response = client.delete("/files/ghost.md")
    assert response.status_code == 200
    assert response.json()["deleted"] is True


def test_manifest_carries_the_tombstone(client):
    client.post("/files/notes.md", json={"content": "hello"})
    client.delete("/files/notes.md")

    manifest = client.get("/internal/manifest").json()
    assert manifest["notes.md"]["deleted"] is True


def test_upload_logs_the_new_version(client, caplog):
    with caplog.at_level("INFO"):
        response = client.post("/files/notes.md", json={"content": "hello"})

    version = response.json()["version"]
    assert any("notes.md" in r.message and str(version) in r.message for r in caplog.records)


def test_a_same_line_conflict_logs_a_warning(client, caplog):
    base = client.post("/files/notes.md", json={"content": "line1\nline2\nline3\n"})
    base_version = base.json()["version"]
    client.post("/files/notes.md", json={"content": "line1\nA-edit\nline3\n", "base_version": base_version})

    with caplog.at_level("WARNING"):
        client.post("/files/notes.md", json={"content": "line1\nB-edit\nline3\n", "base_version": base_version})

    assert any(r.levelname == "WARNING" and "notes.md" in r.message for r in caplog.records)
