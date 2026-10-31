# AI Prompt and Use Record

**Tool:** Claude Code (model: Claude Opus 5.5) in VS Code, one session on 2026-10-31.
**AI role:** requirements clarification, design, implementation, test authoring, and self-verification. The human owner made every policy decision, choosing from options the AI presented.
**Sensitive data:** none. The prompts contained only the assessment brief. There were no credentials, and seed data uses synthetic learner IDs.

> Candor note: the AI wrote essentially all of the code, tests and docs in this repo. The candidate's contribution was defining the task, choosing every policy (in three cases overriding the AI's recommendation), and reviewing. Section 5 is for the candidate's own independent review evidence.

---

## 1. Prompts

### Prompt 1: task definition (human)
> "You are senior developer building a proof of concept for the course registration wait list app, the requirements are the attached file, build a scalable python app, with fastapi, and do not assume anything, and confirm the requirements."
> *(attached: Course-Registration-AI-Assisted-Assessment.pdf)*

**AI response (summary):** Inspected the repo first and found no starter code or data model (only `README.md` and `.gitignore`), so the brief's fallback applies. Also found that Python was not installed. Restated the scope and out-of-scope items, then asked the decision questions below instead of assuming answers.

### Prompts 2–4: requirement clarification (AI asked, human answered)

| Question the AI raised | AI recommendation | Human decision | Outcome |
|---|---|---|---|
| What makes a learner eligible? | Completed all prerequisite course codes | Same | **Accepted** |
| Ineligible learner at the front of the queue? | Skip **and remove** | **Skip but retain** | **Rejected → edited.** Retaining required P5 (re-eligibility) and P6 (free seat) to be defined as well. |
| Persistence? | SQLite + SQLAlchemy | Same | **Accepted** (in-memory and Postgres rejected) |
| Python setup | (no recommendation) | Install via winget | AI ran `winget install Python.Python.3.12` → 3.12.10 |
| How learners and eligibility change | Admin API endpoints | **Seed file only** | **Rejected.** No learner API. Tests change eligibility directly in the DB. |
| Re-eligible learner: when promoted? | At next seat opening | Same | **Accepted** |
| Free seat, only ineligible waiters? | New eligible learner enrolls directly | Same | **Accepted** |
| Extras | (multi-select) | Leave waitlist, read endpoints, create-offering API, Dockerfile, README | **Edited scope.** Dockerfile kept despite the brief listing deployment as out of scope, at the owner's request. |
| Re-enroll after drop? | Allowed as a fresh request | **Never again** | **Rejected.** Led to the single-row-per-learner model with a unique constraint. |
| Duplicate response | `409` + current status | Same | **Accepted** |

### Prompt 5: implementation (implicit continuation)
**AI response (summary):** Produced `app/` (config, db, models, errors, service, schemas, routes, seed, main), `tests/` (50 tests), Dockerfile, and docs. *(The owner later removed the Dockerfile.)*

### Prompt 6: refactor for dynamic learners (human)
> "learners can be dynamically generated, refactor this such that new learners can be added to wait list any time"

**AI response (summary):** Asked three clarifying questions instead of guessing:

| Question | AI recommendation | Human decision | Outcome |
|---|---|---|---|
| How are learners created? | `POST /learners` endpoint | **Both:** the endpoint and inline on enroll | **Edited.** Added `enroll_with_profile` alongside `enroll`. |
| Who picks the learner ID? | Server generates | "server uses the UUID for transactions but user can give username for their reference" | **Edited.** UUID PK plus a unique `username` column, and a by-username lookup endpoint. |
| Completed courses editable? | No (create-only) | **Yes, add PUT** | **Rejected → added** `PUT /learners/{id}/completed-courses`. |

This reverses the earlier policy P9 (seed only). Files changed: `models.py` (UUID default, `username`), `service.py` (learner methods; `enroll` split into `enroll` / `enroll_with_profile` over a shared `_enroll`), `schemas.py`, `routes.py`, `errors.py` (`USERNAME_TAKEN`), `seed.py` and `seed.json` (keyed by username), and the tests.

**Decision the AI proposed:** an inline enroll with an *existing* username uses the stored learner and ignores the supplied name and courses (P11). The AI raised the alternatives (reject with 409 on a mismatch, or apply the new courses).

### Prompt 7: confirm inline-enroll behavior (human)
> "if an inline enroll uses a username that already exists, I use the stored learner and ignore the name and courses sent in the request. So a client that sends updated courses inline can still get PREREQUISITES_NOT_MET, use this"

**Outcome:** **Accepted** as proposed. There was no code change, only docs updated from "flagged" to "confirmed". Already covered by `test_enroll_inline_with_existing_username_uses_existing_learner` and the reuse mutation check (§4).

### Prompt 8: resolve P10 (human)
**AI question:** when a seat is free and a waitlisted learner has just become eligible again, should a new enroll request promote that waiter first (newcomer joins the waitlist) or let the newcomer take the seat?
> "new comer joins waitlist"

**Outcome:** **Accepted** the existing behavior. No code change. Verified by `test_reeligible_waiter_takes_free_seat_before_new_learner`, which asserts the newcomer is `WAITLISTED` and the re-eligible waiter is enrolled.

---

## 2. Design decisions the AI made on its own (flagged for human review)

| Decision | Where | Why | Status |
|---|---|---|---|
| P10: on enroll, promote already-eligible waiters before placing a new learner | `service.py` `enroll` | FIFO fairness. Only reachable via P5 + P6 + a course update. | **Confirmed by owner** (Prompt 8) |
| `BEGIN IMMEDIATE` for SQLite + `FOR UPDATE` on the offering row | `db.py`, `service.py` | SQLite has no row locks, and a deferred `BEGIN` deadlocks when it upgrades to a write lock | Verified (see §4) |
| `_fill_open_seats` fills *all* free seats, not just one | `service.py` | Keeps "no eligible learner waits while a seat is free" true under P4/P6 | Covered by tests |
| HTTP codes: `422` for prerequisites, `404 NOT_REGISTERED`, `409` for state conflicts | `errors.py` | The brief doesn't specify. Stable `code` strings matter more than the exact status. | Documented in DESIGN §5 |
| Course codes trimmed and upper-cased | `service.py` | Avoids `cs101` ≠ `CS101` prerequisite failures | Tested |

## 3. AI self-review edits during the session

- Replaced a hand-rolled prerequisite-code validator in `schemas.py` with a `StringConstraints` regex type shared with `code`. The first version duplicated logic and was harder to read.
- Removed an unused import and an `__all__` stub from `tests/conftest.py`.
- Chose file-backed SQLite for tests instead of `:memory:`, because `:memory:` with threads shares one connection and would hide concurrency bugs.

## 4. Verification evidence

| Check | Command | Result |
|---|---|---|
| Full test suite | `python -m pytest -q` | **50 passed** |
| Mutation: weaken the lock (`BEGIN IMMEDIATE` → `BEGIN`) | concurrency tests | **4/4 failed** (`database is locked`) → the tests exercise the lock |
| Mutation: LIFO waitlist (`order_by(id.desc())`) | full suite | **9 failed** → FIFO is guarded |
| Mutation: promotion ignores eligibility | full suite | **5 failed** → the skip-but-retain policy is guarded |
| All mutations reverted | full suite | 50 passed |
| **After the Prompt 6 refactor:** full suite | `python -m pytest -q` | **64 passed** (existing service tests unchanged; API tests moved to UUIDs; 14 new tests) |
| Mutation: inline enroll always creates (ignores existing username) | full suite | **2 failed** → reuse of an existing username is guarded |
| Live smoke test (Prompt 6) | uvicorn + curl, fresh DB | Seeded learners enrolled by UUID; `POST /learners` → UUID; new learner waitlisted at position 1; inline walk-in created and waitlisted at position 2 (`learner_created: true`); state shows usernames |
| Live smoke test (uvicorn + curl, seed data) | see README | enroll → waitlist → `422` prerequisites → `409` duplicate → drop promotes L003 → `409 ALREADY_DROPPED`; `/docs` served `200` |
| Docker image build | `docker build .` | **Not verified.** Docker is not installed on the dev machine. |
| PostgreSQL `FOR UPDATE` path | n/a | **Not verified.** No Postgres available. |

## 5. Candidate's independent review (to be completed by the candidate)

- [ ] Read `app/service.py` and check each policy P1–P9 against the code
- [x] Decide on P10: keep (newcomer joins the waitlist; Prompt 8)
- [ ] Run `python -m pytest -q` locally and record the result
- [ ] Try one mutation of your own (e.g. remove the `if free <= 0` guard, or the duplicate check) and confirm a test fails
- [ ] Notes / changes made:
