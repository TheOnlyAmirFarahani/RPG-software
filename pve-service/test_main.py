import pytest
from fastapi.testclient import TestClient
from main import app, Base, engine, exp_needed, battle_chance

Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

client = TestClient(app)
campaign_id = None
hero_id = None
USER = "test-user-1"
H = {"X-User-Id": USER}

def test_start_campaign():
    global campaign_id, hero_id
    r = client.post("/pve/campaign", json={"heroClass": "warrior"}, headers=H)
    assert r.status_code == 201
    campaign_id = r.json()["campaignId"]
    hero = r.json()["party"][0]
    hero_id = hero["heroId"]
    # Spec: all heroes start at exactly 5/5/100/50
    assert hero["attack"] == 5
    assert hero["defense"] == 5
    assert hero["health"] == 100
    assert hero["mana"] == 50
    assert campaign_id

def test_invalid_class():
    r = client.post("/pve/campaign", json={"heroClass": "wizard"}, headers=H)
    assert r.status_code in (400, 409)  # 409 if campaign already exists

def test_get_campaign():
    r = client.get(f"/pve/campaign/{campaign_id}", headers=H)
    assert r.status_code == 200
    assert r.json()["campaignId"] == campaign_id

def test_access_denied():
    r = client.get(f"/pve/campaign/{campaign_id}", headers={"X-User-Id": "other"})
    assert r.status_code == 403

def test_next_room():
    r = client.post(f"/pve/campaign/{campaign_id}/next-room", headers=H)
    assert r.status_code == 200
    assert r.json()["roomType"] in ("battle", "inn")

def test_get_party():
    r = client.get(f"/pve/campaign/{campaign_id}/party", headers=H)
    assert r.status_code == 200
    assert "party" in r.json()

def test_get_inventory():
    r = client.get(f"/pve/campaign/{campaign_id}/inventory", headers=H)
    assert r.status_code == 200
    assert "gold" in r.json()

def test_save_campaign():
    # reset status if needed
    from main import DB, Campaign
    db = DB()
    c = db.query(Campaign).filter_by(id=campaign_id).first()
    if c: c.status = "in_progress"; db.commit()
    db.close()
    r = client.put(f"/pve/campaign/{campaign_id}", headers=H)
    assert r.status_code == 200

def test_battle_chance_formula():
    assert battle_chance(0) == pytest.approx(0.60, abs=1e-2)
    assert battle_chance(10) == pytest.approx(0.63, abs=1e-2)
    assert battle_chance(100) == pytest.approx(0.90, abs=1e-2)

def test_exp_formula():
    assert exp_needed(1) == 238
    assert exp_needed(2) > exp_needed(1)
