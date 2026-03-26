# Legends of Sword and Wand

Python · FastAPI · Uvicorn · SQLite

## Services

| Service        | Port | File                        |
|----------------|------|-----------------------------|
| Gateway        | 8080 | `gateway/main.py`           |
| Auth Service   | 8081 | `auth-service/main.py`      |
| PvE Service    | 8082 | `pve-service/main.py`       |
| Battle Service | 8083 | `battle-service/main.py`    |
| PvP Service    | 8084 | `pvp-service/main.py`       |

## Gang of Four Patterns (all in `battle-service/main.py`)

| Pattern         | Class(es)                                      |
|-----------------|------------------------------------------------|
| Strategy        | `ActionStrategy`, `AttackStrategy`, `DefendStrategy`, `WaitStrategy`, `CastStrategy` |
| Template Method | `AbstractAbility.execute()` — fixed skeleton   |
| Factory Method  | `AbilityFactory.create()`                      |
| Observer        | `BattleEventBus`, `BattleListener`, `BattleCompletionListener` |
| Builder         | `BattleStateBuilder`                           |
| Decorator       | `SpecializationBonus` wraps `BaseStats`        |

## Running

```bash
docker compose up --build
```

Open `ui/index.html` in a browser. The gateway listens on `http://localhost:8080`.

## Running tests locally

```bash
cd auth-service   && pip install -r requirements.txt && pytest test_main.py -v
cd battle-service && pip install -r requirements.txt && pytest test_main.py -v
cd pve-service    && pip install -r requirements.txt && pytest test_main.py -v
cd pvp-service    && pip install -r requirements.txt && pytest test_main.py -v
cd gateway        && pip install -r requirements.txt && pytest test_main.py -v
```

## API docs

Each service exposes Swagger UI at `/docs` when running.
