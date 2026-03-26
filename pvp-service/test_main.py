import pytest
from fastapi.testclient import TestClient
from main import app, Base, engine, Invitation, DB

Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

client = TestClient(app)
inv_id = None
INVITER, INVITEE = "u1", "u2"

def h(uid): return {"X-User-Id": uid}

def test_create_invitation_directly():
    global inv_id
    db = DB()
    inv = Invitation(inviter_id=INVITER, inviter_username="p1",
                     invitee_id=INVITEE, invitee_username="p2")
    db.add(inv); db.commit()
    inv_id = inv.id
    db.close()
    assert inv_id

def test_get_invitation():
    r = client.get(f"/pvp/invitations/{inv_id}", headers=h(INVITER))
    assert r.status_code == 200
    assert r.json()["status"] == "pending"

def test_stranger_denied():
    r = client.get(f"/pvp/invitations/{inv_id}", headers=h("stranger"))
    assert r.status_code == 403

def test_only_invitee_responds():
    r = client.put(f"/pvp/invitations/{inv_id}", json={"action":"accept"}, headers=h(INVITER))
    assert r.status_code == 403

def test_accept():
    r = client.put(f"/pvp/invitations/{inv_id}", json={"action":"accept"}, headers=h(INVITEE))
    assert r.status_code == 200
    assert r.json()["status"] == "accepted"

def test_accept_twice_fails():
    r = client.put(f"/pvp/invitations/{inv_id}", json={"action":"accept"}, headers=h(INVITEE))
    assert r.status_code == 409

def test_select_first_party():
    r = client.post(f"/pvp/invitations/{inv_id}/select-party",
                    json={"partyId":"p1"}, headers=h(INVITER))
    assert r.status_code == 200
    assert r.json()["status"] == "waiting_for_opponent"

def test_select_second_party():
    r = client.post(f"/pvp/invitations/{inv_id}/select-party",
                    json={"partyId":"p2"}, headers=h(INVITEE))
    assert r.status_code == 200
    assert r.json()["status"] in ("in_battle", "waiting_for_opponent")

def test_select_twice_fails():
    r = client.post(f"/pvp/invitations/{inv_id}/select-party",
                    json={"partyId":"p1"}, headers=h(INVITER))
    assert r.status_code == 409

def test_league():
    r = client.get("/pvp/league")
    assert r.status_code == 200
    assert "standings" in r.json()
