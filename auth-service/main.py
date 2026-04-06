import hashlib, uuid
from datetime import datetime, timedelta
from typing import Optional
from fastapi import FastAPI, HTTPException, Header
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

app = FastAPI(title="Auth Service")
engine = create_engine("sqlite:///./auth.db", connect_args={"check_same_thread": False})
DB = sessionmaker(bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id       = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")
    scores   = relationship("Score", back_populates="user", cascade="all, delete-orphan")
    parties  = relationship("Party", back_populates="user", cascade="all, delete-orphan")

class UserSession(Base):
    __tablename__ = "sessions"
    id         = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id    = Column(String, ForeignKey("users.id"), nullable=False)
    username   = Column(String, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    user       = relationship("User", back_populates="sessions")

class ActiveCampaign(Base):
    __tablename__ = "active_campaigns"
    id          = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id     = Column(String, unique=True, nullable=False)
    campaign_id = Column(String, nullable=False)

class Party(Base):
    __tablename__ = "parties"
    id         = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id    = Column(String, ForeignKey("users.id"), nullable=False)
    name       = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    user       = relationship("User", back_populates="parties")
    heroes     = relationship("PartyHero", back_populates="party", cascade="all, delete-orphan")

class PartyHero(Base):
    __tablename__ = "party_heroes"
    id         = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    party_id   = Column(String, ForeignKey("parties.id"), nullable=False)
    hero_class = Column(String)
    level      = Column(Integer, default=1)
    attack     = Column(Integer, default=5)
    defense    = Column(Integer, default=5)
    health     = Column(Integer, default=100)
    mana       = Column(Integer, default=50)
    party      = relationship("Party", back_populates="heroes")

class Score(Base):
    __tablename__ = "scores"
    id             = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id        = Column(String, ForeignKey("users.id"), nullable=False)
    campaign_score = Column(Integer, nullable=False)
    achieved_at    = Column(DateTime, default=datetime.utcnow)
    user           = relationship("User", back_populates="scores")

Base.metadata.create_all(bind=engine)

def hash_pw(p): return hashlib.sha256(p.encode()).hexdigest()

@app.post("/auth/register", status_code=201)
def register(body: dict):
    u, p = body.get("username","").strip(), body.get("password","")
    if not u or not p: raise HTTPException(400, "username and password required")
    db = DB()
    try:
        if db.query(User).filter_by(username=u).first(): raise HTTPException(409, "username already taken")
        user = User(id=str(uuid.uuid4()), username=u, password_hash=hash_pw(p))
        db.add(user); db.commit()
        return {"userId": user.id, "username": user.username}
    finally: db.close()

@app.post("/auth/login")
def login(body: dict):
    db = DB()
    try:
        user = db.query(User).filter_by(username=body.get("username","")).first()
        if not user or user.password_hash != hash_pw(body.get("password","")): raise HTTPException(401, "invalid credentials")
        s = UserSession(id=str(uuid.uuid4()), user_id=user.id, username=user.username,
                        expires_at=datetime.utcnow()+timedelta(hours=24))
        db.add(s); db.commit()
        return {"token": s.id, "userId": user.id, "username": user.username}
    finally: db.close()

@app.post("/auth/logout")
def logout(authorization: Optional[str] = Header(None)):
    if authorization and authorization.startswith("Session "):
        db = DB()
        try:
            s = db.query(UserSession).filter_by(id=authorization[8:].strip()).first()
            if s: db.delete(s); db.commit()
        finally: db.close()
    return {"message": "logged out"}

@app.get("/internal/session/{sid}")
def validate_session(sid: str):
    db = DB()
    try:
        s = db.query(UserSession).filter_by(id=sid).first()
        if not s or s.expires_at < datetime.utcnow(): raise HTTPException(401, "invalid or expired session")
        return {"userId": s.user_id, "username": s.username}
    finally: db.close()

@app.get("/internal/user")
def get_user_by_username(username: str):
    """Used by PvP service to resolve username to userId."""
    db = DB()
    try:
        u = db.query(User).filter_by(username=username).first()
        if not u: raise HTTPException(404, "user not found")
        return {"userId": u.id, "username": u.username,
                "partyCount": db.query(Party).filter_by(user_id=u.id).count()}
    finally: db.close()

@app.get("/profile/{uid}")
def get_profile(uid: str, x_user_id: Optional[str] = Header(None)):
    if x_user_id != uid: raise HTTPException(403, "access denied")
    db = DB()
    try:
        u = db.query(User).filter_by(id=uid).first()
        if not u: raise HTTPException(404, "user not found")
        ac = db.query(ActiveCampaign).filter_by(user_id=uid).first()
        all_scores = db.query(Score).order_by(Score.campaign_score.desc()).all()
        my_scores  = db.query(Score).filter_by(user_id=uid).order_by(Score.achieved_at.desc()).all()
        # Compute rank: position of best score in global leaderboard
        my_best = db.query(Score).filter_by(user_id=uid).order_by(Score.campaign_score.desc()).first()
        rank = None
        if my_best:
            rank = next((i+1 for i,s in enumerate(all_scores) if s.user_id == uid), None)
        return {"userId": u.id, "username": u.username,
                "activeCampaignId": ac.campaign_id if ac else None,
                "rank": rank,
                "savedParties": [_party(p) for p in u.parties],
                "scores": [{"scoreId":s.id,"campaignScore":s.campaign_score,
                            "achievedAt":s.achieved_at.isoformat()} for s in my_scores]}
    finally: db.close()

@app.get("/profile/{uid}/parties")
def get_parties(uid: str, x_user_id: Optional[str] = Header(None)):
    if x_user_id != uid: raise HTTPException(403, "access denied")
    db = DB()
    try: return {"parties": [_party(p) for p in db.query(Party).filter_by(user_id=uid).all()]}
    finally: db.close()

@app.post("/profile/{uid}/parties", status_code=201)
def save_party(uid: str, body: dict, x_user_id: Optional[str] = Header(None)):
    if x_user_id != uid: raise HTTPException(403, "access denied")
    db = DB()
    try:
        count = db.query(Party).filter_by(user_id=uid).count()
        if count >= 5: raise HTTPException(409, "party limit reached")
        p = Party(id=str(uuid.uuid4()), user_id=uid, name=body.get("name","Party"))
        db.add(p); db.flush()
        for h in body.get("heroes", []):
            db.add(PartyHero(id=str(uuid.uuid4()), party_id=p.id,
                             hero_class=h.get("heroClass"), level=h.get("level",1),
                             attack=h.get("attack",5), defense=h.get("defense",5),
                             health=h.get("health",100), mana=h.get("mana",50)))
        db.commit()
        return {"partyId": p.id, "name": p.name}
    finally: db.close()

@app.delete("/profile/{uid}/parties/{pid}")
def delete_party(uid: str, pid: str, x_user_id: Optional[str] = Header(None)):
    if x_user_id != uid: raise HTTPException(403, "access denied")
    db = DB()
    try:
        p = db.query(Party).filter_by(id=pid, user_id=uid).first()
        if not p: raise HTTPException(404, "party not found")
        db.delete(p); db.commit()
        return {"message": "deleted"}
    finally: db.close()

@app.post("/profile/{uid}/scores", status_code=201)
def save_score(uid: str, body: dict, x_user_id: Optional[str] = Header(None)):
    if x_user_id != uid: raise HTTPException(403, "access denied")
    db = DB()
    try:
        s = Score(id=str(uuid.uuid4()), user_id=uid, campaign_score=body.get("campaignScore",0))
        db.add(s); db.commit()
        return {"scoreId":s.id,"campaignScore":s.campaign_score,"achievedAt":s.achieved_at.isoformat()}
    finally: db.close()

@app.get("/scores/hall-of-fame")
def hall_of_fame():
    db = DB()
    try:
        top = db.query(Score).order_by(Score.campaign_score.desc()).limit(10).all()
        result = []
        for i, s in enumerate(top):
            u = db.query(User).filter_by(id=s.user_id).first()
            result.append({"rank":i+1,"username":u.username if u else "?",
                           "campaignScore":s.campaign_score,"achievedAt":s.achieved_at.isoformat()})
        return {"hallOfFame": result}
    finally: db.close()

@app.post("/internal/campaign/set")
def set_campaign(body: dict):
    db = DB()
    try:
        db.query(ActiveCampaign).filter_by(user_id=body["userId"]).delete()
        db.add(ActiveCampaign(id=str(uuid.uuid4()), user_id=body["userId"], campaign_id=body["campaignId"]))
        db.commit(); return {"message": "ok"}
    finally: db.close()

@app.post("/internal/campaign/clear")
def clear_campaign(body: dict):
    db = DB()
    try:
        db.query(ActiveCampaign).filter_by(user_id=body["userId"]).delete()
        db.commit(); return {"message": "ok"}
    finally: db.close()

def _party(p):
    return {"partyId":p.id,"name":p.name,"createdAt":p.created_at.isoformat() if p.created_at else None,
            "heroes":[{"heroId":h.id,"heroClass":h.hero_class,"level":h.level,
                       "attack":h.attack,"defense":h.defense,"health":h.health,"mana":h.mana}
                      for h in p.heroes]}

if __name__ == "__main__":
    import uvicorn; uvicorn.run("main:app", host="0.0.0.0", port=8081)
