"""Enrollment, drop and waitlist-promotion rules for a course offering.

Policies (see docs/DESIGN.md for the reasoning):

* Eligibility: a learner is eligible if they have completed every prerequisite
  course code of the offering. It is checked on enroll and again at promotion.
* Enroll once: a learner has at most one registration per offering. After a
  drop or leaving the waitlist, later enroll requests are rejected.
* Waitlist: FIFO by join order. When seats open, waitlisted learners are
  scanned in order and the earliest eligible ones are promoted.
* Ineligible at promotion time: skipped but retained. They keep their queue
  position and are reconsidered, in place, the next time seats open.

* Learners: created at any time (directly, or inline on enroll) with a
  server-generated UUID and a unique, user-chosen username for reference.

Every public method runs in one transaction. Registration operations start by
locking the offering (see app/db.py), so capacity checks and the writes that
depend on them are atomic.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app import errors
from app.models import (
    Learner,
    LearnerCompletedCourse,
    Offering,
    OfferingPrerequisite,
    Registration,
    RegistrationStatus,
)


def normalize_course_code(code: str) -> str:
    return code.strip().upper()


@dataclass(frozen=True)
class LearnerProfile:
    """Details for creating a learner; the server assigns the UUID."""

    username: str
    name: str
    completed_courses: list[str]


@dataclass(frozen=True)
class LearnerView:
    id: str
    username: str
    name: str
    completed_courses: list[str]


@dataclass(frozen=True)
class EnrollResult:
    offering_id: int
    learner_id: str
    username: str
    status: RegistrationStatus
    waitlist_position: int | None  # 1-based; None when enrolled
    learner_created: bool = False


@dataclass(frozen=True)
class DropResult:
    offering_id: int
    learner_id: str
    status: RegistrationStatus
    promoted_learner_ids: list[str]


@dataclass(frozen=True)
class LeaveWaitlistResult:
    offering_id: int
    learner_id: str
    status: RegistrationStatus


@dataclass(frozen=True)
class WaitlistEntryView:
    learner_id: str
    username: str
    position: int
    eligible: bool
    missing_prerequisites: list[str]


@dataclass(frozen=True)
class OfferingState:
    id: int
    code: str
    title: str
    capacity: int
    prerequisites: list[str]
    enrolled_learner_ids: list[str]
    waitlist: list[WaitlistEntryView]

    @property
    def seats_available(self) -> int:
        return self.capacity - len(self.enrolled_learner_ids)


class RegistrationService:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    # ------------------------------------------------------------------ offerings

    def create_offering(self, code: str, title: str, capacity: int, prerequisites: list[str]) -> OfferingState:
        code = normalize_course_code(code)
        prereq_codes = sorted({normalize_course_code(p) for p in prerequisites})
        try:
            with self._session_factory.begin() as session:
                if session.scalar(select(Offering.id).where(Offering.code == code)) is not None:
                    raise errors.DuplicateOfferingCode(f"Offering code {code} already exists.", code=code)
                offering = Offering(
                    code=code,
                    title=title.strip(),
                    capacity=capacity,
                    prerequisites=[OfferingPrerequisite(course_code=p) for p in prereq_codes],
                )
                session.add(offering)
                session.flush()
                return self._build_state(session, offering)
        except IntegrityError as exc:
            raise errors.DuplicateOfferingCode(f"Offering code {code} already exists.", code=code) from exc

    def get_offering_state(self, offering_id: int) -> OfferingState:
        with self._session_factory.begin() as session:
            offering = session.get(Offering, offering_id)
            if offering is None:
                raise errors.OfferingNotFound(f"Offering {offering_id} not found.", offering_id=offering_id)
            return self._build_state(session, offering)

    # ------------------------------------------------------------------- learners

    def create_learner(self, profile: LearnerProfile) -> LearnerView:
        try:
            with self._session_factory.begin() as session:
                learner = self._insert_learner(session, profile)
                return self._learner_view(learner)
        except IntegrityError as exc:
            raise self._username_taken(profile.username) from exc

    def get_learner(self, learner_id: str) -> LearnerView:
        with self._session_factory.begin() as session:
            return self._learner_view(self._get_learner(session, learner_id))

    def get_learner_by_username(self, username: str) -> LearnerView:
        with self._session_factory.begin() as session:
            learner = self._find_by_username(session, username)
            if learner is None:
                raise errors.LearnerNotFound(f"No learner with username {username!r}.", username=username)
            return self._learner_view(learner)

    def set_completed_courses(self, learner_id: str, completed_courses: list[str]) -> LearnerView:
        """Replace a learner's completed courses.

        Waitlist positions are untouched. A waiting learner who becomes eligible
        is considered at the next seat opening; one who becomes ineligible is
        skipped (but retained) from then on.
        """
        with self._session_factory.begin() as session:
            learner = self._get_learner(session, learner_id)
            codes = sorted({normalize_course_code(c) for c in completed_courses})
            learner.completed_courses = [LearnerCompletedCourse(course_code=c) for c in codes]
            session.flush()
            return self._learner_view(learner)

    # ---------------------------------------------------------------- operations

    def enroll(self, offering_id: int, learner_id: str) -> EnrollResult:
        def resolve(session: Session) -> tuple[Learner, bool]:
            return self._get_learner(session, learner_id), False

        return self._enroll(offering_id, resolve)

    def enroll_with_profile(self, offering_id: int, profile: LearnerProfile) -> EnrollResult:
        """Enroll by username, creating the learner first if the username is new.

        If the username already exists, that learner is used as-is: the supplied
        name and completed courses are NOT applied (use set_completed_courses).
        Creation and enrollment share one transaction, so a rejected enrollment
        (e.g. prerequisites not met) also discards the newly created learner.
        """

        def resolve(session: Session) -> tuple[Learner, bool]:
            existing = self._find_by_username(session, profile.username)
            if existing is not None:
                return existing, False
            return self._insert_learner(session, profile), True

        return self._enroll(offering_id, resolve)

    def _enroll(self, offering_id: int, resolve_learner) -> EnrollResult:
        try:
            with self._session_factory.begin() as session:
                offering = self._lock_offering(session, offering_id)
                learner, created = resolve_learner(session)
                learner_id = learner.id

                existing = self._get_registration(session, offering_id, learner_id)
                if existing is not None:
                    self._raise_duplicate(session, existing)

                missing = self._missing_prerequisites(offering, learner)
                if missing:
                    raise errors.PrerequisitesNotMet(
                        f"Learner {learner.username} has not completed the prerequisites for {offering.code}.",
                        learner_id=learner_id,
                        missing_prerequisites=missing,
                    )

                # Eligible learners already waiting are ahead of a new request.
                # This only matters if a waiter regained eligibility while a seat
                # was free; normally it promotes nobody.
                self._fill_open_seats(session, offering)

                if self._enrolled_count(session, offering_id) < offering.capacity:
                    status = RegistrationStatus.ENROLLED
                else:
                    status = RegistrationStatus.WAITLISTED
                registration = Registration(offering_id=offering_id, learner_id=learner_id, status=status)
                session.add(registration)
                session.flush()

                position = None
                if status is RegistrationStatus.WAITLISTED:
                    position = self._waitlist_position(session, registration)
                return EnrollResult(offering_id, learner_id, learner.username, status, position, created)
        except IntegrityError as exc:
            # Unreachable while the offering lock holds (SQLite serializes all
            # writers); on PostgreSQL a concurrent inline create of the same new
            # username can also land here. Kept as a safety net.
            raise errors.DuplicateRegistration(
                f"Conflicting concurrent registration for offering {offering_id}; retry the request.",
                offering_id=offering_id,
            ) from exc

    def drop(self, offering_id: int, learner_id: str) -> DropResult:
        with self._session_factory.begin() as session:
            offering = self._lock_offering(session, offering_id)
            self._get_learner(session, learner_id)
            registration = self._get_registration(session, offering_id, learner_id)

            if registration is None:
                raise errors.RegistrationNotFound(
                    f"Learner {learner_id} is not registered for offering {offering_id}.", learner_id=learner_id
                )
            if registration.status is RegistrationStatus.DROPPED:
                raise errors.AlreadyDropped(
                    f"Learner {learner_id} has already dropped this offering.", learner_id=learner_id
                )
            if registration.status is not RegistrationStatus.ENROLLED:
                raise errors.NotEnrolled(
                    f"Learner {learner_id} is not enrolled (status {registration.status.value}). "
                    "Waitlisted learners should leave the waitlist instead.",
                    learner_id=learner_id,
                    current_status=registration.status.value,
                )

            registration.status = RegistrationStatus.DROPPED
            session.flush()
            promoted = self._fill_open_seats(session, offering)
            return DropResult(offering_id, learner_id, RegistrationStatus.DROPPED, promoted)

    def leave_waitlist(self, offering_id: int, learner_id: str) -> LeaveWaitlistResult:
        with self._session_factory.begin() as session:
            self._lock_offering(session, offering_id)
            self._get_learner(session, learner_id)
            registration = self._get_registration(session, offering_id, learner_id)

            if registration is None:
                raise errors.RegistrationNotFound(
                    f"Learner {learner_id} is not registered for offering {offering_id}.", learner_id=learner_id
                )
            if registration.status is RegistrationStatus.WITHDRAWN:
                raise errors.AlreadyWithdrawn(
                    f"Learner {learner_id} has already left the waitlist.", learner_id=learner_id
                )
            if registration.status is not RegistrationStatus.WAITLISTED:
                raise errors.NotWaitlisted(
                    f"Learner {learner_id} is not on the waitlist (status {registration.status.value}).",
                    learner_id=learner_id,
                    current_status=registration.status.value,
                )

            # No seat opens, so there is nothing to promote.
            registration.status = RegistrationStatus.WITHDRAWN
            return LeaveWaitlistResult(offering_id, learner_id, RegistrationStatus.WITHDRAWN)

    # ------------------------------------------------------------------ internals

    @staticmethod
    def _lock_offering(session: Session, offering_id: int) -> Offering:
        # FOR UPDATE is a no-op on SQLite (serialized by BEGIN IMMEDIATE instead)
        # and a row lock on PostgreSQL.
        offering = session.scalar(select(Offering).where(Offering.id == offering_id).with_for_update())
        if offering is None:
            raise errors.OfferingNotFound(f"Offering {offering_id} not found.", offering_id=offering_id)
        return offering

    @staticmethod
    def _get_learner(session: Session, learner_id: str) -> Learner:
        learner = session.get(Learner, learner_id)
        if learner is None:
            raise errors.LearnerNotFound(f"Learner {learner_id} not found.", learner_id=learner_id)
        return learner

    @staticmethod
    def _find_by_username(session: Session, username: str) -> Learner | None:
        return session.scalar(select(Learner).where(Learner.username == username.strip()))

    def _insert_learner(self, session: Session, profile: LearnerProfile) -> Learner:
        username = profile.username.strip()
        if self._find_by_username(session, username) is not None:
            raise self._username_taken(username)
        codes = sorted({normalize_course_code(c) for c in profile.completed_courses})
        learner = Learner(
            username=username,
            name=profile.name.strip(),
            completed_courses=[LearnerCompletedCourse(course_code=c) for c in codes],
        )
        session.add(learner)
        session.flush()
        return learner

    @staticmethod
    def _username_taken(username: str) -> errors.UsernameTaken:
        return errors.UsernameTaken(f"Username {username!r} is already taken.", username=username)

    @staticmethod
    def _learner_view(learner: Learner) -> LearnerView:
        return LearnerView(learner.id, learner.username, learner.name, sorted(learner.completed_codes))

    @staticmethod
    def _get_registration(session: Session, offering_id: int, learner_id: str) -> Registration | None:
        return session.scalar(
            select(Registration).where(Registration.offering_id == offering_id, Registration.learner_id == learner_id)
        )

    @staticmethod
    def _missing_prerequisites(offering: Offering, learner: Learner) -> list[str]:
        return sorted(offering.prerequisite_codes - learner.completed_codes)

    @staticmethod
    def _enrolled_count(session: Session, offering_id: int) -> int:
        return session.scalar(
            select(func.count())
            .select_from(Registration)
            .where(Registration.offering_id == offering_id, Registration.status == RegistrationStatus.ENROLLED)
        )

    @staticmethod
    def _waitlist(session: Session, offering_id: int) -> list[tuple[Registration, Learner]]:
        rows = session.execute(
            select(Registration, Learner)
            .join(Learner, Learner.id == Registration.learner_id)
            .where(Registration.offering_id == offering_id, Registration.status == RegistrationStatus.WAITLISTED)
            .order_by(Registration.id)
        )
        return [(reg, learner) for reg, learner in rows]

    @staticmethod
    def _waitlist_position(session: Session, registration: Registration) -> int:
        return session.scalar(
            select(func.count())
            .select_from(Registration)
            .where(
                Registration.offering_id == registration.offering_id,
                Registration.status == RegistrationStatus.WAITLISTED,
                Registration.id <= registration.id,
            )
        )

    def _fill_open_seats(self, session: Session, offering: Offering) -> list[str]:
        """Promote the earliest eligible waitlisted learners into any free seats.

        Ineligible learners are skipped but left on the waitlist in place.
        """
        free = offering.capacity - self._enrolled_count(session, offering.id)
        promoted: list[str] = []
        if free <= 0:
            return promoted
        for registration, learner in self._waitlist(session, offering.id):
            if len(promoted) == free:
                break
            if not self._missing_prerequisites(offering, learner):
                registration.status = RegistrationStatus.ENROLLED
                promoted.append(learner.id)
        session.flush()
        return promoted

    def _raise_duplicate(self, session: Session, registration: Registration) -> None:
        learner_id = registration.learner_id
        status = registration.status
        if status is RegistrationStatus.ENROLLED:
            raise errors.AlreadyEnrolled(
                f"Learner {learner_id} is already enrolled.", learner_id=learner_id, current_status=status.value
            )
        if status is RegistrationStatus.WAITLISTED:
            raise errors.AlreadyWaitlisted(
                f"Learner {learner_id} is already on the waitlist.",
                learner_id=learner_id,
                current_status=status.value,
                waitlist_position=self._waitlist_position(session, registration),
            )
        raise errors.ReenrollmentNotAllowed(
            f"Learner {learner_id} previously left this offering ({status.value}) and may not enroll again.",
            learner_id=learner_id,
            current_status=status.value,
        )

    def _build_state(self, session: Session, offering: Offering) -> OfferingState:
        enrolled = session.scalars(
            select(Registration.learner_id)
            .where(Registration.offering_id == offering.id, Registration.status == RegistrationStatus.ENROLLED)
            .order_by(Registration.id)
        ).all()
        waitlist = []
        for position, (_, learner) in enumerate(self._waitlist(session, offering.id), start=1):
            missing = self._missing_prerequisites(offering, learner)
            waitlist.append(WaitlistEntryView(learner.id, learner.username, position, not missing, missing))
        return OfferingState(
            id=offering.id,
            code=offering.code,
            title=offering.title,
            capacity=offering.capacity,
            prerequisites=sorted(offering.prerequisite_codes),
            enrolled_learner_ids=list(enrolled),
            waitlist=waitlist,
        )
