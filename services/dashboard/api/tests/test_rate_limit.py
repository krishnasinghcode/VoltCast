import pytest

from src import config, rate_limit


@pytest.fixture()
def low_limit(monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MINUTE", 5)
    return 5


def test_n_plus_one_request_is_rate_limited(client, viewer_headers, low_limit):
    for _ in range(low_limit):
        resp = client.get("/api/v1/fleets", headers=viewer_headers)
        assert resp.status_code == 200
    resp = client.get("/api/v1/fleets", headers=viewer_headers)
    assert resp.status_code == 429
    body = resp.json()
    assert body["error"]["code"] == "RATE_LIMITED"
    assert int(resp.headers["Retry-After"]) > 0


def test_limit_is_per_endpoint(client, viewer_headers, low_limit):
    for _ in range(low_limit + 1):
        client.get("/api/v1/fleets", headers=viewer_headers)
    assert client.get("/api/v1/fleets", headers=viewer_headers).status_code == 429
    assert client.get("/api/v1/alerts", headers=viewer_headers).status_code == 200


def test_path_params_share_one_bucket(client, viewer_headers, low_limit):
    codes = [client.get(f"/api/v1/vehicles/EV-{i:09d}", headers=viewer_headers).status_code
             for i in range(1, low_limit + 2)]
    assert codes[:low_limit] == [200] * low_limit
    assert codes[-1] == 429


def test_key_follows_contract_pattern(client, viewer_headers, redis_client, low_limit):
    client.get("/api/v1/fleets", headers=viewer_headers)
    keys = redis_client.keys("ratelimit:*")
    assert any(k.endswith(":/api/v1/fleets") for k in keys)
    ttl = redis_client.ttl([k for k in keys if k.endswith(":/api/v1/fleets")][0])
    assert 0 < ttl <= 60


def test_login_is_rate_limited_by_ip(client, low_limit):
    for _ in range(low_limit):
        client.post("/api/v1/auth/login", json={"email": "viewer@cvi.local", "password": "wrong"})
    resp = client.post("/api/v1/auth/login", json={"email": "viewer@cvi.local", "password": "viewer123"})
    assert resp.status_code == 429


def test_fails_open_when_redis_is_down(monkeypatch):
    class Broken:
        def incr(self, *_):
            raise ConnectionError("down")

    monkeypatch.setattr(rate_limit.db, "get_redis", lambda: Broken())
    assert rate_limit.check("u", "/x", limit=1).allowed


def test_health_is_exempt(client, low_limit):
    for _ in range(low_limit + 3):
        assert client.get("/health").status_code == 200
