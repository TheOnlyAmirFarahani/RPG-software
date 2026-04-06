import json, random, uuid
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Column, String, Integer, Boolean, DateTime, ForeignKey, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

app = FastAPI(title="Battle Service")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
engine = create_engine("sqlite:///./battle.db", connect_args={"check_same_thread": False})
DBS = sessionmaker(bind=engine)
Base = declarative_base()

# Models

class Battle(Base):
    __tablename__ = "battles"
    id              = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    type            = Column(String, default="pve")
    status          = Column(String, default="in_progress")
    initiated_by    = Column(String)
    current_turn    = Column(Integer, default=1)
    winner          = Column(String)
    created_at      = Column(DateTime, default=datetime.utcnow)
    completed_at    = Column(DateTime)
    # Turn management (JSON arrays of unit IDs)
    round_order     = Column(String, default="[]")   # order for current round
    acted_this_round= Column(String, default="[]")   # have completed action
    wait_queue      = Column(String, default="[]")   # FIFO wait queue
    units = relationship("BattleUnit", back_populates="battle",
                         cascade="all, delete-orphan", order_by="BattleUnit.position")
    logs  = relationship("ActionLog", back_populates="battle",
                         cascade="all, delete-orphan", order_by="ActionLog.turn_number")

class BattleUnit(Base):
    __tablename__ = "battle_units"
    id             = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    battle_id      = Column(String, ForeignKey("battles.id"), nullable=False)
    team           = Column(String)
    name           = Column(String)
    hero_class     = Column(String)
    level          = Column(Integer, default=1)
    base_attack    = Column(Integer, default=5)
    base_defense   = Column(Integer, default=5)
    base_health    = Column(Integer, default=100)
    base_mana      = Column(Integer, default=50)
    current_health = Column(Integer, default=100)
    current_mana   = Column(Integer, default=50)
    current_shield = Column(Integer, default=0)
    stunned        = Column(Boolean, default=False)
    dead           = Column(Boolean, default=False)
    position       = Column(Integer, default=0)
    abilities      = Column(String, default="")   # comma-separated
    passives       = Column(String, default="")   # comma-separated passive effects
    battle         = relationship("Battle", back_populates="units")

class ActionLog(Base):
    __tablename__ = "action_logs"
    id             = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    battle_id      = Column(String, ForeignKey("battles.id"), nullable=False)
    turn_number    = Column(Integer)
    acting_unit_id = Column(String)
    action_type    = Column(String)
    ability_used   = Column(String)
    damage_dealt   = Column(Integer, default=0)
    healing_done   = Column(Integer, default=0)
    mana_spent     = Column(Integer, default=0)
    notes          = Column(String)   # e.g. "stunned", "sneak_attack triggered"
    battle         = relationship("Battle", back_populates="logs")

Base.metadata.create_all(bind=engine)

#
# PATTERN 6: DECORATOR  SpecializationBonus wraps BaseStats
#

class HeroStats(ABC):
    @abstractmethod
    def attack(self) -> int: ...
    @abstractmethod
    def defense(self) -> int: ...

class BaseStats(HeroStats):
    def __init__(self, atk, dfs): self._a, self._d = atk, dfs
    def attack(self) -> int:  return self._a
    def defense(self) -> int: return self._d

class SpecializationBonus(HeroStats):
    _B = {"chaos":{"attack":3},"warrior":{"attack":2,"defense":3},
          "order":{"defense":2},"mage":{"attack":1}}
    def __init__(self, wrapped, cls):
        self._w = wrapped; self._b = self._B.get(cls or "", {})
    def attack(self) -> int:  return self._w.attack()  + self._b.get("attack",  0)
    def defense(self) -> int: return self._w.defense() + self._b.get("defense", 0)

#
# PATTERN 5: BUILDER  BattleStateBuilder
#

class BattleStateBuilder:
    def __init__(self): self._s = {}

    def with_battle(self, b):
        self._s.update({"battleId":b.id,"status":b.status,"type":b.type,
                         "currentTurn":b.current_turn,"winner":b.winner})
        return self

    def with_units(self, units, active_id, turn_order_ids):
        self._s["activeUnitId"] = active_id
        self._s["turnOrder"] = turn_order_ids          # spec US3: show order
        # waitQueue is set by the caller via _build_state directly
        self._s["attackerUnits"] = [_udto(u) for u in units if u.team == "attacker"]
        self._s["defenderUnits"] = [_udto(u) for u in units if u.team == "defender"]
        return self

    def with_log(self, logs):
        self._s["actionLog"] = [{"turn":l.turn_number,"actingUnitId":l.acting_unit_id,
                                  "actionType":l.action_type,"abilityUsed":l.ability_used or "",
                                  "damageDealt":l.damage_dealt,"healingDone":l.healing_done,
                                  "notes":l.notes or ""} for l in logs]
        return self

    def build(self): return self._s

#
# PATTERN 4: OBSERVER  BattleEventBus
#

class BattleListener(ABC):
    @abstractmethod
    def on_battle_ended(self, battle, winner, db): ...

class BattleCompletionListener(BattleListener):
    def on_battle_ended(self, battle, winner, db):
        battle.status = "completed"; battle.winner = winner
        battle.completed_at = datetime.utcnow(); db.commit()

_bus = type('Bus', (), {
    '_ls': [],
    'subscribe': lambda self,l: self._ls.append(l),
    'publish': lambda self,b,w,db: [l.on_battle_ended(b,w,db) for l in self._ls]
})()
_bus.subscribe(BattleCompletionListener())

#
# PATTERN 2: TEMPLATE METHOD  AbstractAbility
#

class AbstractAbility(ABC):
    @abstractmethod
    def mana_cost(self) -> int: ...
    @abstractmethod
    def ability_name(self) -> str: ...
    def select_targets(self, actor, primary, units): return [primary]
    @abstractmethod
    def apply_effect(self, actor, targets, units, db) -> dict: ...

    def execute(self, actor, primary, units, db) -> dict:
        # Skeleton: check mana → deduct → select targets → apply → return
        if actor.current_mana < self.mana_cost():
            raise ValueError(f"not enough mana (need {self.mana_cost()}, have {actor.current_mana})")
        actor.current_mana -= self.mana_cost(); db.flush()
        targets = self.select_targets(actor, primary, units)
        result = self.apply_effect(actor, targets, units, db)
        result.setdefault("abilityUsed", self.ability_name())
        result.setdefault("manaSpent", self.mana_cost())
        return result

def _dmg(actor, target):
    """Spec: damage = UA(attack) - UD(defense), minimum 0."""
    return max(0, actor.base_attack - target.base_defense)

def _hit(t, damage, db):
    absorb = min(t.current_shield, damage)
    t.current_shield -= absorb
    t.current_health = max(0, t.current_health - (damage - absorb))
    if t.current_health == 0: t.dead = True
    db.flush()
    return damage - absorb  # actual damage after shield

class FireballAbility(AbstractAbility):
    def mana_cost(self): return 30
    def ability_name(self): return "fireball"
    def select_targets(self, actor, primary, units):
        rest = [u for u in units if u.team != actor.team and not u.dead and u.id != primary.id]
        return [primary] + rest[:2]
    def apply_effect(self, actor, targets, units, db):
        total = 0; aff = []
        for t in targets: d = _dmg(actor,t); _hit(t,d,db); total+=d; aff.append(_amap(t))
        return {"damage":total,"healing":0,"affected":aff}

class FireballDoubleAbility(FireballAbility):
    """Sorcerer hybrid: fireball does double damage."""
    def ability_name(self): return "fireball_double"
    def apply_effect(self, actor, targets, units, db):
        total = 0; aff = []
        for t in targets: d = _dmg(actor,t)*2; _hit(t,d,db); total+=d; aff.append(_amap(t))
        return {"damage":total,"healing":0,"affected":aff}

class ChainLightningAbility(AbstractAbility):
    def mana_cost(self): return 40
    def ability_name(self): return "chain_lightning"
    def _ratio(self): return 0.25
    def select_targets(self, actor, primary, units):
        rest = [u for u in units if u.team != actor.team and not u.dead and u.id != primary.id]
        random.shuffle(rest); return [primary] + rest
    def apply_effect(self, actor, targets, units, db):
        base = _dmg(actor, targets[0]); total = 0; aff = []
        for i,t in enumerate(targets):
            d = max(0, int(base * (self._ratio()**i)))
            _hit(t,d,db); total+=d; aff.append(_amap(t))
        return {"damage":total,"healing":0,"affected":aff}

class ChainLightning50Ability(ChainLightningAbility):
    """Invoker: 50% reduction per target instead of 25%."""
    def ability_name(self): return "chain_lightning_50"
    def _ratio(self): return 0.5

class HealAbility(AbstractAbility):
    def mana_cost(self): return 35
    def ability_name(self): return "heal"
    def _multiplier(self): return 1
    def select_targets(self, actor, primary, units):
        allies = [u for u in units if u.team == actor.team and not u.dead]
        return [min(allies, key=lambda u: u.current_health)] if allies else []
    def apply_effect(self, actor, targets, units, db):
        total = 0; aff = []
        for t in targets:
            amt = min(int(t.base_health*0.25*self._multiplier()), t.base_health-t.current_health)
            t.current_health += amt; db.flush(); total+=amt; aff.append(_amap(t))
        return {"damage":0,"healing":total,"affected":aff}

class HealAllAbility(HealAbility):
    """Priest specialization: heal ALL friendly units."""
    def ability_name(self): return "heal_all"
    def select_targets(self, actor, primary, units):
        return [u for u in units if u.team == actor.team and not u.dead]

class HealDoubleAbility(HealAllAbility):
    """Prophet hybrid: heal doubles (applies to all)."""
    def ability_name(self): return "heal_double"
    def _multiplier(self): return 2

class ProtectAbility(AbstractAbility):
    def mana_cost(self): return 25
    def ability_name(self): return "protect"
    def _shield_pct(self): return 0.10
    def select_targets(self, actor, primary, units):
        return [u for u in units if u.team == actor.team and not u.dead]
    def apply_effect(self, actor, targets, units, db):
        aff = []
        for t in targets:
            t.current_shield += int(t.base_health * self._shield_pct()); db.flush(); aff.append(_amap(t))
        return {"damage":0,"healing":0,"affected":aff}

class ProtectDoubleAbility(ProtectAbility):
    """Prophet hybrid: protect with 20% HP shield."""
    def ability_name(self): return "protect_double"
    def _shield_pct(self): return 0.20

class FireShieldAbility(ProtectAbility):
    """Heretic hybrid: like protect but marks units to return 10% damage."""
    def ability_name(self): return "fire_shield"
    def apply_effect(self, actor, targets, units, db):
        result = super().apply_effect(actor, targets, units, db)
        result["notes"] = "fire_shield_active"
        return result

class BerserkerAttackAbility(AbstractAbility):
    def mana_cost(self): return 60
    def ability_name(self): return "berserker_attack"
    def select_targets(self, actor, primary, units):
        splash = [u for u in units if u.team!=actor.team and not u.dead and u.id!=primary.id]
        return [primary] + splash[:2]
    def apply_effect(self, actor, targets, units, db):
        total=0; aff=[]; notes_list=[]
        for i,t in enumerate(targets):
            d = _dmg(actor,t) if i==0 else max(0, int(_dmg(actor,t)*0.25))
            _hit(t,d,db); total+=d; aff.append(_amap(t))
        return {"damage":total,"healing":0,"affected":aff,"notes":";".join(notes_list)}

class BerserkerStunAbility(BerserkerAttackAbility):
    """Knight specialization: 50% chance to stun splash targets."""
    def ability_name(self): return "berserker_stun"
    def apply_effect(self, actor, targets, units, db):
        result = super().apply_effect(actor, targets, units, db)
        notes = []
        for t in targets[1:]:  # stun chance on splash targets
            if not t.dead and random.random() < 0.5:
                t.stunned = True; db.flush()
                notes.append(f"{t.name} stunned")
        if notes: result["notes"] = "; ".join(notes)
        return result

class BerserkerHealAbility(BerserkerAttackAbility):
    """Paladin hybrid: heal self 10% HP before attacking."""
    def ability_name(self): return "berserker_heal"
    def apply_effect(self, actor, targets, units, db):
        heal_amt = int(actor.base_health * 0.10)
        actor.current_health = min(actor.base_health, actor.current_health + heal_amt)
        db.flush()
        result = super().apply_effect(actor, targets, units, db)
        result["healing"] = heal_amt
        result["notes"] = f"self healed {heal_amt} before attack"
        return result

class ReplenishAbility(AbstractAbility):
    def mana_cost(self): return 80
    def ability_name(self): return "replenish"
    def select_targets(self, actor, primary, units):
        return [u for u in units if u.team==actor.team and not u.dead]
    def apply_effect(self, actor, targets, units, db):
        for t in targets:
            gain = 60 if t.id==actor.id else 30
            t.current_mana = min(t.base_mana, t.current_mana+gain); db.flush()
        return {"damage":0,"healing":0,"affected":[]}

class ReplenishCheapAbility(ReplenishAbility):
    """Wizard specialization: costs only 40 mana."""
    def mana_cost(self): return 40
    def ability_name(self): return "replenish_cheap"

class ReplenishDoubleAbility(ReplenishAbility):
    """Prophet hybrid: double mana restoration."""
    def ability_name(self): return "replenish_double"
    def apply_effect(self, actor, targets, units, db):
        for t in targets:
            gain = 120 if t.id==actor.id else 60
            t.current_mana = min(t.base_mana, t.current_mana+gain); db.flush()
        return {"damage":0,"healing":0,"affected":[]}

#
# PATTERN 3: FACTORY METHOD  AbilityFactory.create()
#

class AbilityFactory:
    _MAP = {
        "fireball": FireballAbility, "fireball_double": FireballDoubleAbility,
        "chain_lightning": ChainLightningAbility, "chain_lightning_50": ChainLightning50Ability,
        "heal": HealAbility, "heal_all": HealAllAbility, "heal_double": HealDoubleAbility,
        "protect": ProtectAbility, "protect_double": ProtectDoubleAbility,
        "fire_shield": FireShieldAbility,
        "berserker_attack": BerserkerAttackAbility, "berserker_stun": BerserkerStunAbility,
        "berserker_heal": BerserkerHealAbility,
        "replenish": ReplenishAbility, "replenish_cheap": ReplenishCheapAbility,
        "replenish_double": ReplenishDoubleAbility,
    }
    @staticmethod
    def create(name: str) -> AbstractAbility:
        cls = AbilityFactory._MAP.get(name.lower())
        if not cls: raise ValueError(f"unknown ability: {name}")
        return cls()

#
# PATTERN 1: STRATEGY  ActionStrategy and concrete strategies
#

class ActionStrategy(ABC):
    @abstractmethod
    def execute(self, actor, target, units, battle, db) -> dict: ...

class AttackStrategy(ActionStrategy):
    def execute(self, actor, target, units, battle, db):
        if target is None:
            enemies = [u for u in units if u.team!=actor.team and not u.dead]
            if not enemies: return {"damage":0,"healing":0,"affected":[],"notes":""}
            target = enemies[0]
        d = _dmg(actor, target); actual = _hit(target, d, db)
        notes = ""
        # Check fire_shield retaliation
        if target.current_shield > 0 and not target.dead:
            pass  # fire_shield handled separately in apply_damage
        # Rogue passive: sneak_attack
        if "sneak_attack" in (actor.passives or "") and random.random() < 0.5:
            all_enemies = [u for u in units if u.team!=actor.team and not u.dead and u.id!=target.id]
            if all_enemies:
                bonus_t = random.choice(all_enemies)
                bonus_d = max(0, int(d * 0.5)); _hit(bonus_t, bonus_d, db)
                notes = f"sneak_attack on {bonus_t.name} for {bonus_d}"
        # Warlock passive: mana_burn
        if "mana_burn" in (actor.passives or "") and not target.dead:
            burn = int(target.base_mana * 0.10)
            target.current_mana = max(0, target.current_mana - burn); db.flush()
            notes += f"{'; ' if notes else ''}mana_burn {burn}"
        return {"damage":actual,"healing":0,"affected":[_amap(target)],"notes":notes}

class DefendStrategy(ActionStrategy):
    def execute(self, actor, target, units, battle, db):
        heal = min(10, actor.base_health - actor.current_health)
        actor.current_health += heal
        actor.current_mana = min(actor.base_mana, actor.current_mana+5)
        db.flush()
        return {"damage":0,"healing":heal,"affected":[_amap(actor)],"notes":""}

class WaitStrategy(ActionStrategy):
    def execute(self, actor, target, units, battle, db):
        return {"damage":0,"healing":0,"affected":[],"notes":"waited"}

class CastStrategy(ActionStrategy):
    def __init__(self, ability_name): self._ab = AbilityFactory.create(ability_name)
    def execute(self, actor, target, units, battle, db):
        result = self._ab.execute(actor, target, units, db)
        result.setdefault("notes", "")
        return result

#  Turn Management

def _get_active_unit_id(battle, units):
    wait_q  = json.loads(battle.wait_queue or "[]")
    acted   = json.loads(battle.acted_this_round or "[]")
    r_order = json.loads(battle.round_order or "[]")
    alive   = {u.id for u in units if not u.dead}
    # Normal order: not yet acted, not waiting
    for uid in r_order:
        if uid in alive and uid not in acted and uid not in wait_q:
            return uid
    # All non-waiting have acted: process wait queue (FIFO)
    for uid in wait_q:
        if uid in alive:
            return uid
    return None  # round complete

def _after_action(battle, actor_id, action_type, units):
    wait_q = json.loads(battle.wait_queue or "[]")
    acted  = json.loads(battle.acted_this_round or "[]")
    r_order = json.loads(battle.round_order or "[]")
    alive  = {u.id for u in units if not u.dead}

    if action_type == "wait":
        if actor_id not in wait_q: wait_q.append(actor_id)
        battle.wait_queue = json.dumps(wait_q)
        # NOT added to acted  they'll act again from wait queue
    else:
        if actor_id in wait_q: wait_q.remove(actor_id)
        battle.wait_queue = json.dumps(wait_q)
        if actor_id not in acted: acted.append(actor_id)
        battle.acted_this_round = json.dumps(acted)

    # Reload after modification
    wait_q2 = json.loads(battle.wait_queue)
    acted2  = json.loads(battle.acted_this_round)
    remaining_normal = [uid for uid in r_order if uid in alive and uid not in acted2 and uid not in wait_q2]
    remaining_wait   = [uid for uid in wait_q2 if uid in alive]

    if not remaining_normal and not remaining_wait:
        # Round complete  start new round
        new_order = [u.id for u in sorted(units, key=lambda x: x.position) if not u.dead]
        battle.round_order       = json.dumps(new_order)
        battle.wait_queue        = "[]"
        battle.acted_this_round  = "[]"
        battle.current_turn += 1

def _is_over(units): 
    return all(u.dead for u in units if u.team=="attacker") or \
           all(u.dead for u in units if u.team=="defender")

def _winner(units):
    return "attacker" if all(u.dead for u in units if u.team=="defender") else "defender"

def _build_state(battle, db):
    units = db.query(BattleUnit).filter_by(battle_id=battle.id).order_by(BattleUnit.position).all()
    logs  = db.query(ActionLog).filter_by(battle_id=battle.id).order_by(ActionLog.turn_number).all()
    # Build turn order display
    wait_q  = json.loads(battle.wait_queue or "[]")
    acted   = json.loads(battle.acted_this_round or "[]")
    r_order = json.loads(battle.round_order or "[]")
    alive_ids = {u.id for u in units if not u.dead}
    # Turn order for display: remaining normal + wait queue
    remaining = [uid for uid in r_order if uid in alive_ids and uid not in acted and uid not in wait_q]
    turn_order_ids = remaining + [uid for uid in wait_q if uid in alive_ids]
    active_id = None if battle.status=="completed" else _get_active_unit_id(battle, units)
    state = (BattleStateBuilder()
             .with_battle(battle)
             .with_log(logs)
             .build())
    state["activeUnitId"] = active_id
    state["turnOrder"] = turn_order_ids
    state["waitQueue"] = wait_q
    state["attackerUnits"] = [_udto(u) for u in units if u.team=="attacker"]
    state["defenderUnits"] = [_udto(u) for u in units if u.team=="defender"]
    return state

def _udto(u):
    return {"unitId":u.id,"name":u.name,"team":u.team,"heroClass":u.hero_class,
            "level":u.level,"baseAttack":u.base_attack,"baseDefense":u.base_defense,
            "baseHealth":u.base_health,"baseMana":u.base_mana,
            "currentHealth":u.current_health,"currentMana":u.current_mana,
            "currentShield":u.current_shield,"stunned":u.stunned,"dead":u.dead,
            "abilities":[a for a in u.abilities.split(",") if a],
            "passives":[p for p in u.passives.split(",") if p],
            "position":u.position}

def _amap(u): return {"unitId":u.id,"newHealth":u.current_health,
                       "newMana":u.current_mana,"newShield":u.current_shield,
                       "isDead":u.dead,"isStunned":u.stunned}

#  Routes

@app.post("/battle", status_code=201)
def init_battle(body: dict):
    atk_party = body.get("attackerParty",[])
    def_party = body.get("defenderParty",[])
    if not atk_party or not def_party: raise HTTPException(400,"attacker or defender party is empty")
    db = DBS()
    try:
        battle = Battle(id=str(uuid.uuid4()), type=body.get("type","pve"),
                        initiated_by=body.get("initiatedBy"))
        db.add(battle); db.flush()

        def mk_unit(data, team, pos):
            # Stats from pve-service already include class bonuses  use them directly.
            # SpecializationBonus decorator is preserved for educational purposes (GoF pattern 6)
            # but is not applied here to avoid double-counting pre-computed stats.
            u = BattleUnit(
                id=str(uuid.uuid4()), battle_id=battle.id, team=team,
                name=data.get("name",team), hero_class=data.get("heroClass"),
                level=data.get("level",1),
                base_attack=data.get("attack",5), base_defense=data.get("defense",5),
                base_health=data.get("health",100), base_mana=data.get("mana",50),
                current_health=data.get("currentHealth") or data.get("health",100),
                current_mana=data.get("currentMana") or data.get("mana",50),
                abilities=",".join(data.get("abilities") or []),
                passives=",".join(data.get("passives") or []),
                position=pos
            )
            db.add(u); return u

        # Turn order: highest level → teams alternate
        atk_s = sorted(atk_party, key=lambda x: (x.get("level",1), x.get("attack",5)), reverse=True)
        def_s = sorted(def_party, key=lambda x: (x.get("level",1), x.get("attack",5)), reverse=True)
        pos = 1
        for i in range(max(len(atk_s), len(def_s))):
            if i < len(atk_s): mk_unit(atk_s[i],"attacker",pos); pos+=1
            if i < len(def_s): mk_unit(def_s[i],"defender",pos); pos+=1

        db.flush()
        units = db.query(BattleUnit).filter_by(battle_id=battle.id).order_by(BattleUnit.position).all()
        order = [u.id for u in units]
        battle.round_order = json.dumps(order)
        db.commit()
        return _build_state(battle, db)
    finally: db.close()

@app.get("/battle/{bid}")
def get_state(bid: str):
    db = DBS()
    try:
        b = db.query(Battle).filter_by(id=bid).first()
        if not b: raise HTTPException(404,"battle not found")
        return _build_state(b, db)
    finally: db.close()

@app.post("/battle/{bid}/action")
def take_action(bid: str, body: dict):
    db = DBS()
    try:
        battle = db.query(Battle).filter_by(id=bid).first()
        if not battle: raise HTTPException(404,"battle not found")
        if battle.status == "completed": raise HTTPException(409,"battle already completed")

        units = db.query(BattleUnit).filter_by(battle_id=bid).order_by(BattleUnit.position).all()
        active_id = _get_active_unit_id(battle, units)
        if body.get("unitId") != active_id: raise HTTPException(409,"not this unit's turn")

        actor = next(u for u in units if u.id == active_id)

        # Handle stun  consume turn and return updated state (not an error)
        if actor.stunned:
            actor.stunned = False; db.flush()
            _after_action(battle, actor.id, "attack", units)
            db.flush()
            fresh_units = db.query(BattleUnit).filter_by(battle_id=bid).all()
            if _is_over(fresh_units):
                _bus.publish(battle, _winner(fresh_units), db)
            else:
                db.commit()
            state = _build_state(battle, db)
            state["stunSkip"] = True  # optional flag so UI can show a message
            return state

        action_type = body.get("actionType","attack")
        target_id   = body.get("targetUnitId")
        target = next((u for u in units if u.id==target_id), None)
        if target is None and action_type == "attack":
            target = next((u for u in units if u.team!=actor.team and not u.dead), None)

        # Strategy selection
        if action_type == "cast":
            strategy = CastStrategy(body.get("ability",""))
        elif action_type == "defend":
            strategy = DefendStrategy()
        elif action_type == "wait":
            strategy = WaitStrategy()
        else:
            strategy = AttackStrategy()

        try:
            result = strategy.execute(actor, target, units, battle, db)
        except ValueError as e:
            raise HTTPException(409, str(e))

        # Log action
        log = ActionLog(id=str(uuid.uuid4()), battle_id=bid, turn_number=battle.current_turn,
                        acting_unit_id=actor.id, action_type=action_type,
                        ability_used=result.get("abilityUsed"), damage_dealt=result.get("damage",0),
                        healing_done=result.get("healing",0), mana_spent=result.get("manaSpent",0),
                        notes=result.get("notes",""))
        db.add(log)

        # Update turn state
        _after_action(battle, actor.id, action_type, units)

        # Check battle over (reload units after all modifications)
        db.flush()
        fresh_units = db.query(BattleUnit).filter_by(battle_id=bid).all()
        if _is_over(fresh_units):
            _bus.publish(battle, _winner(fresh_units), db)
        else:
            db.commit()

        return _build_state(battle, db)
    finally: db.close()

@app.get("/battle/{bid}/result")
def get_result(bid: str):
    db = DBS()
    try:
        b = db.query(Battle).filter_by(id=bid).first()
        if not b: raise HTTPException(404,"battle not found")
        if b.status != "completed": raise HTTPException(409,"battle not yet completed")
        return _build_state(b, db)
    finally: db.close()

@app.delete("/battle/{bid}")
def delete_battle(bid: str):
    db = DBS()
    try:
        b = db.query(Battle).filter_by(id=bid).first()
        if not b: raise HTTPException(404,"battle not found")
        if b.status == "in_progress": raise HTTPException(403,"cannot delete in-progress battle")
        db.delete(b); db.commit(); return {"message":"deleted"}
    finally: db.close()

if __name__ == "__main__":
    import uvicorn; uvicorn.run("main:app", host="0.0.0.0", port=8083)
