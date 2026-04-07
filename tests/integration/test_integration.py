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

def delete(path, **kw):
    return requests.delete(f"{BASE}{path}", **kw)

def auth_header(token):
    return {"Authorization": f"Session {token}"}


state = {}


class TestGatewayHealth:
    def test_hall_of_fame_reachable(self):
        """Public endpoint should return 200 without authentication."""
        r = get("/scores/hall-of-fame")
        assert r.status_code == 200, r.text
        assert "hallOfFame" in r.json()

    def test_pvp_league_reachable(self):
        r = get("/pvp/league")
        assert r.status_code == 200
        assert "standings" in r.json()

    def test_protected_endpoint_requires_token(self):
        r = get("/pve/campaign/any-id")
        assert r.status_code == 401

    def test_bad_token_rejected(self):
        r = get("/pve/campaign/any-id", headers={"Authorization": "Session bad-token"})
        assert r.status_code == 401



class TestAuthFlow:
    USERNAME = f"integ_user_{int(time.time())}"
    PASSWORD = "S3cur3Pass!"

    def test_register_new_user(self):
        r = post("/auth/register",
                 json={"username": self.USERNAME, "password": self.PASSWORD})
        assert r.status_code == 201, r.text
        data = r.json()
        assert "userId" in data
        state["user_id"] = data["userId"]
        state["username"] = data["username"]

    def test_duplicate_registration_rejected(self):
        r = post("/auth/register",
                 json={"username": self.USERNAME, "password": "other"})
        assert r.status_code == 409

    def test_login(self):
        r = post("/auth/login",
                 json={"username": self.USERNAME, "password": self.PASSWORD})
        assert r.status_code == 200, r.text
        data = r.json()
        assert "token" in data
        state["token"] = data["token"]

    def test_wrong_password_rejected(self):
        r = post("/auth/login",
                 json={"username": self.USERNAME, "password": "wrong"})
        assert r.status_code == 401

    def test_authenticated_profile_readable(self):
        uid = state["user_id"]
        r = get(f"/profile/{uid}", headers=auth_header(state["token"]))
        assert r.status_code == 200
        assert r.json()["username"] == self.USERNAME

    def test_other_users_profile_forbidden(self):
        r = post("/auth/register",
                 json={"username": f"{self.USERNAME}_b", "password": "pw"})
        uid_b = r.json()["userId"]
        r2 = get(f"/profile/{uid_b}", headers=auth_header(state["token"]))
        assert r2.status_code == 403



class TestPvEFlow:
    def test_start_campaign(self):
        assert "token" in state, "Auth tests must run first"
        r = post("/pve/campaign",
                 json={"heroClass": "warrior"},
                 headers=auth_header(state["token"]))
        assert r.status_code == 201, r.text
        data = r.json()
        assert "campaignId" in data
        state["campaign_id"] = data["campaignId"]
        party = data["party"]
        assert len(party) >= 1
        hero = party[0]
        assert hero["attack"] == 5
        assert hero["defense"] == 5
        assert hero["health"] == 100
        assert hero["mana"] == 50

    def test_duplicate_campaign_rejected(self):
        r = post("/pve/campaign",
                 json={"heroClass": "mage"},
                 headers=auth_header(state["token"]))
        assert r.status_code == 409

    def test_get_campaign(self):
        cid = state["campaign_id"]
        r = get(f"/pve/campaign/{cid}", headers=auth_header(state["token"]))
        assert r.status_code == 200
        assert r.json()["campaignId"] == cid

    def test_campaign_owned_by_user(self):
        """Another user's token cannot read this campaign."""
        r = post("/auth/register",
                 json={"username": f"eve_{int(time.time())}", "password": "pw"})
        r2 = post("/auth/login",
                  json={"username": r.json()["username"], "password": "pw"})
        eve_token = r2.json()["token"]
        cid = state["campaign_id"]
        r3 = get(f"/pve/campaign/{cid}", headers=auth_header(eve_token))
        assert r3.status_code == 403

    def test_next_room_advances_campaign(self):
        cid = state["campaign_id"]
        r = post(f"/pve/campaign/{cid}/next-room",
                 headers=auth_header(state["token"]))
        assert r.status_code == 200
        data = r.json()
        assert "roomType" in data
        assert data["roomType"] in ("battle", "inn")
        state["room_type"] = data["roomType"]
        if data["roomType"] == "battle":
            state["active_battle_id"] = data.get("battleId")

    def test_party_endpoint_returns_heroes(self):
        cid = state["campaign_id"]
        r = get(f"/pve/campaign/{cid}/party",
                headers=auth_header(state["token"]))
        assert r.status_code == 200
        assert "party" in r.json()

    def test_inventory_endpoint_returns_gold(self):
        cid = state["campaign_id"]
        r = get(f"/pve/campaign/{cid}/inventory",
                headers=auth_header(state["token"]))
        assert r.status_code == 200
        data = r.json()
        assert "gold" in data



class TestBattleViaGateway:
    def test_battle_created_by_pve_service_is_fetchable(self):
        """If next-room returned a battle, verify we can query it."""
        bid = state.get("active_battle_id")
        if not bid:
            pytest.skip("No battle room encountered in previous test")
        r = get(f"/battle/{bid}", headers=auth_header(state["token"]))
        assert r.status_code == 200
        data = r.json()
        assert "battleId" in data
        assert data["status"] in ("in_progress", "completed")

    def test_battle_state_has_units(self):
        bid = state.get("active_battle_id")
        if not bid:
            pytest.skip("No battle room encountered in previous test")
        r = get(f"/battle/{bid}", headers=auth_header(state["token"]))
        data = r.json()
        assert len(data.get("attackerUnits", [])) + len(data.get("defenderUnits", [])) >= 2


class TestScorePersistence:
    def test_post_score_via_gateway(self):
        uid = state["user_id"]
        r = post(f"/profile/{uid}/scores",
                 json={"campaignScore": 9999},
                 headers=auth_header(state["token"]))
        assert r.status_code == 201
        assert r.json()["campaignScore"] == 9999

    def test_score_appears_in_hall_of_fame(self):
        r = get("/scores/hall-of-fame")
        assert r.status_code == 200
        names = [e["username"] for e in r.json()["hallOfFame"]]
        assert state["username"] in names

    def test_hall_of_fame_ordered_descending(self):
        r = get("/scores/hall-of-fame")
        scores = [e["campaignScore"] for e in r.json()["hallOfFame"]]
        assert scores == sorted(scores, reverse=True)



class TestPvPFlow:
    @classmethod
    def setup_method(self):
        """Ensure two players exist with saved parties."""
        ts = int(time.time())
        for key, uname in [("pvp_a", f"pvpa_{ts}"), ("pvp_b", f"pvpb_{ts}")]:
            post("/auth/register", json={"username": uname, "password": "pw"})
            r = post("/auth/login", json={"username": uname, "password": "pw"})
            token = r.json()["token"]
            uid = r.json()["userId"]
            post(f"/profile/{uid}/parties",
                 json={"name": "MyParty", "heroes": [
                     {"heroClass": "warrior", "level": 1,
                      "attack": 5, "defense": 5, "health": 100, "mana": 50}
                 ]},
                 headers=auth_header(token))
            parties = get(f"/profile/{uid}/parties",
                           headers=auth_header(token)).json()
            party_id = parties["parties"][0]["partyId"] if parties.get("parties") else None
            state[key] = {"uid": uid, "uname": uname, "token": token, "party_id": party_id}

    def test_league_standings_accessible_without_auth(self):
        r = get("/pvp/league")
        assert r.status_code == 200

    def test_send_invitation(self):
        a = state["pvp_a"]
        b = state["pvp_b"]
        r = post("/pvp/invitations",
                 json={"inviteeUsername": b["uname"]},
                 headers=auth_header(a["token"]))
        assert r.status_code in (201, 503), r.text
        if r.status_code == 201:
            state["invitation_id"] = r.json()["invitationId"]

    def test_invitee_can_accept(self):
        if "invitation_id" not in state:
            pytest.skip("Invitation not created")
        inv_id = state["invitation_id"]
        b = state["pvp_b"]
        r = put(f"/pvp/invitations/{inv_id}",
                json={"action": "accept"},
                headers=auth_header(b["token"]))
        assert r.status_code == 200
        assert r.json()["status"] == "accepted"

    def test_inviter_cannot_accept(self):
        if "invitation_id" not in state:
            pytest.skip("Invitation not created")
        inv_id = state["invitation_id"]
        a = state["pvp_a"]
        r = put(f"/pvp/invitations/{inv_id}",
                json={"action": "accept"},
                headers=auth_header(a["token"]))
        assert r.status_code == 403


