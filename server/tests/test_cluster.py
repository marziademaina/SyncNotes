from server.cluster import leader_http_url


class _FakeStore:
    def __init__(self, leader):
        self._leader = leader

    def getStatus(self):
        return {"leader": self._leader}


def test_leader_http_url_returns_none_when_no_leader_elected():
    assert leader_http_url(_FakeStore(None), "server-1:9000", "8000") is None


def test_leader_http_url_returns_none_when_self_is_leader():
    assert leader_http_url(_FakeStore("server-1:9000"), "server-1:9000", "8000") is None


def test_leader_http_url_maps_raft_host_to_http_port():
    assert leader_http_url(_FakeStore("server-2:9000"), "server-1:9000", "8000") == "http://server-2:8000"
