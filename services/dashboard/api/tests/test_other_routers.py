"""Alerts, risk lists, fleets, observability, proxies (upstream down), SQL sandbox guardrails, WebSocket."""
import pytest
from starlette.websockets import WebSocketDisconnect

from src.errors import ApiError
from src.routers.sql_sandbox_router import prepare_query

from .conftest import FLEET_A, FLEET_B, login, vid

ALERT_1 = "aaaaaaaa-0000-0000-0000-000000000001"


def test_alerts_keyset_and_filters(client, viewer_headers):
    all_items, cursor = [], None
    while True:
        params = {"limit": 2, **({"after": cursor} if cursor else {})}
        body = client.get("/api/v1/alerts", params=params, headers=viewer_headers).json()
        all_items += body["items"]
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert len(all_items) == 4 and len({a["alert_id"] for a in all_items}) == 4
    assert [a["created_at"] for a in all_items] == sorted((a["created_at"] for a in all_items), reverse=True)

    open_ = client.get("/api/v1/alerts", params={"status": "open"}, headers=viewer_headers).json()["items"]
    assert len(open_) == 3
    crit = client.get("/api/v1/alerts", params={"severity": "CRITICAL"}, headers=viewer_headers).json()["items"]
    assert [a["vehicle_id"] for a in crit] == [vid(3)]
    mine = client.get("/api/v1/alerts", params={"vehicle_id": vid(1)}, headers=viewer_headers).json()["items"]
    assert len(mine) == 2
    fleet_b = client.get("/api/v1/alerts", params={"fleet_id": FLEET_B}, headers=viewer_headers).json()["items"]
    assert [a["vehicle_id"] for a in fleet_b] == [vid(4)]
    assert client.get("/api/v1/alerts", params={"status": "BOGUS"}, headers=viewer_headers).status_code == 400


def test_acknowledge_flow(client, manager_headers):
    url = f"/api/v1/alerts/{ALERT_1}/acknowledge"
    assert client.post(url, headers=manager_headers).json()["status"] == "ACKNOWLEDGED"
    again = client.post(url, headers=manager_headers)
    assert again.status_code == 409 and again.json()["error"]["code"] == "INVALID_STATE"
    missing = client.post("/api/v1/alerts/aaaaaaaa-0000-0000-0000-00000000ffff/acknowledge", headers=manager_headers)
    assert missing.status_code == 404
    assert client.post("/api/v1/alerts/not-a-uuid/acknowledge", headers=manager_headers).status_code == 400


def test_risk_lists_use_latest_prediction(client, viewer_headers):
    high = client.get("/api/v1/risk/high", headers=viewer_headers).json()
    # worst degradation first
    assert [r["vehicle_id"] for r in high["items"]] == [vid(6), vid(4), vid(3)]
    low = client.get("/api/v1/risk/low", headers=viewer_headers).json()
    assert [r["vehicle_id"] for r in low["items"]] == [vid(2), vid(1)]
    page1 = client.get("/api/v1/risk/high", params={"limit": 2}, headers=viewer_headers).json()
    page2 = client.get("/api/v1/risk/high", params={"limit": 2, "after": page1["next_cursor"]},
                       headers=viewer_headers).json()
    assert [r["vehicle_id"] for r in page1["items"] + page2["items"]] == [vid(6), vid(4), vid(3)]
    fleet = client.get("/api/v1/risk/high", params={"fleet_id": FLEET_A}, headers=viewer_headers).json()
    assert [r["vehicle_id"] for r in fleet["items"]] == [vid(3)]


def test_fleets_and_summary_cache(client, viewer_headers, redis_client):
    fleets = client.get("/api/v1/fleets", headers=viewer_headers).json()
    assert [f["vehicle_count"] for f in fleets["items"]] == [6, 6]
    s = client.get(f"/api/v1/fleets/{FLEET_A}/summary", headers=viewer_headers).json()
    assert s["vehicle_count"] == 6 and s["fleet_name"] == "Alpha Logistics"
    assert s["risk_breakdown"] == {"LOW": 1, "MEDIUM": 1, "HIGH": 1, "UNPREDICTED": 3}
    assert s["active_alerts"] == 3 and s["critical_alerts"] == 1
    assert s["cached"] is False
    assert redis_client.ttl(f"cache:fleet_summary:{FLEET_A}") <= 10
    assert client.get(f"/api/v1/fleets/{FLEET_A}/summary", headers=viewer_headers).json()["cached"] is True
    overall = client.get("/api/v1/fleets/summary", headers=viewer_headers).json()
    assert overall["vehicle_count"] == 12
    assert redis_client.get("cache:fleet_summary") is not None
    missing = client.get("/api/v1/fleets/33333333-3333-3333-3333-333333333333/summary", headers=viewer_headers)
    assert missing.status_code == 404


def test_observability_shape_when_peers_down(client, viewer_headers):
    client.get("/api/v1/fleets", headers=viewer_headers)
    body = client.get("/api/v1/observability", headers=viewer_headers).json()
    assert set(body["ingestion"]) >= {"events_consumed_total", "events_deduplicated_total",
                                      "events_rejected_total", "consumer_lag"}
    assert body["ingestion"]["reachable"] is False
    assert set(body["api"]) >= {"requests_total", "errors_total", "avg_latency_ms", "p95_latency_ms"}
    assert body["api"]["requests_total"] > 0
    assert body["fleet"] == {"vehicle_count": 12, "active_alerts": 3}
    names = {s["name"]: s for s in body["services"]}
    assert names["data-gen"]["status"] == "down" and names["redis"]["status"] == "ok"


def test_proxies_report_upstream_unavailable(client, manager_headers):
    resp = client.post("/api/v1/simulation/abc/start", headers=manager_headers)
    assert resp.status_code == 502 and resp.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"
    resp = client.post("/api/v1/ml/predict", headers=manager_headers)
    assert resp.status_code == 502
    assert client.post("/api/v1/simulation/abc/explode", headers=manager_headers).status_code == 422


@pytest.mark.parametrize("query", [
    "DELETE FROM alerts",
    "SELECT 1; DROP TABLE users",
    "UPDATE alerts SET status = 'OPEN'",
    "WITH d AS (DELETE FROM alerts RETURNING *) SELECT * FROM d",
    "SELECT * INTO copy_of_users FROM users",
    "SELECT 1 /* ; */ ; SELECT 2",
    "WITH a AS (SELECT '/*'), d AS (DELETE FROM alerts RETURNING 1) SELECT '*/'",
    "SELECT $$x$$",
    "EXPLAIN ANALYZE SELECT 1",
    "",
])
def test_sandbox_rejects(query):
    with pytest.raises(ApiError) as exc:
        prepare_query(query)
    assert exc.value.status_code == 400


def test_sandbox_accepts_and_limits():
    sql, added = prepare_query("select alert_type from alerts where alert_type = 'UPDATE';")
    assert added and sql.endswith("LIMIT 500") and "alert_type = 'UPDATE'" in sql
    sql, added = prepare_query("WITH x AS (SELECT 1 AS n) SELECT n FROM x LIMIT 5")
    assert not added and sql.startswith("WITH")
    sql, added = prepare_query("SELECT 1 -- trailing comment")
    assert added and "\n) AS sandbox_q LIMIT 500" in sql


def test_sandbox_rbac(client, viewer_headers):
    assert client.get("/api/v1/sql-sandbox", headers=viewer_headers).json()["examples"]
    resp = client.post("/api/v1/sql-sandbox", json={"query": "SELECT 1"}, headers=viewer_headers)
    assert resp.status_code == 403


def test_websocket_requires_token(client):
    with client.websocket_connect("/ws/live?token=bad") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "error"
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4401


def test_websocket_pushes_snapshot_and_vehicle_states(client):
    token = login(client, "viewer@cvi.local")
    with client.websocket_connect(f"/ws/live?token={token}") as ws:
        first = ws.receive_json()
        assert first["type"] == "live"
        assert first["fleet_summary"]["vehicle_count"] == 12
        assert len(first["recent_alerts"]) == 4
        ws.send_json({"action": "subscribe", "vehicle_ids": [vid(1)]})
        msg = ws.receive_json()
        while msg["type"] != "vehicle_states":
            msg = ws.receive_json()
        assert msg["vehicle_states"][vid(1)]["soc_pct"] == 72.4
        assert msg["vehicle_states"][vid(1)]["alert_flags"] == ["HIGH_TEMP"]
        pushed = ws.receive_json()  # next periodic push carries the subscription too
        while pushed["type"] != "live":
            pushed = ws.receive_json()
        assert vid(1) in pushed["vehicle_states"]
