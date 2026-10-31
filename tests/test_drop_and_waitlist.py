import pytest

from app import errors
from app.models import RegistrationStatus as S


@pytest.fixture
def full_offering(service, add_learner):
    """Capacity 2: A, B enrolled; C, D, E waitlisted (all eligible)."""
    oid = service.create_offering("CS201", "Data Structures", capacity=2, prerequisites=["CS101"]).id
    for lid in "ABCDE":
        add_learner(lid, ["CS101"])
        service.enroll(oid, lid)
    return oid


def waitlist_ids(service, oid):
    return [e.learner_id for e in service.get_offering_state(oid).waitlist]


# ------------------------------------------------------------------ drop + FIFO


def test_drop_promotes_earliest_waitlisted_learner(service, full_offering, check_invariants):
    result = service.drop(full_offering, "A")

    assert result.status is S.DROPPED
    assert result.promoted_learner_ids == ["C"]
    state = service.get_offering_state(full_offering)
    assert sorted(state.enrolled_learner_ids) == ["B", "C"]
    assert waitlist_ids(service, full_offering) == ["D", "E"]
    check_invariants(full_offering)


def test_successive_drops_follow_fifo(service, full_offering, check_invariants):
    assert service.drop(full_offering, "A").promoted_learner_ids == ["C"]
    assert service.drop(full_offering, "B").promoted_learner_ids == ["D"]
    assert service.drop(full_offering, "C").promoted_learner_ids == ["E"]
    assert service.drop(full_offering, "D").promoted_learner_ids == []
    state = service.get_offering_state(full_offering)
    assert state.enrolled_learner_ids == ["E"]
    assert state.seats_available == 1
    check_invariants(full_offering)


def test_drop_with_empty_waitlist_frees_a_seat(service, add_learner, check_invariants):
    oid = service.create_offering("CS201", "DS", capacity=2, prerequisites=[]).id
    add_learner("A")
    service.enroll(oid, "A")

    result = service.drop(oid, "A")

    assert result.promoted_learner_ids == []
    assert service.get_offering_state(oid).seats_available == 2
    check_invariants(oid)


# --------------------------------------------------- invalid and repeated drops


def test_repeated_drop_is_rejected_without_change(service, full_offering, snapshot):
    service.drop(full_offering, "A")
    before = snapshot(full_offering)

    with pytest.raises(errors.AlreadyDropped):
        service.drop(full_offering, "A")

    assert snapshot(full_offering) == before


def test_drop_of_unregistered_learner(service, full_offering, add_learner, snapshot):
    add_learner("Z", ["CS101"])
    before = snapshot(full_offering)
    with pytest.raises(errors.RegistrationNotFound):
        service.drop(full_offering, "Z")
    assert snapshot(full_offering) == before


def test_drop_of_waitlisted_learner_is_rejected(service, full_offering, snapshot):
    before = snapshot(full_offering)
    with pytest.raises(errors.NotEnrolled) as exc:
        service.drop(full_offering, "C")
    assert exc.value.details["current_status"] == "WAITLISTED"
    assert snapshot(full_offering) == before


def test_drop_with_unknown_ids(service, full_offering, snapshot):
    before = snapshot(full_offering)
    with pytest.raises(errors.LearnerNotFound):
        service.drop(full_offering, "NOPE")
    with pytest.raises(errors.OfferingNotFound):
        service.drop(9999, "A")
    assert snapshot(full_offering) == before


# ------------------------------------------- ineligible: skipped but retained


def test_ineligible_front_learner_is_skipped_but_retained(service, full_offering, set_completed, check_invariants):
    set_completed("C", [])  # C (front of queue) loses eligibility

    result = service.drop(full_offering, "A")

    assert result.promoted_learner_ids == ["D"]
    state = service.get_offering_state(full_offering)
    assert "C" not in state.enrolled_learner_ids
    assert [(e.learner_id, e.position, e.eligible) for e in state.waitlist] == [("C", 1, False), ("E", 2, True)]
    assert state.waitlist[0].missing_prerequisites == ["CS101"]
    check_invariants(full_offering)


def test_retained_learner_keeps_position_and_is_promoted_when_eligible_again(
    service, full_offering, set_completed, check_invariants
):
    set_completed("C", [])
    service.drop(full_offering, "A")  # D promoted, C retained at front
    set_completed("C", ["CS101"])  # no promotion yet: no seat open

    assert waitlist_ids(service, full_offering) == ["C", "E"]
    result = service.drop(full_offering, "B")

    assert result.promoted_learner_ids == ["C"]  # ahead of E, original position kept
    assert waitlist_ids(service, full_offering) == ["E"]
    check_invariants(full_offering)


def test_no_eligible_waiters_leaves_seat_open(service, full_offering, set_completed, check_invariants):
    for lid in "CDE":
        set_completed(lid, [])

    result = service.drop(full_offering, "A")

    assert result.promoted_learner_ids == []
    state = service.get_offering_state(full_offering)
    assert state.seats_available == 1
    assert waitlist_ids(service, full_offering) == ["C", "D", "E"]
    check_invariants(full_offering)


def test_ineligible_learners_are_skipped_consistently_across_drops(
    service, full_offering, set_completed, check_invariants
):
    set_completed("C", [])
    set_completed("D", [])
    assert service.drop(full_offering, "A").promoted_learner_ids == ["E"]
    assert service.drop(full_offering, "B").promoted_learner_ids == []
    assert waitlist_ids(service, full_offering) == ["C", "D"]
    check_invariants(full_offering)


# ------------------------------------------------------------- leave waitlist


def test_leave_waitlist_removes_learner_and_shifts_positions(service, full_offering, check_invariants):
    result = service.leave_waitlist(full_offering, "C")

    assert result.status is S.WITHDRAWN
    state = service.get_offering_state(full_offering)
    assert [(e.learner_id, e.position) for e in state.waitlist] == [("D", 1), ("E", 2)]
    assert state.seats_available == 0  # no seat opened, nobody promoted
    assert service.drop(full_offering, "A").promoted_learner_ids == ["D"]
    check_invariants(full_offering)


def test_leave_waitlist_invalid_cases(service, full_offering, add_learner, snapshot):
    service.leave_waitlist(full_offering, "C")
    before = snapshot(full_offering)

    with pytest.raises(errors.AlreadyWithdrawn):
        service.leave_waitlist(full_offering, "C")
    with pytest.raises(errors.NotWaitlisted):
        service.leave_waitlist(full_offering, "A")  # enrolled, should drop instead
    add_learner("Z", ["CS101"])
    with pytest.raises(errors.RegistrationNotFound):
        service.leave_waitlist(full_offering, "Z")

    assert snapshot(full_offering) == before
