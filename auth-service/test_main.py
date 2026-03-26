import pytest
from fastapi.testclient import TestClient
from main import app, Base, engine

Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

client = TestClient(app)
token = None
user_id = None

def test_register():
    r = client.post("/auth/register", json={"username": "jim", "password": "pass123"})
    assert r.status_code == 201
    assert "userId" in r.json()

def test_register_duplicate():
    r = client.post("/auth/register", json={"username": "jim", "password": "other"})
    assert r.status_code == 409

def test_login_success():
    global token, user_id
    r = client.post("/auth/login", json={"username": "jim", "password": "pass123"})
    assert r.status_code == 200
    assert "token" in r.json()
    token = r.json()["token"]
    user_id = r.json()["userId"]

def test_login_wrong_password():
    r = client.post("/auth/login", json={"username": "jim", "password": "wrong"})
    assert r.status_code == 401

def test_validate_session():
    r = client.get(f"/internal/session/{token}")
    assert r.status_code == 200
    assert r.json()["userId"] == user_id

def test_invalid_session():
    r = client.get("/internal/session/bad-token")
    assert r.status_code == 401

def test_get_profile():
    r = client.get(f"/profile/{user_id}", headers={"X-User-Id": user_id})
    assert r.status_code == 200
    assert r.json()["username"] == "jim"

def test_get_profile_forbidden():
    r = client.get(f"/profile/{user_id}", headers={"X-User-Id": "someone-else"})
    assert r.status_code == 403

def test_save_score():
    r = client.post(f"/profile/{user_id}/scores",
                    json={"campaignScore": 5000},
                    headers={"X-User-Id": user_id})
    assert r.status_code == 201
    assert r.json()["campaignScore"] == 5000

def test_hall_of_fame():
    r = client.get("/scores/hall-of-fame")
    assert r.status_code == 200
    assert "hallOfFame" in r.json()

def test_logout():
    r = client.post("/auth/logout", headers={"Authorization": f"Session {token}"})
    assert r.status_code == 200

def test_session_invalid_after_logout():
    r = client.get(f"/internal/session/{token}")
    assert r.status_code == 401
