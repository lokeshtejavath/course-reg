# course-reg: Course Waitlist Service (POC)

A FastAPI service for **enrolling, dropping, and FIFO waitlist promotion** in a course offering, with prerequisite checks. It uses SQLite through SQLAlchemy, and transactions are safe under concurrent requests.

- Design spec: [docs/DESIGN.md](docs/DESIGN.md)
- AI prompt and use record: [docs/AI_USAGE.md](docs/AI_USAGE.md)

## Key policies

- **Eligible** = has completed **all** of the offering's prerequisite course codes. Checked on enroll and again at promotion.
- Full offering → eligible learners join a **FIFO waitlist**. Ineligible learners are rejected (`422 PREREQUISITES_NOT_MET`).
- When a seat opens, the **earliest eligible** waiter is promoted. An ineligible waiter is **skipped but retained** in their position.
- **Enroll once, permanently:** after dropping or leaving the waitlist, a learner cannot enroll in that offering again.
- Duplicate or invalid requests return a clear error (`409` / `404` / `422`) and **change nothing**.
- Learners come from [seed/seed.json](seed/seed.json). There's no learner API.

## Setup

Requires Python 3.11+.

```powershell
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
```

## Run the tests

```powershell
python -m pytest -q
```

Expected: `50 passed`. Test files:

| File | Covers |
|---|---|
| `tests/test_enroll.py` | open capacity, full capacity / waitlist, duplicates, prerequisite failure, unknown IDs, re-enroll rejection |
| `tests/test_drop_and_waitlist.py` | FIFO promotion, repeated or invalid drops, skip-but-retain, re-eligibility, leaving the waitlist |
| `tests/test_concurrency.py` | concurrent enrolls never exceed capacity, concurrent duplicates and drops |
| `tests/test_api.py` | HTTP status codes, error envelope, request validation |

## Run the service

```powershell
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000/docs for interactive API docs. On startup the DB is created at `data/course_reg.db` and loaded with 5 learners plus offering `1` (CS201, capacity 2, prerequisite CS101).

```bash
curl -X POST localhost:8000/offerings/1/enrollments -H "Content-Type: application/json" -d '{"learner_id":"L001"}'
curl -X POST localhost:8000/offerings/1/enrollments -H "Content-Type: application/json" -d '{"learner_id":"L002"}'
curl -X POST localhost:8000/offerings/1/enrollments -H "Content-Type: application/json" -d '{"learner_id":"L003"}'  # WAITLISTED, position 1
curl -X DELETE localhost:8000/offerings/1/enrollments/L001                                                        # promotes L003
curl localhost:8000/offerings/1
```

### Configuration

| Env var | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///<repo>/data/course_reg.db` | Any SQLAlchemy URL (a Postgres URL also needs a driver, e.g. `psycopg`) |
| `SEED_FILE` | `seed/seed.json` | Seed file path. Set it empty to disable seeding. |

### Docker

```bash
docker build -t course-reg .
docker run -p 8000:8000 -v course-reg-data:/data course-reg
```

## Project layout

```
app/
  api/routes.py   HTTP endpoints (thin)
  service.py      enrollment / drop / promotion rules (all business logic)
  models.py       SQLAlchemy tables
  db.py           engine + transaction locking
  errors.py       domain errors → HTTP codes
  schemas.py      request/response models
  seed.py         seed loader
  main.py         app factory
tests/            pytest suite
docs/             design spec, AI use record
seed/seed.json    learners + demo offering
```

## Limitations and follow-up risks

- **Not verified on PostgreSQL.** The `SELECT … FOR UPDATE` path is written but untested against a real database. Run `tests/test_concurrency.py` against Postgres before relying on it.
- **Docker image not build-tested.** Docker wasn't available on the dev machine.
- **SQLite allows one writer per database.** Every write is serialized across *all* offerings. Fine for a POC; a production load needs Postgres.
- **Whole-offering lock.** A very popular offering serializes all of its requests. Acceptable at registration volumes, but worth load testing.
- **Eligibility changes are silent.** With seed-only learners, eligibility changes only through direct DB edits. Nothing notifies or re-evaluates the queue until the next seat opens (by design, P5).
- **Retained ineligible learners stay forever.** Under skip-but-retain they hold a queue position until they leave. The waitlist can grow with entries that will never be promoted, and nothing expires them.
- **The free-seat edge case (DESIGN P10)** was an implementation decision, not a confirmed requirement. It needs owner sign-off.
- **No auth.** Anyone can enroll or drop any learner (identity is out of scope).
- **No migrations.** Tables come from `create_all`. Schema changes would need Alembic.
- **Capacity is fixed** once an offering is created. There's no endpoint to change it.
