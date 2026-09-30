from datetime import datetime, timedelta, timezone

from jose import jwt

from src import config

from .conftest import login


def _assert_error(resp, status, code):
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert body["error"]["code"] == code
    assert "message" in body["error"] and "details" in body["error"]


def test_health_is_public_and_reports_contract_version(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["contract_version"] == "1.0.0"


def test_login_succeeds_with_seeded_user(client):
    resp = client.post("/api/v1/auth/login", json={"email": "viewer@cvi.local", "password": "viewer123"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "VIEWER"
    assert "password_hash" not in body
    claims = jwt.decode(body["access_token"], config.JWT_SECRET, algorithms=["HS256"])
    assert claims["role"] == "VIEWER" and claims["sub"] and claims["exp"]


def test_login_email_is_case_insensitive(client):
    resp = client.post("/api/v1/auth/login", json={"email": "  Viewer@CVI.local ", "password": "viewer123"})
    assert resp.status_code == 200


def test_login_fails_with_wrong_password(client):
    resp = client.post("/api/v1/auth/login", json={"email": "viewer@cvi.local", "password": "nope"})
    _assert_error(resp, 401, "INVALID_CREDENTIALS")


def test_login_fails_for_unknown_user(client):
    resp = client.post("/api/v1/auth/login", json={"email": "ghost@cvi.local", "password": "x"})
    _assert_error(resp, 401, "INVALID_CREDENTIALS")


def test_protected_endpoint_requires_token(client):
    _assert_error(client.get("/api/v1/vehicles"), 401, "UNAUTHORIZED")


def test_tampered_and_expired_tokens_rejected(client):
    token = login(client, "viewer@cvi.local")
    _assert_error(client.get("/api/v1/vehicles", headers={"Authorization": f"Bearer {token[:-2]}xx"}),
                  401, "UNAUTHORIZED")
    expired = jwt.encode({"sub": "u", "role": "ADMIN",
                          "exp": int((datetime.now(timezone.utc) - timedelta(minutes=1)).timestamp())},
                         config.JWT_SECRET, algorithm="HS256")
    _assert_error(client.get("/api/v1/vehicles", headers={"Authorization": f"Bearer {expired}"}),
                  401, "UNAUTHORIZED")
    forged = jwt.encode({"sub": "u", "role": "ADMIN", "exp": 9999999999}, "wrong-secret", algorithm="HS256")
    _assert_error(client.get("/api/v1/vehicles", headers={"Authorization": f"Bearer {forged}"}),
                  401, "UNAUTHORIZED")


def test_jwt_round_trips_through_require_role(client, viewer_headers, manager_headers):
    alert = "aaaaaaaa-0000-0000-0000-000000000001"
    # VIEWER can read ...
    assert client.get("/api/v1/alerts", headers=viewer_headers).status_code == 200
    # ... but is blocked from a FLEET_MANAGER-only endpoint
    _assert_error(client.post(f"/api/v1/alerts/{alert}/acknowledge", headers=viewer_headers), 403, "FORBIDDEN")
    _assert_error(client.post("/api/v1/ml/train", headers=viewer_headers, json={"simulation_id": "x"}),
                  403, "FORBIDDEN")
    _assert_error(client.post("/api/v1/simulation", headers=viewer_headers, json={}), 403, "FORBIDDEN")
    # FLEET_MANAGER passes
    resp = client.post(f"/api/v1/alerts/{alert}/acknowledge", headers=manager_headers)
    assert resp.status_code == 200 and resp.json()["status"] == "ACKNOWLEDGED"


def test_me_endpoint(client, manager_headers):
    resp = client.get("/api/v1/auth/me", headers=manager_headers)
    assert resp.status_code == 200
    assert resp.json()["role"] == "FLEET_MANAGER"


def test_register_is_admin_only(client, manager_headers, admin_headers):
    body = {"email": "new.user@cvi.local", "password": "longenough1", "role": "VIEWER"}
    _assert_error(client.post("/api/v1/auth/register", json=body, headers=manager_headers), 403, "FORBIDDEN")
    resp = client.post("/api/v1/auth/register", json=body, headers=admin_headers)
    if resp.status_code == 409:  # already created by an earlier run within the same session DB
        return
    assert resp.status_code == 201, resp.text
    assert "password_hash" not in resp.json()
    _assert_error(client.post("/api/v1/auth/register", json=body, headers=admin_headers), 409, "CONFLICT")
    assert client.post("/api/v1/auth/login", json={"email": body["email"], "password": body["password"]}
                       ).status_code == 200
