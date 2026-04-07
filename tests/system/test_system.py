import os
import time
import pytest
import requests

BASE = os.getenv("GATEWAY_URL", "http://localhost:8080")


def post(path, **kw):
    return requests.post(f"{BASE}{path}", **kw)

def get(path, **kw):
    return requests.get(f"{BASE}{path}", **kw)

def put(path, **kw):
    return requests.put(f"{BASE}{path}", **kw)


def auth(token):
    return {"Authorization": f"Session {token}"}


def new_player(suffix=""):
    ts = int(time.time() * 1000)
    uname = f"player_{ts}{suffix}"
    post("/auth/register", json={"username": uname, "password": "Pw1!"})
    r = post("/auth/login", json={"username": uname, "password": "Pw1!"})
    assert r.status_code == 200, f"Login failed for {uname}: {r.text}"
    data = r.json()
    return {"uname": uname, "uid": data["userId"], "token": data["token"]}



class TestUS1_NewPlayerOnboarding:
    def test_full_onboarding_flow(self):
        p = new_player("_us1")


        r = get(f"/profile/{p['uid']}", headers=auth(p["token"]))
        assert r.status_code == 200
        assert r.json()["username"] == p["uname"]


        r2 = get("/scores/hall-of-fame")
        assert r2.status_code == 200



class TestUS2_CampaignExploration:
    def test_start_and_explore(self):
        p = new_player("_us2")
        h = auth(p["token"])

        r = post("/pve/campaign", json={"heroClass": "warrior"}, headers=h)
        assert r.status_code == 201
        cid = r.json()["campaignId"]
        hero = r.json()["party"][0]

        assert hero["attack"] == 5
        assert hero["defense"] == 5
        assert hero["health"] == 100
        assert hero["mana"] == 50

        seen_room_types = set()
        for _ in range(6):
            r2 = post(f"/pve/campaign/{cid}/next-room", headers=h)
            if r2.status_code != 200:
                break   # campaign may have ended
            seen_room_types.add(r2.json().get("roomType"))

        assert len(seen_room_types) >= 1
        assert seen_room_types.issubset({"battle", "inn"})

    def test_cannot_start_two_campaigns(self):
        p = new_player("_us2b")
        h = auth(p["token"])
        post("/pve/campaign", json={"heroClass": "mage"}, headers=h)
        r2 = post("/pve/campaign", json={"heroClass": "warrior"}, headers=h)
        assert r2.status_code == 409


class TestUS3_ScoreTracking:
    def test_score_lifecycle(self):
        p = new_player("_us3")
        h = auth(p["token"])

        r = post(f"/profile/{p['uid']}/scores",
                 json={"campaignScore": 42000}, headers=h)
        assert r.status_code == 201
        assert r.json()["campaignScore"] == 42000

        hof = get("/scores/hall-of-fame").json()["hallOfFame"]
        names = [e["username"] for e in hof]
        assert p["uname"] in names

        scores = [e["campaignScore"] for e in hof]
        assert scores == sorted(scores, reverse=True)


class TestUS4_PartySaving:
    def test_save_and_retrieve_party(self):
        p = new_player("_us4")
        h = auth(p["token"])

        party_payload = {
            "name": "Alpha Squad",
            "heroes": [
                {"heroClass": "warrior", "level": 3,
                 "attack": 8, "defense": 7, "health": 120, "mana": 40},
                {"heroClass": "mage", "level": 2,
                 "attack": 6, "defense": 4, "health": 90, "mana": 80},
            ]
        }

        r = post(f"/profile/{p['uid']}/parties", json=party_payload, headers=h)
        assert r.status_code == 201
        pid = r.json()["partyId"]

        r2 = get(f"/profile/{p['uid']}/parties", headers=h)
        assert r2.status_code == 200
        ids = [party["partyId"] for party in r2.json()["parties"]]
        assert pid in ids

        r3 = requests.delete(f"{BASE}/profile/{p['uid']}/parties/{pid}",
                             headers=h)
        assert r3.status_code == 200

        # Verify it's gone
        r4 = get(f"/profile/{p['uid']}/parties", headers=h)
        ids_after = [party["partyId"] for party in r4.json()["parties"]]
        assert pid not in ids_after



class TestUS5_BattleStateMachine:
    def test_battle_lifecycle(self):
        p = new_player("_us5")
        h = auth(p["token"])

        payload = {
            "type": "pve",
            "initiatedBy": "system-test",
            "attackerParty": [
                {"name": "Knight", "heroClass": "warrior",
                 "level": 5, "attack": 15, "defense": 10,
                 "health": 150, "mana": 60,
                 "currentHealth": 150, "currentMana": 60,
                 "abilities": ["berserker_attack"]}
            ],
            "defenderParty": [
                {"name": "Goblin", "level": 2,
                 "attack": 8, "defense": 4,
                 "health": 60, "mana": 0,
                 "currentHealth": 60, "currentMana": 0,
                 "abilities": []}
            ]
        }
        r = post("/battle", json=payload, headers=h)
        assert r.status_code == 201, r.text
        bid = r.json()["battleId"]
        attacker_id = r.json()["attackerUnits"][0]["unitId"]
        defender_id = r.json()["defenderUnits"][0]["unitId"]

        r2 = get(f"/battle/{bid}", headers=h)
        assert r2.status_code == 200
        assert r2.json()["status"] == "in_progress"

        active_id = r2.json()["activeUnitId"]
        target_id = defender_id if active_id == attacker_id else attacker_id
        r3 = post(f"/battle/{bid}/action",
                  json={"unitId": active_id,
                        "actionType": "attack",
                        "targetUnitId": target_id},
                  headers=h)
        assert r3.status_code in (200, 409)

    def test_empty_attacker_party_rejected(self):
        p = new_player("_us5b")
        h = auth(p["token"])
        r = post("/battle",
                 json={"type": "pve", "initiatedBy": "x",
                       "attackerParty": [],
                       "defenderParty": [{"name": "G", "level": 1,
                                          "attack": 5, "defense": 3,
                                          "health": 40, "mana": 0,
                                          "currentHealth": 40, "currentMana": 0,
                                          "abilities": []}]},
                 headers=h)
        assert r.status_code == 400


class TestUS6_GatewayRouting:
    """Smoke-test that the gateway routes each prefix to the right service."""

    def test_auth_prefix_routed(self):
        r = post("/auth/register",
                 json={"username": f"route_test_{int(time.time())}", "password": "pw"})
        assert r.status_code in (201, 409)

    def test_scores_prefix_routed(self):
        r = get("/scores/hall-of-fame")
        assert r.status_code == 200

    def test_pvp_prefix_routed(self):
        r = get("/pvp/league")
        assert r.status_code == 200

    def test_pve_prefix_requires_auth(self):
        r = get("/pve/campaign/dummy")
        assert r.status_code == 401

    def test_battle_prefix_requires_auth(self):
        r = get("/battle/dummy")
        assert r.status_code == 401


class TestUS7_Concurrency:
    def test_no_duplicate_username_under_race(self):
        import threading
        uname = f"race_{int(time.time())}"
        results = []

        def register():
            r = post("/auth/register",
                     json={"username": uname, "password": "pw"})
            results.append(r.status_code)

        threads = [threading.Thread(target=register) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        created = results.count(201)
        assert created == 1, f"Expected exactly 1 creation, got {results}"
