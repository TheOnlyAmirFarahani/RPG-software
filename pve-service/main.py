import json, random, uuid
from datetime import datetime
from typing import Optional
import httpx
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Column, String, Integer, Boolean, DateTime, ForeignKey, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
import os

app = FastAPI(title="PvE Service")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
engine = create_engine("sqlite:///./pve.db", connect_args={"check_same_thread": False})
DB = sessionmaker(bind=engine)
Base = declarative_base()
BATTLE_URL = os.getenv("BATTLE_SERVICE_URL", "http://localhost:8083")
AUTH_URL   = os.getenv("AUTH_SERVICE_URL",   "http://localhost:8081")

#  Models

class Campaign(Base):
    __tablename__ = "campaigns"
    id               = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id          = Column(String, nullable=False)
    status           = Column(String, default="in_progress")  # in_progress|in_battle|at_inn|completed
    current_room     = Column(Integer, default=0)
    gold             = Column(Integer, default=0)
    active_battle_id = Column(String)
    last_inn_room    = Column(Integer, default=0)  # spec: on loss, return here
    updated_at       = Column(DateTime, default=datetime.utcnow)
    heroes    = relationship("Hero",    back_populates="campaign", cascade="all, delete-orphan")
    inventory = relationship("InvItem", back_populates="campaign", cascade="all, delete-orphan")
    rooms     = relationship("Room",    back_populates="campaign", cascade="all, delete-orphan")

class Hero(Base):
    __tablename__ = "heroes"
    id             = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    campaign_id    = Column(String, ForeignKey("campaigns.id"), nullable=False)
    # Starting class (order/chaos/warrior/mage)  used for display when no spec/hybrid
    hero_class     = Column(String)
    # Per-class level tracking  key for specialization/hybrid detection
    order_levels   = Column(Integer, default=0)
    chaos_levels   = Column(Integer, default=0)
    warrior_levels = Column(Integer, default=0)
    mage_levels    = Column(Integer, default=0)
    # Evolution state
    specialization = Column(String)   # None | "order"|"chaos"|"warrior"|"mage"
    hybrid_class   = Column(String)   # None | "heretic"|"paladin"|etc.
    # Stats
    level          = Column(Integer, default=1)
    attack         = Column(Integer, default=5)
    defense        = Column(Integer, default=5)
    health         = Column(Integer, default=100)
    mana           = Column(Integer, default=50)
    current_health = Column(Integer, default=100)
    current_mana   = Column(Integer, default=50)
    experience     = Column(Integer, default=0)
    dead           = Column(Boolean, default=False)
    campaign       = relationship("Campaign", back_populates="heroes")

class InvItem(Base):
    __tablename__ = "inventory"
    id          = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    campaign_id = Column(String, ForeignKey("campaigns.id"), nullable=False)
    item_type   = Column(String)
    quantity    = Column(Integer, default=1)
    campaign    = relationship("Campaign", back_populates="inventory")

class Room(Base):
    __tablename__ = "rooms"
    id           = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    campaign_id  = Column(String, ForeignKey("campaigns.id"), nullable=False)
    room_number  = Column(Integer)
    room_type    = Column(String)
    revival_log  = Column(String, default="[]")  # JSON: who was revived/healed at this inn
    campaign     = relationship("Campaign", back_populates="rooms")
    inn_heroes   = relationship("InnHero", back_populates="room", cascade="all, delete-orphan")
    inn_items    = relationship("InnItem",  back_populates="room", cascade="all, delete-orphan")

class InnHero(Base):
    __tablename__ = "inn_heroes"
    id           = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    room_id      = Column(String, ForeignKey("rooms.id"), nullable=False)
    hero_class   = Column(String); level = Column(Integer); attack = Column(Integer)
    defense      = Column(Integer); health = Column(Integer); mana = Column(Integer)
    recruit_cost = Column(Integer); recruited = Column(Boolean, default=False)
    room         = relationship("Room", back_populates="inn_heroes")

class InnItem(Base):
    __tablename__ = "inn_items"
    id        = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    room_id   = Column(String, ForeignKey("rooms.id"), nullable=False)
    item_type = Column(String); cost = Column(Integer)
    quantity  = Column(Integer, default=1); purchased = Column(Boolean, default=False)
    room      = relationship("Room", back_populates="inn_items")

Base.metadata.create_all(bind=engine)

#  Spec formulas & constants

CLASSES = ["order", "chaos", "warrior", "mage"]

# Base stats at level 1 (spec: 5/5/100/50)
BASE_STATS = {"attack": 5, "defense": 5, "health": 100, "mana": 50}
# Per-level base gains (before class bonus)
BASE_GAINS = {"attack": 1, "defense": 1, "health": 5, "mana": 2}
# Class bonus per level (added on top of base)
CLASS_BONUS = {
    "order":   {"defense": 2, "mana": 5},
    "chaos":   {"attack": 3, "health": 5},
    "warrior": {"attack": 2, "defense": 3},
    "mage":    {"attack": 1, "mana": 5},
}
# Specialization names (single class reaches level 5)
SPEC_NAME = {"order":"priest","chaos":"invoker","warrior":"knight","mage":"wizard"}
# Hybrid names (two classes both reach level 5)
HYBRID_NAME = {
    frozenset(["order","chaos"]): "heretic",
    frozenset(["order","warrior"]): "paladin",
    frozenset(["order","mage"]): "prophet",
    frozenset(["chaos","warrior"]): "rogue",
    frozenset(["chaos","mage"]): "sorcerer",
    frozenset(["warrior","mage"]): "warlock",
}
# Reverse: hybrid name → component classes
HYBRID_COMPONENTS = {v: sorted(list(k)) for k,v in HYBRID_NAME.items()}

# Abilities per evolution state
BASE_ABILITIES = {
    "order":   ["protect","heal"],
    "chaos":   ["fireball","chain_lightning"],
    "warrior": ["berserker_attack"],
    "mage":    ["replenish"],
}
SPEC_ABILITIES = {
    "priest":  ["protect","heal_all"],
    "invoker": ["fireball","chain_lightning_50"],
    "knight":  ["berserker_stun"],
    "wizard":  ["replenish_cheap"],
}
HYBRID_ABILITIES = {
    "heretic":  ["fire_shield","chain_lightning"],
    "paladin":  ["berserker_heal","protect","heal"],
    "prophet":  ["protect_double","heal_double","replenish_double"],
    "rogue":    ["fireball","chain_lightning"],
    "sorcerer": ["fireball_double","chain_lightning"],
    "warlock":  ["berserker_attack","replenish"],
}
HYBRID_PASSIVES = {"rogue": ["sneak_attack"], "warlock": ["mana_burn"]}

def get_abilities(h: Hero):
    if h.hybrid_class: return HYBRID_ABILITIES.get(h.hybrid_class, [])
    if h.specialization:
        spec_n = SPEC_NAME.get(h.specialization, "")
        return SPEC_ABILITIES.get(spec_n, BASE_ABILITIES.get(h.hero_class, []))
    return BASE_ABILITIES.get(h.hero_class, [])

def get_passives(h: Hero):
    if h.hybrid_class: return HYBRID_PASSIVES.get(h.hybrid_class, [])
    return []

def display_class(h: Hero) -> str:
    if h.hybrid_class: return h.hybrid_class.capitalize()
    if h.specialization: return SPEC_NAME.get(h.specialization, h.hero_class).capitalize()
    return h.hero_class.capitalize()

def exp_needed(level: int) -> int:
    """Easier curve: ~40% of original so levelling feels rewarding."""
    return max(50, int(sum(500 + 75*l + 20*l*l for l in range(1, level+1)) * 0.4))

def battle_chance(cum: int) -> float:
    """60% base + 3% per 10 cumulative levels, max 90%."""
    return min(0.90, 0.60 + (cum // 10) * 0.03)

ITEM_COST   = {"bread":200,"cheese":500,"steak":1000,"water":150,"juice":400,"wine":750,"elixir":2000}
ITEM_HP     = {"bread":20,"cheese":50,"steak":200}
ITEM_MANA   = {"water":10,"juice":30,"wine":100}
ITEM_EFFECT = {"bread":"+20 HP","cheese":"+50 HP","steak":"+200 HP",
               "water":"+10 mana","juice":"+30 mana","wine":"+100 mana","elixir":"Revive+Full HP+Full mana"}

def _apply_level_up(h: Hero, chosen_class: str, db) -> dict:
    """Apply one level-up choosing the given class. Returns gain info."""
    if h.level >= 20: raise ValueError("hero is already at max level (20)")
    needed = exp_needed(h.level)
    if h.experience < needed: raise ValueError("not enough experience")
    h.experience -= needed; h.level += 1

    # Increment chosen class counter
    field = f"{chosen_class}_levels"
    setattr(h, field, getattr(h, field) + 1)

    # Check for specialization / hybrid transition
    cl = {"order":h.order_levels,"chaos":h.chaos_levels,"warrior":h.warrior_levels,"mage":h.mage_levels}
    at5 = [c for c,lv in cl.items() if lv >= 5]
    spec_changed_to = None
    hybrid_changed_to = None

    if not h.hybrid_class:
        if len(at5) >= 2 and not h.hybrid_class:
            key = frozenset(at5[:2])
            h.hybrid_class   = HYBRID_NAME.get(key)
            h.specialization = None   # lose specialization benefit
            hybrid_changed_to = h.hybrid_class
        elif len(at5) == 1 and not h.specialization:
            h.specialization = at5[0]
            spec_changed_to = SPEC_NAME.get(at5[0])

    # Calculate stat gains
    gains = dict(BASE_GAINS)  # always: +1/+1/+5/+2

    if h.hybrid_class:
        # Combine class bonuses of the two hybridized classes (no doubling)
        for cls in HYBRID_COMPONENTS.get(h.hybrid_class, []):
            for stat, val in CLASS_BONUS.get(cls, {}).items():
                gains[stat] = gains.get(stat, 0) + val
    elif h.specialization:
        # Spec class bonus is DOUBLED; base stays normal
        for stat, val in CLASS_BONUS.get(h.specialization, {}).items():
            gains[stat] = gains.get(stat, 0) + val * 2
    else:
        # Normal: base + chosen class bonus
        for stat, val in CLASS_BONUS.get(chosen_class, {}).items():
            gains[stat] = gains.get(stat, 0) + val

    h.attack  += gains["attack"]
    h.defense += gains["defense"]
    h.health  += gains["health"]
    h.mana    += gains["mana"]
    h.current_health = min(h.health, h.current_health + gains["health"])
    h.current_mana   = min(h.mana,   h.current_mana   + gains["mana"])
    db.flush()

    return {
        "gains": gains,
        "newSpecialization": spec_changed_to,
        "newHybridClass": hybrid_changed_to,
        "canLevelUpAgain": h.experience >= exp_needed(h.level)
    }

#  Helpers

def hero_map(h: Hero) -> dict:
    return {
        "heroId": h.id, "heroClass": h.hero_class, "displayClass": display_class(h),
        "specialization": h.specialization, "hybridClass": h.hybrid_class,
        "classLevels": {"order":h.order_levels,"chaos":h.chaos_levels,
                        "warrior":h.warrior_levels,"mage":h.mage_levels},
        "level": h.level, "attack": h.attack, "defense": h.defense,
        "health": h.health, "mana": h.mana,
        "currentHealth": h.current_health, "currentMana": h.current_mana,
        "experience": h.experience, "expNeeded": exp_needed(h.level),
        "canLevelUp": h.experience >= exp_needed(h.level),
        "dead": h.dead,
        "abilities": get_abilities(h), "passives": get_passives(h)
    }

def camp_map(c: Campaign, db) -> dict:
    inv = db.query(InvItem).filter_by(campaign_id=c.id).all()
    return {
        "campaignId": c.id, "status": c.status,
        "roomNumber": c.current_room, "gold": c.gold,
        "activeBattleId": c.active_battle_id, "lastInnRoom": c.last_inn_room,
        "party": [hero_map(h) for h in c.heroes],
        "inventory": [{"itemId":i.id,"itemType":i.item_type,"quantity":i.quantity,
                       "effect":ITEM_EFFECT.get(i.item_type,"")} for i in inv]
    }

def secure(cid: str, uid: str, db):
    c = db.query(Campaign).filter_by(id=cid).first()
    if not c: raise HTTPException(404, "campaign not found")
    if c.user_id != uid: raise HTTPException(403, "access denied")
    return c

def _notify_auth(path: str, data: dict, uid: str):
    try: httpx.post(f"{AUTH_URL}{path}", json=data,
                    headers={"X-User-Id": uid}, timeout=3)
    except Exception: pass

#  Routes

@app.post("/pve/campaign", status_code=201)
def start(body: dict, x_user_id: Optional[str] = Header(None)):
    cls = body.get("heroClass","warrior").lower()
    if cls not in CLASSES: raise HTTPException(400, "invalid class")
    db = DB()
    try:
        if db.query(Campaign).filter(Campaign.user_id==x_user_id,
                                     Campaign.status!="completed").first():
            raise HTTPException(409, "active campaign already exists")
        c = Campaign(id=str(uuid.uuid4()), user_id=x_user_id)
        db.add(c); db.flush()
        # Spec: every hero starts at exactly 5/5/100/50 regardless of class.
        # Class bonuses only apply when gaining levels. warrior_levels stays 0 until first level-up.
        h = Hero(id=str(uuid.uuid4()), campaign_id=c.id, hero_class=cls, **BASE_STATS)
        h.current_health = h.health; h.current_mana = h.mana
        db.add(h); db.commit()
        try: httpx.post(f"{AUTH_URL}/internal/campaign/set",
                        json={"userId":x_user_id,"campaignId":c.id}, timeout=3)
        except Exception: pass
        c = db.query(Campaign).filter_by(id=c.id).first()
        return camp_map(c, db)
    finally: db.close()

@app.get("/pve/campaign/{cid}")
def get_camp(cid: str, x_user_id: Optional[str] = Header(None)):
    db = DB()
    try: return camp_map(secure(cid, x_user_id, db), db)
    finally: db.close()

@app.put("/pve/campaign/{cid}")
def save_camp(cid: str, x_user_id: Optional[str] = Header(None)):
    db = DB()
    try:
        c = secure(cid, x_user_id, db)
        # Spec US5: can only save at inn or between rooms
        if c.status == "in_battle": raise HTTPException(403, "cannot exit during a battle")
        c.updated_at = datetime.utcnow(); db.commit()
        return {"message": "saved"}
    finally: db.close()

@app.delete("/pve/campaign/{cid}")
def abandon_camp(cid: str, x_user_id: Optional[str] = Header(None)):
    db = DB()
    try:
        c = secure(cid, x_user_id, db)
        if c.status == "in_battle": raise HTTPException(403, "cannot abandon during a battle")
        try: httpx.post(f"{AUTH_URL}/internal/campaign/clear",
                        json={"userId":x_user_id}, timeout=3)
        except Exception: pass
        db.delete(c); db.commit(); return {"message": "campaign abandoned"}
    finally: db.close()

@app.post("/pve/campaign/{cid}/next-room")
def next_room(cid: str, x_user_id: Optional[str] = Header(None)):
    db = DB()
    try:
        c = secure(cid, x_user_id, db)
        if c.status == "in_battle": raise HTTPException(409, "currently in battle")
        if c.current_room >= 30: raise HTTPException(409, "campaign complete  call /complete")
        c.current_room += 1
        cum = sum(h.level for h in c.heroes)
        is_battle = random.random() < battle_chance(cum)
        room = Room(id=str(uuid.uuid4()), campaign_id=c.id,
                    room_number=c.current_room, room_type="battle" if is_battle else "inn")
        db.add(room); db.flush()
        battle_id = None
        if is_battle:
            battle_id = _start_battle(c, db)
            c.status = "in_battle"; c.active_battle_id = battle_id
        else:
            revival_info = _enter_inn(c, room, db)
            room.revival_log = json.dumps(revival_info)
            c.status = "at_inn"; c.last_inn_room = c.current_room
        db.commit()
        return {"roomNumber":c.current_room,"roomType":"battle" if is_battle else "inn","battleId":battle_id}
    finally: db.close()

@app.get("/pve/campaign/{cid}/party")
def get_party(cid: str, x_user_id: Optional[str] = Header(None)):
    db = DB()
    try: return {"party":[hero_map(h) for h in secure(cid,x_user_id,db).heroes]}
    finally: db.close()

@app.post("/pve/campaign/{cid}/party/hero/{hid}/level-up")
def level_up(cid: str, hid: str, body: dict, x_user_id: Optional[str] = Header(None)):
    db = DB()
    try:
        secure(cid, x_user_id, db)
        h = db.query(Hero).filter_by(id=hid).first()
        if not h: raise HTTPException(404, "hero not found")
        cls = body.get("classChoice", h.hero_class).lower()
        if cls not in CLASSES: raise HTTPException(400, "invalid class")
        try:
            info = _apply_level_up(h, cls, db)
            db.commit()
            return {**hero_map(h), "levelUpInfo": info}
        except ValueError as e:
            raise HTTPException(400, str(e))
    finally: db.close()

@app.get("/pve/campaign/{cid}/inventory")
def get_inv(cid: str, x_user_id: Optional[str] = Header(None)):
    db = DB()
    try:
        c = secure(cid, x_user_id, db)
        items = db.query(InvItem).filter_by(campaign_id=cid).all()
        return {"gold":c.gold,"items":[{"itemId":i.id,"itemType":i.item_type,
                "quantity":i.quantity,"effect":ITEM_EFFECT.get(i.item_type,"")} for i in items]}
    finally: db.close()

@app.post("/pve/campaign/{cid}/inventory/give")
def give_item(cid: str, body: dict, x_user_id: Optional[str] = Header(None)):
    db = DB()
    try:
        secure(cid, x_user_id, db)
        item = db.query(InvItem).filter_by(id=body.get("itemId")).first()
        hero = db.query(Hero).filter_by(id=body.get("heroId")).first()
        if not item: raise HTTPException(404,"item not found")
        if not hero: raise HTTPException(404,"hero not found")
        if hero.dead and item.item_type != "elixir": raise HTTPException(400,"hero is dead; use elixir")
        if item.item_type == "elixir":
            hero.dead = False; hero.current_health = hero.health; hero.current_mana = hero.mana
        else:
            if item.item_type in ITEM_HP:
                hero.current_health = min(hero.health, hero.current_health + ITEM_HP[item.item_type])
            if item.item_type in ITEM_MANA:
                hero.current_mana = min(hero.mana, hero.current_mana + ITEM_MANA[item.item_type])
        if item.quantity <= 1: db.delete(item)
        else: item.quantity -= 1
        db.commit(); return hero_map(hero)
    finally: db.close()

@app.get("/pve/campaign/{cid}/inn")
def get_inn(cid: str, x_user_id: Optional[str] = Header(None)):
    db = DB()
    try:
        c = secure(cid, x_user_id, db)
        if c.status != "at_inn": raise HTTPException(403, "not at an inn")
        room = db.query(Room).filter_by(campaign_id=cid, room_number=c.current_room).first()
        if not room: raise HTTPException(404, "room not found")
        avail_heroes = [{"innHeroId":h.id,"heroClass":h.hero_class,"level":h.level,
                         "attack":h.attack,"defense":h.defense,"health":h.health,
                         "mana":h.mana,"recruitCost":h.recruit_cost}
                        for h in room.inn_heroes if not h.recruited]
        avail_items = [{"itemType":i.item_type,"cost":i.cost,
                        "effect":ITEM_EFFECT.get(i.item_type,""),"quantity":i.quantity}
                       for i in room.inn_items if not i.purchased]
        revival_log = json.loads(room.revival_log or "[]")
        return {"innId":room.id,"gold":c.gold,
                "revivalLog":revival_log,                       # spec US4: show who was healed
                "party":[hero_map(h) for h in c.heroes],
                "availableHeroes":avail_heroes,"availableItems":avail_items}
    finally: db.close()

@app.post("/pve/campaign/{cid}/inn/purchase")
def purchase(cid: str, body: dict, x_user_id: Optional[str] = Header(None)):
    db = DB()
    try:
        c = secure(cid, x_user_id, db)
        itype = body.get("itemType")
        cost = ITEM_COST.get(itype, 0)
        if c.gold < cost: raise HTTPException(400, "not enough gold")
        room = db.query(Room).filter_by(campaign_id=cid, room_number=c.current_room).first()
        inn_item = next((i for i in room.inn_items if i.item_type==itype and not i.purchased), None)
        if not inn_item: raise HTTPException(400, "out of stock")
        inn_item.purchased = True; c.gold -= cost
        existing = db.query(InvItem).filter_by(campaign_id=cid, item_type=itype).first()
        if existing: existing.quantity += 1
        else: db.add(InvItem(id=str(uuid.uuid4()), campaign_id=cid, item_type=itype, quantity=1))
        db.commit()
        return {"itemType":itype,"cost":cost,"remainingGold":c.gold}
    finally: db.close()

@app.post("/pve/campaign/{cid}/inn/recruit")
def recruit(cid: str, body: dict, x_user_id: Optional[str] = Header(None)):
    db = DB()
    try:
        c = secure(cid, x_user_id, db)
        if len(c.heroes) >= 5: raise HTTPException(400, "party full (max 5)")
        ih = db.query(InnHero).filter_by(id=body.get("innHeroId")).first()
        if not ih or ih.recruited: raise HTTPException(404, "hero not available")
        if c.gold < ih.recruit_cost: raise HTTPException(400, "not enough gold")
        c.gold -= ih.recruit_cost; ih.recruited = True
        h = Hero(id=str(uuid.uuid4()), campaign_id=c.id, hero_class=ih.hero_class,
                 **{f"{ih.hero_class}_levels": max(0, ih.level - 1)},
                 level=ih.level, attack=ih.attack, defense=ih.defense,
                 health=ih.health, mana=ih.mana,
                 current_health=ih.health, current_mana=ih.mana)
        db.add(h); db.commit()
        return {"heroId":h.id,"heroClass":h.hero_class,"level":h.level,"remainingGold":c.gold}
    finally: db.close()

@app.post("/pve/campaign/{cid}/battle/result")
def process_battle_result(cid: str, x_user_id: Optional[str] = Header(None)):
    """Process battle end: award/deduct exp+gold, return detailed results."""
    db = DB()
    try:
        c = secure(cid, x_user_id, db)
        won = False; result = {}
        try:
            r = httpx.get(f"{BATTLE_URL}/battle/{c.active_battle_id}/result", timeout=5)
            result = r.json()
            won = result.get("winner") == "attacker"
        except Exception: pass

        # Gather enemies from battle units
        enemies = result.get("defenderUnits", [])
        total_enemy_levels = sum(u.get("level",1) for u in enemies)

        hero_results = []
        total_gold = 0
        gold_lost = 0
        if won:
            # Exp(L) = 50*L per enemy, divided among survivors
            total_exp = sum(100 * u.get("level",1) for u in enemies)  # generous reward
            total_gold = sum(100 * u.get("level",1) for u in enemies)
            # Spec: exp split among heroes still standing (current_health > 1)
            survivors = [h for h in c.heroes if h.current_health > 1]
            if not survivors: survivors = [h for h in c.heroes if not h.dead]  # fallback
            per_hero_exp = total_exp // len(survivors) if survivors else 0
            for h in survivors:
                old_exp = h.experience
                h.experience += per_hero_exp
                hero_results.append({"heroId":h.id,"heroClass":h.hero_class,
                                     "expGained":per_hero_exp,
                                     "canLevelUp": h.experience >= exp_needed(h.level)})
            c.gold += total_gold
        else:
            # Spec: lose 10% gold, 30% current-level exp, return to last inn
            # Bug 5 fix: capture gold_lost before subtracting so goldDelta is exact
            gold_lost = int(c.gold * 0.10)
            c.gold = max(0, c.gold - gold_lost)
            for h in c.heroes:
                exp_lost = int(h.experience * 0.30)
                h.experience = max(0, h.experience - exp_lost)
                hero_results.append({"heroId":h.id,"heroClass":h.hero_class,
                                     "expLost": exp_lost, "canLevelUp": False})
            # Return to last inn room
            c.current_room = c.last_inn_room if c.last_inn_room > 0 else 0
            # Revive party at last inn
            for h in c.heroes:
                h.dead = False; h.current_health = h.health; h.current_mana = h.mana

        c.status = "in_progress"; c.active_battle_id = None
        db.commit()

        return {
            "playerWon": won, "gold": c.gold,
            "goldDelta": total_gold if won else -gold_lost,  # Bug 5 fix: exact value
            "heroResults": hero_results,
            "returnedToRoom": c.current_room if not won else None,
            "party": [hero_map(h) for h in c.heroes]
        }
    finally: db.close()

@app.post("/pve/campaign/{cid}/complete")
def complete_campaign(cid: str, body: dict, x_user_id: Optional[str] = Header(None)):
    """Called after room 30. Score is calculated and stored. Party save is optional."""
    db = DB()
    try:
        c = secure(cid, x_user_id, db)
        if c.current_room < 30: raise HTTPException(409, "campaign not complete yet")
        if c.status == "in_battle": raise HTTPException(403, "cannot complete during battle")

        # Score: hero_levels*100 + gold*10 + (item_cost/2)*10 per item
        cum_levels = sum(h.level for h in c.heroes)
        inv = db.query(InvItem).filter_by(campaign_id=cid).all()
        item_score = sum((ITEM_COST.get(i.item_type,0)//2) * 10 * i.quantity for i in inv)
        score = cum_levels * 100 + c.gold * 10 + item_score

        c.status = "completed"; db.commit()

        # Save score to auth service
        try: httpx.post(f"{AUTH_URL}/profile/{x_user_id}/scores",
                        json={"campaignScore":score},
                        headers={"X-User-Id":x_user_id}, timeout=3)
        except Exception: pass

        # Clear active campaign
        try: httpx.post(f"{AUTH_URL}/internal/campaign/clear",
                        json={"userId":x_user_id}, timeout=3)
        except Exception: pass

        return {"campaignScore":score,"cumLevels":cum_levels,"gold":c.gold,
                "itemScore":item_score,
                "party":[hero_map(h) for h in c.heroes],
                "partyCount": len(c.heroes)}
    finally: db.close()

@app.post("/pve/campaign/{cid}/save-party")
def save_party(cid: str, body: dict, x_user_id: Optional[str] = Header(None)):
    """Save party to profile after campaign. Handles the 5-party limit."""
    db = DB()
    try:
        c = secure(cid, x_user_id, db)
        replace_party_id = body.get("replacePartyId")  # if full, client sends which to replace

        # Check party count
        try:
            r = httpx.get(f"{AUTH_URL}/profile/{x_user_id}/parties",
                          headers={"X-User-Id":x_user_id}, timeout=3)
            party_count = len(r.json().get("parties",[]))
        except Exception: party_count = 0

        if party_count >= 5:
            if not replace_party_id:
                # Return 409 with current parties so client can ask user which to replace
                parties_resp = httpx.get(f"{AUTH_URL}/profile/{x_user_id}/parties",
                                         headers={"X-User-Id":x_user_id}, timeout=3)
                return {"needsReplacement": True,
                        "savedParties": parties_resp.json().get("parties",[])}
            # Delete the replaced party first
            try: httpx.delete(f"{AUTH_URL}/profile/{x_user_id}/parties/{replace_party_id}",
                               headers={"X-User-Id":x_user_id}, timeout=3)
            except Exception: pass

        heroes = [{"heroClass":h.hero_class,"level":h.level,"attack":h.attack,
                   "defense":h.defense,"health":h.health,"mana":h.mana} for h in c.heroes]
        try:
            r = httpx.post(f"{AUTH_URL}/profile/{x_user_id}/parties",
                           json={"name": body.get("name","Campaign Party"), "heroes":heroes},
                           headers={"X-User-Id":x_user_id}, timeout=3)
            party_id = r.json().get("partyId")
        except Exception: party_id = None

        return {"partySaved": party_id is not None, "partyId": party_id, "needsReplacement": False}
    finally: db.close()

#  Internal helpers

def _start_battle(c: Campaign, db) -> str:
    alive = [h for h in c.heroes if not h.dead]
    atk = [{"name": display_class(h), "heroClass": h.hero_class,
             "level": h.level, "attack": h.attack, "defense": h.defense,
             "health": h.health, "mana": h.mana,
             "currentHealth": h.current_health, "currentMana": h.current_mana,
             "abilities": get_abilities(h), "passives": get_passives(h)} for h in alive]

    # Enemy level scaling: enemies are capped at the player's average level
    # so the player can always win. Small downward variance adds variety.
    player_avg = max(1, sum(h.level for h in c.heroes) // max(1, len(c.heroes)))
    # Never more enemies than living party members
    n_enemies  = random.randint(1, len(alive))
    levels = []
    for _ in range(n_enemies):
        variance = random.randint(-2, 0)   # only go equal or lower, never higher
        lv = max(1, min(player_avg, player_avg + variance))
        levels.append(lv)

    # Enemy defense must be strictly less than the weakest attacker's attack
    # so the player is always guaranteed to deal at least 1 damage.
    min_player_attack = min(h.attack for h in alive)
    max_enemy_defense = max(0, min_player_attack - 1)

    enemy_types = ["Goblin", "Orc", "Skeleton", "Bandit", "Troll", "Wraith", "Golem"]
    dfn = []
    for i, lv in enumerate(levels):
        etype = random.choice(enemy_types)
        raw_defense = 2 + lv
        defense = min(raw_defense, max_enemy_defense)
        dfn.append({
            "name": f"Lv{lv} {etype}",
            "level": lv,
            "attack":  4 + lv * 2,
            "defense": defense,
            "health":  60 + lv * 20,
            "mana": 0,
            "currentHealth": 60 + lv * 20,
            "currentMana": 0,
            "abilities": [],
        })

    try:
        r = httpx.post(f"{BATTLE_URL}/battle",
                       json={"type":"pve","initiatedBy":c.id,"attackerParty":atk,"defenderParty":dfn},
                       timeout=5)
        return r.json().get("battleId", str(uuid.uuid4()))
    except Exception:
        return str(uuid.uuid4())

def _enter_inn(c: Campaign, room: Room, db) -> list:
    """Revive party, record who was healed/revived for the revival log."""
    log = []
    for h in c.heroes:
        entry = {"heroId": h.id, "heroClass": h.hero_class, "displayClass": display_class(h)}
        if h.dead:
            entry["action"] = "revived"
            entry["note"] = "Revived and fully restored"
            h.dead = False; h.current_health = h.health; h.current_mana = h.mana
        else:
            hp_gained  = h.health  - h.current_health
            mana_gained = h.mana  - h.current_mana
            h.current_health = h.health; h.current_mana = h.mana
            entry["action"] = "healed"
            entry["hpRestored"]   = hp_gained
            entry["manaRestored"] = mana_gained
        log.append(entry)

    # Generate inn stock (rooms 1–10 only for hero recruitment)
    items_list = list(ITEM_COST.keys())
    for _ in range(random.randint(2, 4)):
        itype = random.choice(items_list)
        db.add(InnItem(id=str(uuid.uuid4()), room_id=room.id,
                       item_type=itype, cost=ITEM_COST[itype],
                       quantity=random.randint(1, 2)))
    if room.room_number <= 10 and len(c.heroes) < 5:
        cls = random.choice(CLASSES)
        lvl = random.randint(1, 4)
        # A level-N inn hero has had (N-1) level-ups; level 1 = pure BASE_STATS
        n_ups = lvl - 1
        atk = BASE_STATS["attack"] + sum(BASE_GAINS["attack"] + CLASS_BONUS.get(cls,{}).get("attack",0) for _ in range(n_ups))
        dfs = BASE_STATS["defense"] + sum(BASE_GAINS["defense"] + CLASS_BONUS.get(cls,{}).get("defense",0) for _ in range(n_ups))
        hp  = BASE_STATS["health"] + sum(BASE_GAINS["health"] + CLASS_BONUS.get(cls,{}).get("health",0) for _ in range(n_ups))
        mp  = BASE_STATS["mana"] + sum(BASE_GAINS["mana"] + CLASS_BONUS.get(cls,{}).get("mana",0) for _ in range(n_ups))
        db.add(InnHero(id=str(uuid.uuid4()), room_id=room.id, hero_class=cls, level=lvl,
                       attack=atk, defense=dfs, health=hp, mana=mp,
                       recruit_cost=0 if lvl==1 else 200*lvl))
    return log

if __name__ == "__main__":
    import uvicorn; uvicorn.run("main:app", host="0.0.0.0", port=8082)
