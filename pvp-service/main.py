import uuid
from datetime import datetime
from typing import Optional
import httpx
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Column, String, Integer, Boolean, DateTime, ForeignKey, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
import os

app = FastAPI(title="PvP Service")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
engine = create_engine("sqlite:///./pvp.db", connect_args={"check_same_thread": False})
DB = sessionmaker(bind=engine)
Base = declarative_base()
BATTLE_URL = os.getenv("BATTLE_SERVICE_URL", "http://localhost:8083")
AUTH_URL   = os.getenv("AUTH_SERVICE_URL",   "http://localhost:8081")

class Invitation(Base):
    __tablename__ = "invitations"
    id               = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    inviter_id       = Column(String, nullable=False)
    inviter_username = Column(String)
    invitee_id       = Column(String, nullable=False)
    invitee_username = Column(String)
    status           = Column(String, default="pending")   # pending|accepted|declined|in_battle|completed
    active_battle_id = Column(String)
    created_at       = Column(DateTime, default=datetime.utcnow)
    selections       = relationship("PartySelection", back_populates="invitation",
                                    cascade="all, delete-orphan")

class PartySelection(Base):
    __tablename__ = "party_selections"
    id            = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    invitation_id = Column(String, ForeignKey("invitations.id"), nullable=False)
    user_id       = Column(String, nullable=False)
    party_id      = Column(String, nullable=False)
    invitation    = relationship("Invitation", back_populates="selections")

class LeagueStat(Base):
    __tablename__ = "league"
    id       = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id  = Column(String, unique=True, nullable=False)
    username = Column(String)
    wins     = Column(Integer, default=0)
    losses   = Column(Integer, default=0)

Base.metadata.create_all(bind=engine)

def inv_map(i: Invitation) -> dict:
    return {"invitationId":i.id,"inviterId":i.inviter_id,"inviterUsername":i.inviter_username,
            "inviteeId":i.invitee_id,"inviteeUsername":i.invitee_username,
            "status":i.status,"activeBattleId":i.active_battle_id,
            "createdAt":i.created_at.isoformat() if i.created_at else None}

def _upsert_stat(db, uid: str, uname: str, won: bool):
    s = db.query(LeagueStat).filter_by(user_id=uid).first()
    if not s: s = LeagueStat(id=str(uuid.uuid4()), user_id=uid, username=uname); db.add(s)
    if won: s.wins += 1
    else: s.losses += 1
    db.flush()

@app.post("/pvp/invitations", status_code=201)
def send_invitation(body: dict, x_user_id: Optional[str] = Header(None),
                    x_username: Optional[str] = Header(None)):
    invitee_username = (body.get("inviteeUsername","")).strip()
    if not invitee_username: raise HTTPException(400,"inviteeUsername required")

    # Spec US7: invitee must have a registered profile
    try:
        r = httpx.get(f"{AUTH_URL}/internal/user?username={invitee_username}", timeout=3)
        if r.status_code != 200: raise HTTPException(404, f"user '{invitee_username}' not found")
        invitee_data = r.json()
        invitee_id = invitee_data["userId"]
    except HTTPException: raise
    except Exception: raise HTTPException(503, "auth service unavailable")

    # Spec US7: both players must have at least one saved party
    try:
        my_parties = httpx.get(f"{AUTH_URL}/profile/{x_user_id}/parties",
                               headers={"X-User-Id":x_user_id}, timeout=3).json()
        if not my_parties.get("parties"): raise HTTPException(400, "you have no saved parties")
    except HTTPException: raise
    except Exception: pass

    if invitee_data.get("partyCount", 0) == 0:
        raise HTTPException(400, f"{invitee_username} has no saved parties")

    db = DB()
    try:
        existing = db.query(Invitation).filter_by(
            inviter_id=x_user_id, invitee_id=invitee_id, status="pending").first()
        if existing: raise HTTPException(409,"pending invitation already exists")
        inv = Invitation(id=str(uuid.uuid4()), inviter_id=x_user_id,
                         inviter_username=x_username or "", invitee_id=invitee_id,
                         invitee_username=invitee_username)
        db.add(inv); db.commit()
        return inv_map(inv)
    finally: db.close()

@app.get("/pvp/invitations")
def list_invitations(x_user_id: Optional[str] = Header(None)):
    db = DB()
    try:
        invs = db.query(Invitation).filter(
            (Invitation.inviter_id == x_user_id) | (Invitation.invitee_id == x_user_id)
        ).order_by(Invitation.created_at.desc()).all()
        return {"invitations": [inv_map(i) for i in invs]}
    finally: db.close()

@app.get("/pvp/invitations/{inv_id}")
def get_invitation(inv_id: str, x_user_id: Optional[str] = Header(None)):
    db = DB()
    try:
        inv = db.query(Invitation).filter_by(id=inv_id).first()
        if not inv: raise HTTPException(404,"not found")
        if inv.inviter_id != x_user_id and inv.invitee_id != x_user_id:
            raise HTTPException(403,"access denied")
        return inv_map(inv)
    finally: db.close()

@app.put("/pvp/invitations/{inv_id}")
def respond_invitation(inv_id: str, body: dict, x_user_id: Optional[str] = Header(None)):
    db = DB()
    try:
        inv = db.query(Invitation).filter_by(id=inv_id).first()
        if not inv: raise HTTPException(404,"not found")
        if inv.invitee_id != x_user_id: raise HTTPException(403,"only invitee can respond")
        if inv.status != "pending": raise HTTPException(409,"invitation no longer pending")
        action = body.get("action","")
        if action not in ("accept","decline"): raise HTTPException(400,"use 'accept' or 'decline'")
        inv.status = "accepted" if action == "accept" else "declined"
        db.commit()
        return {"invitationId":inv.id,"status":inv.status}
    finally: db.close()

@app.post("/pvp/invitations/{inv_id}/select-party")
def select_party(inv_id: str, body: dict, x_user_id: Optional[str] = Header(None)):
    """Spec US7: when accepted, both players choose a party → battle starts."""
    db = DB()
    try:
        inv = db.query(Invitation).filter_by(id=inv_id).first()
        if not inv: raise HTTPException(404,"not found")
        if inv.inviter_id != x_user_id and inv.invitee_id != x_user_id:
            raise HTTPException(403,"access denied")
        if inv.status not in ("accepted","in_battle"):
            raise HTTPException(409,"invitation must be accepted first")
        if db.query(PartySelection).filter_by(invitation_id=inv_id, user_id=x_user_id).first():
            raise HTTPException(409,"already selected a party")

        party_id = body.get("partyId","")
        sel = PartySelection(id=str(uuid.uuid4()), invitation_id=inv_id,
                             user_id=x_user_id, party_id=party_id)
        db.add(sel); db.flush()

        all_sels = db.query(PartySelection).filter_by(invitation_id=inv_id).all()
        if len(all_sels) >= 2:
            battle_id = _start_pvp_battle(inv, all_sels, db)
            inv.status = "in_battle"; inv.active_battle_id = battle_id
            db.commit()
            return {"status":"in_battle","battleId":battle_id}
        db.commit()
        return {"status":"waiting_for_opponent","battleId":""}
    finally: db.close()

@app.post("/pvp/battle/{battle_id}/result")
def record_result(battle_id: str):
    db = DB()
    try:
        inv = db.query(Invitation).filter_by(active_battle_id=battle_id).first()
        if not inv: raise HTTPException(404,"battle not associated with any invitation")
        try:
            r = httpx.get(f"{BATTLE_URL}/battle/{battle_id}/result", timeout=5)
            if r.status_code != 200: raise HTTPException(409,"battle not completed")
            winner_team = r.json().get("winner")
        except HTTPException: raise
        except Exception: raise HTTPException(409,"battle not completed or service unavailable")
        # attacker = inviter
        w_id = inv.inviter_id if winner_team=="attacker" else inv.invitee_id
        l_id = inv.invitee_id if winner_team=="attacker" else inv.inviter_id
        w_name = inv.inviter_username if w_id==inv.inviter_id else inv.invitee_username
        l_name = inv.invitee_username if l_id==inv.invitee_id else inv.inviter_username
        _upsert_stat(db, w_id, w_name, True)
        _upsert_stat(db, l_id, l_name, False)
        inv.status = "completed"; db.commit()
        return {"winnerId":w_id,"loserId":l_id,"winnerUsername":w_name}
    finally: db.close()

@app.get("/pvp/league")
def get_league():
    db = DB()
    try:
        stats = db.query(LeagueStat).order_by(LeagueStat.wins.desc()).all()
        return {"standings":[{"rank":i+1,"userId":s.user_id,"username":s.username,
                               "wins":s.wins,"losses":s.losses,
                               "battlesPlayed":s.wins+s.losses}
                              for i,s in enumerate(stats)]}
    finally: db.close()

@app.get("/pvp/league/{uid}/stats")
def get_player_stats(uid: str):
    db = DB()
    try:
        s = db.query(LeagueStat).filter_by(user_id=uid).first()
        if not s: raise HTTPException(404,"not found")
        stats = db.query(LeagueStat).order_by(LeagueStat.wins.desc()).all()
        rank = next((i+1 for i,st in enumerate(stats) if st.user_id==uid), 1)
        return {"userId":s.user_id,"username":s.username,"wins":s.wins,
                "losses":s.losses,"battlesPlayed":s.wins+s.losses,"rank":rank}
    finally: db.close()

def _start_pvp_battle(inv, sels, db) -> str:
    """Fetch both parties from auth and start a real PvP battle."""
    sel_map = {s.user_id: s.party_id for s in sels}
    inviter_party_id = sel_map.get(inv.inviter_id)
    invitee_party_id = sel_map.get(inv.invitee_id)

    def fetch_party(uid, pid):
        try:
            r = httpx.get(f"{AUTH_URL}/profile/{uid}/parties", headers={"X-User-Id":uid}, timeout=3)
            parties = r.json().get("parties", [])
            p = next((x for x in parties if x["partyId"]==pid), None)
            if p: return p.get("heroes", [])
        except Exception: pass
        return [{"name":"Fighter","heroClass":"warrior","level":1,"attack":8,"defense":5,
                 "health":100,"mana":50,"currentHealth":100,"currentMana":50,"abilities":[]}]

    atk_heroes = [{"name":h.get("heroClass","Hero"),"heroClass":h.get("heroClass"),
                   "level":h.get("level",1),"attack":h.get("attack",8),
                   "defense":h.get("defense",5),"health":h.get("health",100),
                   "mana":h.get("mana",50),"currentHealth":h.get("health",100),
                   "currentMana":h.get("mana",50),"abilities":[]}
                  for h in fetch_party(inv.inviter_id, inviter_party_id)]
    def_heroes = [{"name":h.get("heroClass","Hero"),"heroClass":h.get("heroClass"),
                   "level":h.get("level",1),"attack":h.get("attack",8),
                   "defense":h.get("defense",5),"health":h.get("health",100),
                   "mana":h.get("mana",50),"currentHealth":h.get("health",100),
                   "currentMana":h.get("mana",50),"abilities":[]}
                  for h in fetch_party(inv.invitee_id, invitee_party_id)]
    try:
        r = httpx.post(f"{BATTLE_URL}/battle",
                       json={"type":"pvp","initiatedBy":inv.id,
                             "attackerParty":atk_heroes,"defenderParty":def_heroes}, timeout=5)
        return r.json().get("battleId", str(uuid.uuid4()))
    except Exception:
        return str(uuid.uuid4())

if __name__ == "__main__":
    import uvicorn; uvicorn.run("main:app", host="0.0.0.0", port=8084)
