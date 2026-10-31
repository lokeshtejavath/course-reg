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
- **Learners can be added at any time**, either with `POST /learners` or inline on the enroll call. The server assigns each learner a **UUID** (used in every transaction), and the user picks a unique **username** for reference. Completed courses can be updated with `PUT`. A waitlisted learner keeps their position, and the change applies at the next seat opening.
- [seed/seed.json](seed/seed.json) adds demo learners on startup (matched by username).

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

Expected: `64 passed`. Test files:

| File | Covers |
|---|---|
| `tests/test_enroll.py` | open capacity, full capacity / waitlist, duplicates, prerequisite failure, unknown IDs, re-enroll rejection |
| `tests/test_drop_and_waitlist.py` | FIFO promotion, repeated or invalid drops, skip-but-retain, re-eligibility, leaving the waitlist |
| `tests/test_concurrency.py` | concurrent enrolls never exceed capacity, concurrent duplicates and drops, concurrent inline learner creation |
| `tests/test_api.py` | HTTP status codes, error envelope, request validation, learner create / lookup / update, inline enroll |

## Run the service

```powershell
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000/docs for interactive API docs. On startup the DB is created at `data/course_reg.db` and loaded with 5 learners plus offering `1` (CS201, capacity 2, prerequisite CS101).

```bash
# Create a learner (server returns its UUID "id")
curl -X POST localhost:8000/learners -H "Content-Type: application/json" \
     -d '{"username":"jdoe","name":"Jane Doe","completed_courses":["CS101"]}'

# Enroll by UUID (look up a seeded learner's UUID by username first)
curl localhost:8000/learners/by-username/L001
curl -X POST localhost:8000/offerings/1/enrollments -H "Content-Type: application/json" -d '{"learner_id":"<uuid>"}'

# Or enroll by username in one call, creating the learner if the username is new
curl -X POST localhost:8000/offerings/1/enrollments -H "Content-Type: application/json" \
     -d '{"learner":{"username":"walkin","name":"Walk In","completed_courses":["CS101"]}}'

# Update completed courses (affects eligibility at the next seat opening)
curl -X PUT localhost:8000/learners/<uuid>/completed-courses -H "Content-Type: application/json" -d '{"completed_courses":["CS101"]}'

curl -X DELETE localhost:8000/offerings/1/enrollments/<uuid>   # drop, promotes earliest eligible waiter
curl localhost:8000/offerings/1                               # state, waitlist shows usernames
```

### Endpoints

| Method & path | Purpose |
|---|---|
| `POST /learners` | Create a learner `{username, name, completed_courses}` → `201` with server-generated `id` |
| `GET /learners/{id}` / `GET /learners/by-username/{username}` | Look up a learner |
| `PUT /learners/{id}/completed-courses` | Replace completed courses |
| `POST /offerings` / `GET /offerings/{id}` | Create / read an offering |
| `POST /offerings/{id}/enrollments` | Enroll or waitlist. Body: `{learner_id}` **or** `{learner: {username, name, completed_courses}}` |
| `DELETE /offerings/{id}/enrollments/{learner_id}` | Drop (promotes from the waitlist) |
| `DELETE /offerings/{id}/waitlist/{learner_id}` | Leave the waitlist |

### Configuration

| Env var | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///<repo>/data/course_reg.db` | Any SQLAlchemy URL (a Postgres URL also needs a driver, e.g. `psycopg`) |
| `SEED_FILE` | `seed/seed.json` | Seed file path. Set it empty to disable seeding. |

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

- **SQLite allows one writer per database.** Every write is serialized across *all* offerings. Fine for a POC; a production load needs Postgres.
- **Whole-offering lock.** A very popular offering serializes all of its requests. Acceptable at registration volumes, but worth load testing.
- **Eligibility changes don't trigger promotion.** After `PUT …/completed-courses`, nothing re-evaluates the queue until the next seat opens (by design, P5). A seat can stay free until then if the only eligible waiter just became eligible.
- **Inline enroll with an existing username ignores the supplied details (by design).** The stored learner is used as-is, so a client that sends updated courses inline can still get `PREREQUISITES_NOT_MET`. Update courses with `PUT …/completed-courses` first. Clients should be told this, because it's easy to miss.
- **No learner listing, deletion or rename.** Only create, lookup and course updates exist. Usernames are case-sensitive (`jdoe` ≠ `JDoe`).
- **Schema changed (learners gained `username`).** An existing `data/course_reg.db` from the earlier version won't upgrade itself. Delete it and restart.
- **Retained ineligible learners stay forever.** Under skip-but-retain they hold a queue position until they leave. The waitlist can grow with entries that will never be promoted, and nothing expires them.
- **A free seat can sit empty until the next request.** A waiter who regains eligibility isn't promoted immediately (P5). The seat is filled on the next drop *or* enroll. On an enroll, the re-eligible waiter takes the seat and the newcomer joins the waitlist (DESIGN P10).
- **No auth.** Anyone can enroll or drop any learner (identity is out of scope).
- **No migrations.** Tables come from `create_all`. Schema changes would need Alembic.
- **Capacity is fixed** once an offering is created. There's no endpoint to change it.
