import importlib

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
def gateway_app(monkeypatch):
    def _build(replica_urls: str):
        monkeypatch.setenv("REPLICA_URLS", replica_urls)

        import gateway.main as main_module

        importlib.reload(main_module)
        return main_module

    return _build
