from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.config import Settings
from app.db import create_db_engine, create_session_factory, init_db
from app.main import create_app
from app.models import Learner, LearnerCompletedCourse, Registration
from app.service import RegistrationService


@pytest.fixture
def session_factory(tmp_path):
    # File-backed SQLite (not :memory:) so concurrent tests use real separate connections.
    engine = create_db_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(engine)
    yield create_session_factory(engine)
    engine.dispose()


@pytest.fixture
def service(session_factory) -> RegistrationService:
    return RegistrationService(session_factory)


@pytest.fixture
def add_learner(session_factory):
    def _add(learner_id: str, completed: list[str] = ()) -> str:
        with session_factory.begin() as session:
            session.add(
                Learner(
                    # Fixed readable ids keep service-level assertions simple; the
                    # service treats ids as opaque strings (the API enforces UUIDs).
                    id=learner_id,
                    username=learner_id,
                    name=f"Learner {learner_id}",
                    completed_courses=[LearnerCompletedCourse(course_code=c) for c in completed],
                )
            )
        return learner_id

    return _add


@pytest.fixture
def set_completed(session_factory):
    """Change a learner's completed courses directly in the DB (learners are seed-managed)."""

    def _set(learner_id: str, completed: list[str]) -> None:
        with session_factory.begin() as session:
            session.execute(delete(LearnerCompletedCourse).where(LearnerCompletedCourse.learner_id == learner_id))
            session.add_all(LearnerCompletedCourse(learner_id=learner_id, course_code=c) for c in completed)

    return _set


@pytest.fixture
def snapshot(session_factory):
    """Every registration row for an offering, for before/after comparisons."""

    def _snap(offering_id: int) -> list[tuple]:
        with session_factory() as session:
            rows = session.execute(
                select(Registration.id, Registration.learner_id, Registration.status)
                .where(Registration.offering_id == offering_id)
                .order_by(Registration.id)
            )
            return [tuple(r) for r in rows]

    return _snap


@pytest.fixture
def check_invariants(service, session_factory):
    def _check(offering_id: int) -> None:
        state = service.get_offering_state(offering_id)
        assert len(state.enrolled_learner_ids) <= state.capacity
        assert len(set(state.enrolled_learner_ids)) == len(state.enrolled_learner_ids)
        waitlisted = [e.learner_id for e in state.waitlist]
        assert len(set(waitlisted)) == len(waitlisted)
        assert not set(waitlisted) & set(state.enrolled_learner_ids)
        assert [e.position for e in state.waitlist] == list(range(1, len(state.waitlist) + 1))
        # Any free seat may only coexist with ineligible waiters.
        if state.seats_available > 0:
            assert all(not e.eligible for e in state.waitlist)
        with session_factory() as session:
            dupes = session.scalar(
                select(func.count()).select_from(
                    select(Registration.learner_id)
                    .where(Registration.offering_id == offering_id)
                    .group_by(Registration.learner_id)
                    .having(func.count() > 1)
                    .subquery()
                )
            )
        assert dupes == 0

    return _check


@pytest.fixture
def client(tmp_path) -> Iterator[TestClient]:
    seed = tmp_path / "seed.json"
    seed.write_text(
        """{
          "learners": [
            {"username": "A", "name": "A", "completed_courses": ["CS101"]},
            {"username": "B", "name": "B", "completed_courses": ["cs101"]},
            {"username": "C", "name": "C", "completed_courses": ["CS101"]},
            {"username": "D", "name": "D", "completed_courses": []}
          ]
        }""",
        encoding="utf-8",
    )
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'api.db'}", seed_file=seed))
    with TestClient(app) as c:
        yield c
