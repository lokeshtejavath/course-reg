import pytest

from app import errors
from app.models import RegistrationStatus as S


@pytest.fixture
def offering(service):
    return service.create_offering("CS201", "Data Structures", capacity=2, prerequisites=["CS101"]).id


def test_enroll_succeeds_when_capacity_remains_and_prereqs_met(service, add_learner, offering, check_invariants):
    add_learner("A", ["CS101"])

    result = service.enroll(offering, "A")

    assert result.status is S.ENROLLED
    assert result.waitlist_position is None
    state = service.get_offering_state(offering)
    assert state.enrolled_learner_ids == ["A"]
    assert state.seats_available == 1
    check_invariants(offering)


def test_full_offering_puts_eligible_learners_on_waitlist_in_order(service, add_learner, offering, check_invariants):
    for lid in "ABCD":
        add_learner(lid, ["CS101"])

    results = [service.enroll(offering, lid) for lid in "ABCD"]

    assert [r.status for r in results] == [S.ENROLLED, S.ENROLLED, S.WAITLISTED, S.WAITLISTED]
    assert [r.waitlist_position for r in results] == [None, None, 1, 2]
    state = service.get_offering_state(offering)
    assert state.seats_available == 0
    assert [e.learner_id for e in state.waitlist] == ["C", "D"]
    check_invariants(offering)


def test_duplicate_enroll_when_enrolled_is_rejected_without_change(service, add_learner, offering, snapshot):
    add_learner("A", ["CS101"])
    service.enroll(offering, "A")
    before = snapshot(offering)

    with pytest.raises(errors.AlreadyEnrolled) as exc:
        service.enroll(offering, "A")

    assert exc.value.details["current_status"] == "ENROLLED"
    assert snapshot(offering) == before


def test_duplicate_enroll_when_waitlisted_joins_waitlist_only_once(service, add_learner, offering, snapshot):
    for lid in "ABC":
        add_learner(lid, ["CS101"])
    for lid in "ABC":
        service.enroll(offering, lid)
    before = snapshot(offering)

    with pytest.raises(errors.AlreadyWaitlisted) as exc:
        service.enroll(offering, "C")

    assert exc.value.details["waitlist_position"] == 1
    assert snapshot(offering) == before
    assert len(service.get_offering_state(offering).waitlist) == 1


def test_prerequisite_failure_is_rejected_with_missing_courses(service, add_learner, offering, snapshot):
    add_learner("X", [])

    with pytest.raises(errors.PrerequisitesNotMet) as exc:
        service.enroll(offering, "X")

    assert exc.value.details["missing_prerequisites"] == ["CS101"]
    assert snapshot(offering) == []


def test_ineligible_learner_cannot_join_waitlist_when_full(service, add_learner, offering, snapshot):
    add_learner("A", ["CS101"])
    add_learner("B", ["CS101"])
    add_learner("X", ["MATH101"])
    service.enroll(offering, "A")
    service.enroll(offering, "B")
    before = snapshot(offering)

    with pytest.raises(errors.PrerequisitesNotMet):
        service.enroll(offering, "X")

    assert snapshot(offering) == before


def test_multiple_prerequisites_all_required(service, add_learner, check_invariants):
    oid = service.create_offering("CS301", "Algorithms", capacity=5, prerequisites=["cs101", " MATH101 "]).id
    add_learner("P", ["CS101"])
    add_learner("Q", ["CS101", "MATH101"])

    with pytest.raises(errors.PrerequisitesNotMet) as exc:
        service.enroll(oid, "P")
    assert exc.value.details["missing_prerequisites"] == ["MATH101"]
    assert service.enroll(oid, "Q").status is S.ENROLLED
    check_invariants(oid)


def test_offering_without_prerequisites_accepts_anyone(service, add_learner):
    oid = service.create_offering("GEN100", "Orientation", capacity=1, prerequisites=[]).id
    add_learner("X", [])
    assert service.enroll(oid, "X").status is S.ENROLLED


def test_unknown_learner_or_offering(service, add_learner, offering, snapshot):
    add_learner("A", ["CS101"])
    with pytest.raises(errors.LearnerNotFound):
        service.enroll(offering, "NOPE")
    with pytest.raises(errors.OfferingNotFound):
        service.enroll(9999, "A")
    assert snapshot(offering) == []


def test_reenroll_after_drop_is_not_allowed(service, add_learner, offering, snapshot):
    add_learner("A", ["CS101"])
    service.enroll(offering, "A")
    service.drop(offering, "A")
    before = snapshot(offering)

    with pytest.raises(errors.ReenrollmentNotAllowed) as exc:
        service.enroll(offering, "A")

    assert exc.value.details["current_status"] == "DROPPED"
    assert snapshot(offering) == before


def test_reenroll_after_leaving_waitlist_is_not_allowed(service, add_learner, offering):
    for lid in "ABC":
        add_learner(lid, ["CS101"])
        service.enroll(offering, lid)
    service.leave_waitlist(offering, "C")

    with pytest.raises(errors.ReenrollmentNotAllowed) as exc:
        service.enroll(offering, "C")
    assert exc.value.details["current_status"] == "WITHDRAWN"


def test_free_seat_with_only_ineligible_waiters_goes_to_new_eligible_learner(
    service, add_learner, set_completed, offering, check_invariants
):
    for lid in "ABC":
        add_learner(lid, ["CS101"])
        service.enroll(offering, lid)
    set_completed("C", [])  # C loses eligibility while waiting
    service.drop(offering, "A")  # C is skipped (retained); seat stays free
    assert service.get_offering_state(offering).seats_available == 1

    add_learner("N", ["CS101"])
    result = service.enroll(offering, "N")

    assert result.status is S.ENROLLED
    state = service.get_offering_state(offering)
    assert [e.learner_id for e in state.waitlist] == ["C"]
    check_invariants(offering)


def test_reeligible_waiter_takes_free_seat_before_new_learner(
    service, add_learner, set_completed, offering, check_invariants
):
    """Edge case: a waiter regains eligibility while a seat is free. On the next
    enroll they are promoted first (FIFO among eligible learners), and the new
    learner joins the waitlist behind them."""
    for lid in "ABC":
        add_learner(lid, ["CS101"])
        service.enroll(offering, lid)
    set_completed("C", [])
    service.drop(offering, "A")
    set_completed("C", ["CS101"])

    add_learner("N", ["CS101"])
    result = service.enroll(offering, "N")

    assert result.status is S.WAITLISTED
    state = service.get_offering_state(offering)
    assert "C" in state.enrolled_learner_ids
    assert [e.learner_id for e in state.waitlist] == ["N"]
    check_invariants(offering)


def test_duplicate_offering_code_rejected(service):
    service.create_offering("CS201", "Data Structures", capacity=2, prerequisites=[])
    with pytest.raises(errors.DuplicateOfferingCode):
        service.create_offering("cs201", "Again", capacity=3, prerequisites=[])
