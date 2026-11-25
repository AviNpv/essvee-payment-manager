from fastapi.testclient import TestClient

from app.main import app


def test_login_success(client):
    response = client.post(
        "/api/auth/login", json={"email": "admin@essvee.in", "password": "ChangeMe123!"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["role"].upper() == "ADMIN"
    assert body["expires_in"] > 0


def test_login_rejects_bad_credentials(client):
    response = client.post(
        "/api/auth/login", json={"email": "admin@essvee.in", "password": "wrong-password"}
    )
    assert response.status_code == 401


def test_protected_endpoint_requires_token(db):
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/customers").status_code == 401
        assert anonymous.get("/api/reports/dashboard").status_code == 401
        assert anonymous.get("/api/sheets/status").status_code == 401


def test_invalid_token_rejected(db):
    with TestClient(app) as anonymous:
        response = anonymous.get(
            "/api/customers", headers={"Authorization": "Bearer not.a.jwt"}
        )
        assert response.status_code == 401


def test_change_password(client):
    response = client.post(
        "/api/auth/change-password",
        json={"current_password": "ChangeMe123!", "new_password": "NewPassword456!"},
    )
    assert response.status_code == 200
    with TestClient(app) as anonymous:
        assert anonymous.post(
            "/api/auth/login", json={"email": "admin@essvee.in", "password": "ChangeMe123!"}
        ).status_code == 401
        assert anonymous.post(
            "/api/auth/login", json={"email": "admin@essvee.in", "password": "NewPassword456!"}
        ).status_code == 200


def test_me_endpoint(client):
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["email"] == "admin@essvee.in"
