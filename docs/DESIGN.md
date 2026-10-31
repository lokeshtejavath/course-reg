# Design Specification: Course Waitlist Service

## 1. Scope

This service covers one slice of course registration: **enroll, drop, and FIFO waitlist promotion** for a course offering, plus leaving the waitlist, reading an offering's state, creating an offering, and creating learners and updating their completed courses at any time.

The following are out of scope: frontend, course catalog, payments, auth/identity, multiple terms, deployment, and a configurable policy engine.

The supplied starter repo had no code or data model (only `README.md` and `.gitignore`). The brief's fallback applies, so this is a minimal service.

## 2. Confirmed policies

The project owner confirmed each of these before implementation began.

| # | Topic | Policy |
|---|---|---|
| P1 | Prerequisite rule | An offering lists required course codes. A learner is **eligible** if their completed courses include **all** of them. Codes are trimmed and compared case-insensitively. An offering with no prerequisites accepts everyone. |
| P2 | When eligibility is checked | On every enroll request, and again for each waitlisted learner whenever seats open (eligibility can change while waiting). |
| P3 | Ineligible learner at enroll | Rejected with `PREREQUISITES_NOT_MET`. They are neither enrolled nor waitlisted, and no record is created. |
| P4 | Ineligible learner at the front of the queue | **Skipped but retained.** They are not promoted and keep their original queue position. The next eligible learner is promoted instead. |
| P5 | Learner becomes eligible again | Nothing happens immediately. They are considered at their **original position** the next time a seat opens. |
| P6 | Free seat, only ineligible learners waiting | A new eligible enroll request takes the seat directly. |
| P7 | "Enroll only once" | **Permanent.** A learner has at most one registration per offering. After `DROPPED` (drop) or `WITHDRAWN` (left the waitlist), any later enroll for that offering is rejected with `REENROLLMENT_NOT_ALLOWED`. |
| P8 | Duplicate requests | `409` with a machine-readable code (`ALREADY_ENROLLED`, `ALREADY_WAITLISTED`) and the learner's current status. Nothing is written. |
| P9 | Learner data *(revised)* | Learners can be created **at any time**, either via `POST /learners` or inline on enroll. The server generates a **UUID**, which is the only identifier used in transactions and API paths. The user supplies a unique **username** for reference and lookup. `PUT /learners/{id}/completed-courses` replaces completed courses. The seed file still adds demo learners (insert-if-missing by username). *Originally: seed file only, no learner API.* |
| P11 | Inline enroll *(new)* | The enroll body carries **exactly one** of `learner_id` (existing learner) or `learner` (`{username, name, completed_courses}`). For `learner`: if the username is new, the learner is created and enrolled/waitlisted **in one transaction**, so a rejected enrollment also discards the new learner. If the username exists, that learner is used **as stored** and the supplied name and completed courses are ignored, so the request can still fail with `PREREQUISITES_NOT_MET`. Courses change only via `PUT …/completed-courses` *(confirmed by owner)*. The response's `learner_created` flag says which happened. |
| P10 | Free seat + re-eligible waiter *(confirmed by owner)* | When a seat is free *and* a waitlisted learner is eligible again, a new enroll request first promotes eligible waiters in FIFO order. **The newcomer then joins the waitlist** if no seat remains. This keeps FIFO fairness among eligible learners. It only happens when a waiter regains eligibility (via `PUT …/completed-courses`) while a seat sits free (P5 + P6). Covered by `test_reeligible_waiter_takes_free_seat_before_new_learner`. |

## 3. Architecture

```
HTTP (FastAPI, app/api/routes.py)      validate input, map to/from schemas
        │
RegistrationService (app/service.py)   all business rules; one transaction per call
        │
SQLAlchemy ORM (app/models.py)         SQLite by default; DATABASE_URL selects another DB
```

- Routes are thin and contain no rules. The service has no HTTP dependency, so it is unit-tested directly and HTTP is tested separately.
- Domain errors (`app/errors.py`) carry a stable `code` and `http_status`. A single exception handler turns them into `{"error": {"code", "message", "details"}}`. Request validation errors use the same envelope with code `INVALID_REQUEST`.
- Handlers are synchronous `def`. FastAPI runs them in a threadpool, which gives real concurrent requests against the DB.

## 4. Data model

| Table | Key columns | Notes |
|---|---|---|
| `learners` | `id` (UUID string PK, server-generated), `username` (unique), `name` | Created via the API or the seed file. Registrations reference `id` only. |
| `learner_completed_courses` | (`learner_id`, `course_code`) PK | |
| `offerings` | `id` (int PK), `code` (unique), `title`, `capacity` (CHECK ≥ 1) | |
| `offering_prerequisites` | (`offering_id`, `course_code`) PK | |
| `registrations` | `id` (int PK), `offering_id`, `learner_id`, `status`, timestamps; **UNIQUE(`offering_id`, `learner_id`)** | `status` ∈ `ENROLLED`, `WAITLISTED`, `DROPPED`, `WITHDRAWN` |

One row per (offering, learner) holds the whole lifecycle: `ENROLLED → DROPPED`, `WAITLISTED → ENROLLED` (promotion), `WAITLISTED → WITHDRAWN`. The unique constraint is the database-level guarantee behind P7 and P8. Rows are never deleted, so history stays available for audit.

**FIFO key:** a `WAITLISTED` row is inserted at the moment the learner joins the queue, so ordering `WAITLISTED` rows by the monotonic `id` gives join order. Retained ineligible learners (P4) keep their row and their `id`, and therefore their position.

## 5. API

| Method & path | Success | Errors |
|---|---|---|
| `POST /learners` `{username, name, completed_courses[]}` | `201 {id, username, name, completed_courses}`; `id` is server-generated and any client-sent `id` is ignored | `409 USERNAME_TAKEN`, `422 INVALID_REQUEST` |
| `GET /learners/{id}`, `GET /learners/by-username/{username}` | `200` learner | `404 LEARNER_NOT_FOUND`, `422` for a non-UUID `id` |
| `PUT /learners/{id}/completed-courses` `{completed_courses[]}` | `200` learner (no promotion triggered; P5) | `404 LEARNER_NOT_FOUND` |
| `POST /offerings` `{code, title, capacity, prerequisites[]}` | `201` offering state | `409 OFFERING_CODE_EXISTS`, `422 INVALID_REQUEST` |
| `GET /offerings/{id}` | `200` state: capacity, seats_available, enrolled ids, waitlist `[{learner_id, position, eligible, missing_prerequisites}]` | `404 OFFERING_NOT_FOUND` |
| `POST /offerings/{id}/enrollments` `{learner_id}` **or** `{learner: {...}}` (P11) | `201 {learner_id, username, status: ENROLLED\|WAITLISTED, waitlist_position, learner_created}` | `404 OFFERING_NOT_FOUND / LEARNER_NOT_FOUND`, `409 ALREADY_ENROLLED / ALREADY_WAITLISTED / REENROLLMENT_NOT_ALLOWED`, `422 PREREQUISITES_NOT_MET / INVALID_REQUEST` |
| `DELETE /offerings/{id}/enrollments/{learner_id}` (drop) | `200 {status: DROPPED, promoted_learner_ids[]}` | `404 … / NOT_REGISTERED`, `409 ALREADY_DROPPED / NOT_ENROLLED` |
| `DELETE /offerings/{id}/waitlist/{learner_id}` (leave) | `200 {status: WITHDRAWN}` | `404 … / NOT_REGISTERED`, `409 ALREADY_WITHDRAWN / NOT_WAITLISTED` |
| `GET /health` | `200` | |

Interactive OpenAPI docs are served at `/docs`.

## 6. Invariants

These hold after every operation. `check_invariants` in `tests/conftest.py` asserts them after each test that modifies state.

1. `count(ENROLLED) ≤ capacity`.
2. At most one registration per (offering, learner), enforced by the DB unique constraint.
3. A learner is never both enrolled and waitlisted (follows from 2: a single status).
4. Waitlist positions are contiguous, 1..n, in join order.
5. **No eligible learner waits while a seat is free.** Every operation that can open a seat (drop) or find one (enroll) runs `_fill_open_seats` in the same transaction.
6. A failed request (any domain or validation error) writes nothing. Errors are raised before any write, and the transaction rolls back on any exception.

## 7. Promotion algorithm

```
_fill_open_seats(offering):           # runs inside the caller's locked transaction
    free = capacity - count(ENROLLED)
    for reg in WAITLISTED ordered by id:
        if free == 0: stop
        if learner(reg) meets all prerequisites:
            reg.status = ENROLLED; free -= 1
        else:
            leave reg as WAITLISTED       # P4: skip but retain
```

A drop normally frees one seat. The loop fills *all* free seats, which keeps invariant 5 true even after a period where seats stayed open because only ineligible learners were waiting.

## 8. Concurrency and persistence

Every service call is **one transaction** that begins by locking the offering:

- **SQLite (default):** every transaction starts with `BEGIN IMMEDIATE` (`app/db.py`), which takes the database write lock up front. Concurrent writers queue for up to 30 s instead of interleaving, so "count seats, then insert" is atomic. This works across threads *and* processes (multiple uvicorn workers sharing one file).
- **PostgreSQL / others:** the service runs `SELECT … FOR UPDATE` on the offering row (a no-op on SQLite). That serializes operations per offering while different offerings proceed in parallel. Switching backends needs only `DATABASE_URL` and a driver. **This path has not been run against a real Postgres**; see Limitations.
- **Backstop:** the unique constraint rejects a second registration even if locking were bypassed. That `IntegrityError` is mapped to `409 ALREADY_REGISTERED`.
- **Learner creation:** username uniqueness is checked in the transaction and backed by a unique constraint. Inline creation runs inside the offering-locked enroll transaction, so on SQLite two concurrent inline enrolls with the same new username produce exactly one learner (tested). On PostgreSQL the offering lock doesn't cover the learners table, so the loser of that race hits the constraint and gets `409 ALREADY_REGISTERED` ("retry"). A retry then succeeds against the now-existing learner.

`tests/test_concurrency.py` releases 8–30 threads at once through a barrier. It checks capacity under concurrent enrolls, a single record under duplicate enrolls, a single promotion under duplicate drops, and invariants under mixed drops and enrolls. With `BEGIN IMMEDIATE` replaced by a plain `BEGIN`, all four tests fail (SQLite raises `database is locked`), so the tests do exercise the lock.

## 9. Trade-offs

| Choice | Why | Cost |
|---|---|---|
| SQLite + SQLAlchemy over in-memory | Real transactions, survives restarts, multi-process safe, and a straight path to Postgres. The brief allows in-memory, but asked for "scalable". | SQLite allows only one writer per database, so throughput is capped for all offerings together (fine for a POC). |
| Single `registrations` table with a status | One unique constraint enforces enroll-once. Full history, simple queries. | Waitlist queries filter by status (indexed by `offering_id`). |
| Lock the whole offering per operation | Simple and obviously correct. | Serializes traffic for a popular offering. Acceptable at course-registration volumes. |
| Re-check eligibility at promotion time | Eligibility can change while waiting (P2). | One extra prerequisite lookup per waiter scanned. |
| `409` for duplicates instead of idempotent `200` | Clients can tell "already in" from "just got in" (P8). | Retrying clients must treat `ALREADY_*` as success-equivalent. |
