"""Concurrent requests against a file-backed SQLite DB, one connection per thread.

A barrier releases all threads at once to maximise interleaving. Without the
transaction lock in app/db.py, the capacity test oversubscribes the offering.
"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import func, select

from app import errors
from app.models import Learner
from app.models import RegistrationStatus as S
from app.service import LearnerProfile


def run_concurrently(fn, args_list):
    barrier = Barrier(len(args_list))

    def call(args):
        barrier.wait()
        try:
            return fn(*args)
        except errors.DomainError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=len(args_list)) as pool:
        return list(pool.map(call, args_list))


def test_concurrent_enrolls_never_exceed_capacity(service, add_learner, check_invariants):
    oid = service.create_offering("CS201", "DS", capacity=5, prerequisites=["CS101"]).id
    learners = [add_learner(f"L{i:02d}", ["CS101"]) for i in range(30)]

    results = run_concurrently(service.enroll, [(oid, lid) for lid in learners])

    statuses = [r.status for r in results]
    assert statuses.count(S.ENROLLED) == 5
    assert statuses.count(S.WAITLISTED) == 25
    assert sorted(r.waitlist_position for r in results if r.status is S.WAITLISTED) == list(range(1, 26))
    check_invariants(oid)


def test_concurrent_duplicate_enrolls_create_one_record(service, add_learner, snapshot):
    oid = service.create_offering("CS201", "DS", capacity=5, prerequisites=[]).id
    add_learner("A")

    results = run_concurrently(service.enroll, [(oid, "A")] * 10)

    successes = [r for r in results if not isinstance(r, Exception)]
    assert len(successes) == 1
    assert all(isinstance(r, errors.AlreadyEnrolled) for r in results if r not in successes)
    assert len(snapshot(oid)) == 1


def test_concurrent_drops_of_same_learner_promote_once(service, add_learner, check_invariants):
    oid = service.create_offering("CS201", "DS", capacity=1, prerequisites=[]).id
    for lid in "ABC":
        add_learner(lid)
        service.enroll(oid, lid)

    results = run_concurrently(service.drop, [(oid, "A")] * 8)

    successes = [r for r in results if not isinstance(r, Exception)]
    assert len(successes) == 1
    assert successes[0].promoted_learner_ids == ["B"]
    assert all(isinstance(r, errors.AlreadyDropped) for r in results if r not in successes)
    state = service.get_offering_state(oid)
    assert state.enrolled_learner_ids == ["B"]
    assert [e.learner_id for e in state.waitlist] == ["C"]
    check_invariants(oid)


def test_concurrent_drops_and_enrolls_keep_invariants(service, add_learner, check_invariants):
    oid = service.create_offering("CS201", "DS", capacity=3, prerequisites=[]).id
    for lid in ["E1", "E2", "E3"]:
        add_learner(lid)
        service.enroll(oid, lid)
    newcomers = [add_learner(f"N{i}") for i in range(10)]

    calls = [(service.drop, (oid, lid)) for lid in ["E1", "E2", "E3"]]
    calls += [(service.enroll, (oid, lid)) for lid in newcomers]
    results = run_concurrently(lambda fn, args: fn(*args), calls)

    assert not any(isinstance(r, Exception) for r in results)
    state = service.get_offering_state(oid)
    assert len(state.enrolled_learner_ids) == 3
    assert len(state.waitlist) == 7
    check_invariants(oid)


def test_concurrent_inline_creation_of_new_learners(service, check_invariants):
    oid = service.create_offering("CS201", "DS", capacity=3, prerequisites=["CS101"]).id
    profiles = [LearnerProfile(f"new{i}", f"New {i}", ["CS101"]) for i in range(20)]

    results = run_concurrently(service.enroll_with_profile, [(oid, p) for p in profiles])

    assert all(r.learner_created for r in results)
    assert len({r.learner_id for r in results}) == 20
    statuses = [r.status for r in results]
    assert (statuses.count(S.ENROLLED), statuses.count(S.WAITLISTED)) == (3, 17)
    check_invariants(oid)


def test_concurrent_inline_enroll_same_new_username_creates_one_learner(service, session_factory, snapshot):
    oid = service.create_offering("CS201", "DS", capacity=3, prerequisites=[]).id
    profile = LearnerProfile("dup", "Dup", [])

    results = run_concurrently(service.enroll_with_profile, [(oid, profile)] * 8)

    successes = [r for r in results if not isinstance(r, Exception)]
    assert len(successes) == 1 and successes[0].learner_created
    assert all(isinstance(r, errors.AlreadyEnrolled) for r in results if r not in successes)
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Learner).where(Learner.username == "dup")) == 1
    assert len(snapshot(oid)) == 1
