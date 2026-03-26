import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_protected_without_token():
    r = client.get("/pve/campaign/any")
    assert r.status_code == 401

def test_protected_bad_token():
    r = client.get("/pve/campaign/any", headers={"Authorization": "Session bad"})
    assert r.status_code == 401

def test_register_is_public():
    r = client.post("/auth/register", json={"username": "gw", "password": "p"})
    assert r.status_code != 401

def test_hall_of_fame_is_public():
    r = client.get("/scores/hall-of-fame")
    assert r.status_code != 401

def test_pvp_league_is_public():
    r = client.get("/pvp/league")
    assert r.status_code != 401
