from .conftest import FLEET_A, vid


def test_search_shape_and_pagination(client, viewer_headers):
    seen, cursor, pages = [], None, 0
    while True:
        params = {"limit": 5}
        if cursor:
            params["after"] = cursor
        resp = client.get("/api/v1/vehicles", params=params, headers=viewer_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body) == {"items", "next_cursor"}
        seen += [v["vehicle_id"] for v in body["items"]]
        pages += 1
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert pages == 3
    assert seen == sorted(seen) == [vid(i) for i in range(1, 13)]  # no gaps, no duplicates


def test_search_item_fields(client, viewer_headers):
    item = client.get("/api/v1/vehicles", params={"search": vid(3)}, headers=viewer_headers).json()["items"][0]
    for field in ("vehicle_id", "fleet_id", "fleet_name", "archetype", "risk_level", "soh_pct", "model_name"):
        assert field in item
    assert item["risk_level"] == "HIGH"          # latest prediction wins over the older LOW one
    assert abs(item["soh_pct"] - (97 - 0.05)) < 1e-6  # latest agg_daily SOH


def test_search_is_substring_and_case_insensitive(client, viewer_headers):
    resp = client.get("/api/v1/vehicles", params={"search": "ev-00000001"}, headers=viewer_headers)
    assert [v["vehicle_id"] for v in resp.json()["items"]] == [vid(10), vid(11), vid(12)]
    resp = client.get("/api/v1/vehicles", params={"search": "%"}, headers=viewer_headers)
    assert resp.json()["items"] == []  # LIKE wildcards are escaped


def test_filters(client, viewer_headers):
    items = client.get("/api/v1/vehicles", params={"fleet_id": FLEET_A, "limit": 50},
                       headers=viewer_headers).json()["items"]
    assert [v["vehicle_id"] for v in items] == [vid(i) for i in range(1, 13, 2)]
    high = client.get("/api/v1/vehicles", params={"risk_level": "high"}, headers=viewer_headers).json()["items"]
    assert [v["vehicle_id"] for v in high] == [vid(3), vid(4), vid(6)]
    bad = client.get("/api/v1/vehicles", params={"archetype": "BAD"}, headers=viewer_headers).json()["items"]
    assert all(v["archetype"] == "BAD" for v in bad) and bad
    resp = client.get("/api/v1/vehicles", params={"archetype": "NOPE"}, headers=viewer_headers)
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_invalid_cursor(client, viewer_headers):
    resp = client.get("/api/v1/vehicles", params={"after": "garbage!!"}, headers=viewer_headers)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_CURSOR"


def test_detail_prefers_redis_state(client, viewer_headers):
    resp = client.get(f"/api/v1/vehicles/{vid(1)}", headers=viewer_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["vehicle"]["chemistry"] == "NMC"
    state = body["current_state"]
    assert state["source"] == "redis"
    assert state["soc_pct"] == 72.4 and state["is_charging"] is True
    assert state["soh_pct"] == 99  # SOH is not in the Redis hash -> ground truth from Postgres
    assert body["active_alert_flags"] == ["HIGH_TEMP"]
    assert body["latest_prediction"]["risk_level"] == "LOW"
    assert body["open_alert_count"] == 2


def test_detail_falls_back_to_postgres(client, viewer_headers):
    body = client.get(f"/api/v1/vehicles/{vid(2)}", headers=viewer_headers).json()
    assert body["current_state"]["source"] == "postgres"
    assert body["current_state"]["soh_pct"] == 98


def test_detail_404(client, viewer_headers):
    resp = client.get("/api/v1/vehicles/EV-DOES-NOT-EXIST", headers=viewer_headers)
    assert resp.status_code == 404 and resp.json()["error"]["code"] == "NOT_FOUND"


def test_history_newest_first_with_cursor(client, viewer_headers):
    url = f"/api/v1/vehicles/{vid(1)}/history"
    page1 = client.get(url, params={"resolution": "daily", "limit": 3}, headers=viewer_headers).json()
    assert [r["date"] for r in page1["items"]] == ["2026-03-05", "2026-03-04", "2026-03-03"]
    page2 = client.get(url, params={"resolution": "daily", "limit": 3, "after": page1["next_cursor"]},
                       headers=viewer_headers).json()
    assert [r["date"] for r in page2["items"]] == ["2026-03-02", "2026-03-01"]
    assert page2["next_cursor"] is None
    ranged = client.get(url, params={"from": "2026-03-02", "to": "2026-03-03"}, headers=viewer_headers).json()
    assert [r["date"] for r in ranged["items"]] == ["2026-03-03", "2026-03-02"]
    assert client.get(url, params={"resolution": "weekly"}, headers=viewer_headers).status_code == 422
    assert client.get(url, params={"from": "yesterday"}, headers=viewer_headers).status_code == 400
    empty = client.get(url, params={"resolution": "hourly"}, headers=viewer_headers).json()
    assert empty["items"] == []


def test_predictions(client, viewer_headers):
    body = client.get(f"/api/v1/vehicles/{vid(3)}/predictions", headers=viewer_headers).json()
    assert [p["model_version"] for p in body["items"]] == ["v1", "v0"]
    alias = client.get(f"/api/v1/predictions/{vid(3)}", params={"limit": 1}, headers=viewer_headers).json()
    assert len(alias["items"]) == 1 and alias["items"][0]["risk_level"] == "HIGH"
    none = client.get(f"/api/v1/vehicles/{vid(9)}/predictions", headers=viewer_headers).json()
    assert none["items"] == []
