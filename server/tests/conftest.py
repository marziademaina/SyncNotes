import importlib
import socket
import time

import httpx
import pytest


@pytest.fixture()
def mock_async_client(monkeypatch):
    def _apply(handler):
        real_async_client = httpx.AsyncClient

        def fake_async_client(**kwargs):
            kwargs.pop("transport", None)
            return real_async_client(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", fake_async_client)

    return _apply


@pytest.fixture()
def local_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))

    import server.db as db_module

    importlib.reload(db_module)
    db_module.init_db()
    return db_module


@pytest.fixture()
def free_port():
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    return _free_port


@pytest.fixture()
def wait_for_leader():
    def _wait(store, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = store.getStatus()
            if status.get("has_quorum") and status.get("leader"):
                return
            time.sleep(0.05)
        raise TimeoutError("no leader elected within timeout")

    return _wait
