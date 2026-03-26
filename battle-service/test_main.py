import pytest
from fastapi.testclient import TestClient
from main import app, Base, engine

Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

client = TestClient(app)
battle_id = None
attacker_id = None
defender_id = None

def hero(name, cls="warrior"):
    return {"name": name, "heroClass": cls, "level": 5,
            "attack": 15, "defense": 8, "health": 120, "mana": 80,
            "currentHealth": 120, "currentMana": 80,
            "abilities": ["berserker_attack"]}

def enemy(name):
    return {"name": name, "level": 3, "attack": 10, "defense": 5,
            "health": 80, "mana": 0, "currentHealth": 80, "currentMana": 0, "abilities": []}

def test_init_battle():
    global battle_id, attacker_id, defender_id
    r = client.post("/battle", json={"type": "pve", "initiatedBy": "camp-1",
                                      "attackerParty": [hero("Hero")],
                                      "defenderParty": [enemy("Goblin")]})
    assert r.status_code == 201
    data = r.json()
    battle_id = data["battleId"]
    attacker_id = data["attackerUnits"][0]["unitId"]
    defender_id = data["defenderUnits"][0]["unitId"]
    assert battle_id is not None

def test_empty_party_fails():
    r = client.post("/battle", json={"type": "pve",
                                      "attackerParty": [],
                                      "defenderParty": [enemy("G")]})
    assert r.status_code == 400

def test_get_state():
    r = client.get(f"/battle/{battle_id}")
    assert r.status_code == 200
    assert r.json()["currentTurn"] == 1

def test_attack_action():
    r = client.post(f"/battle/{battle_id}/action",
                    json={"unitId": attacker_id, "actionType": "attack",
                          "targetUnitId": defender_id})
    assert r.status_code in (200, 409)

def test_defend_action():
    r = client.get(f"/battle/{battle_id}")
    active = r.json()["activeUnitId"]
    r2 = client.post(f"/battle/{battle_id}/action",
                     json={"unitId": active, "actionType": "defend"})
    assert r2.status_code in (200, 409)

def test_result_before_completion():
    r = client.get(f"/battle/{battle_id}/result")
    assert r.status_code in (200, 409)

def test_delete_in_progress_fails():
    r = client.post("/battle", json={"type": "pve", "initiatedBy": "c2",
                                      "attackerParty": [hero("H2")],
                                      "defenderParty": [enemy("E2")]})
    fresh_id = r.json()["battleId"]
    r2 = client.delete(f"/battle/{fresh_id}")
    assert r2.status_code == 403

def test_wrong_turn_rejected():
    r = client.get(f"/battle/{battle_id}")
    if r.json()["status"] == "completed":
        return  # battle already done, skip
    # Use the wrong unit ID
    r2 = client.post(f"/battle/{battle_id}/action",
                     json={"unitId": "wrong-id", "actionType": "attack"})
    assert r2.status_code in (409, 404)

def test_cast_ability():
    # Start a fresh battle with a mage
    r = client.post("/battle", json={"type": "pve", "initiatedBy": "c3",
                                      "attackerParty": [{"name": "Mage", "heroClass": "order",
                                                         "level": 3, "attack": 10, "defense": 6,
                                                         "health": 110, "mana": 100,
                                                         "currentHealth": 110, "currentMana": 100,
                                                         "abilities": ["heal", "protect"]}],
                                      "defenderParty": [enemy("E3")]})
    b_id = r.json()["battleId"]
    a_id = r.json()["attackerUnits"][0]["unitId"]
    r2 = client.post(f"/battle/{b_id}/action",
                     json={"unitId": a_id, "actionType": "cast", "ability": "protect"})
    assert r2.status_code in (200, 409)
