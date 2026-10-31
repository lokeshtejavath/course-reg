"""HTTP routes. Thin layer: validate input, call the service, shape the output.

Handlers are plain ``def`` (not ``async``) because the service does blocking
database I/O; FastAPI runs them in its threadpool.
"""

from dataclasses import asdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Request, status

from app.schemas import (
    CompletedCoursesUpdate,
    DropOut,
    EnrollOut,
    EnrollRequest,
    ErrorResponse,
    LeaveWaitlistOut,
    LearnerCreate,
    LearnerOut,
    OfferingCreate,
    OfferingOut,
)
from app.service import LearnerProfile, OfferingState, RegistrationService

router = APIRouter()

LearnerId = Annotated[UUID, Path()]
OfferingId = Annotated[int, Path(ge=1)]

ERRORS_404 = {404: {"model": ErrorResponse}}
ERRORS_409 = {409: {"model": ErrorResponse}}


def get_service(request: Request) -> RegistrationService:
    return request.app.state.service


Service = Annotated[RegistrationService, Depends(get_service)]


def _profile(body: LearnerCreate) -> LearnerProfile:
    return LearnerProfile(body.username, body.name, body.completed_courses)


def _offering_out(state: OfferingState) -> OfferingOut:
    return OfferingOut(
        id=state.id,
        code=state.code,
        title=state.title,
        capacity=state.capacity,
        prerequisites=state.prerequisites,
        seats_available=state.seats_available,
        enrolled_learner_ids=state.enrolled_learner_ids,
        waitlist=[asdict(e) for e in state.waitlist],
    )


@router.get("/health", tags=["ops"])
def health() -> dict:
    return {"status": "ok"}


@router.post(
    "/learners",
    response_model=LearnerOut,
    status_code=status.HTTP_201_CREATED,
    responses=ERRORS_409,
    tags=["learners"],
)
def create_learner(body: LearnerCreate, service: Service) -> LearnerOut:
    """Create a learner. The server assigns the UUID ``id``; ``username`` is for reference."""
    return LearnerOut(**asdict(service.create_learner(_profile(body))))


# Declared before /learners/{learner_id} so "by-username" is not parsed as a UUID.
@router.get("/learners/by-username/{username}", response_model=LearnerOut, responses=ERRORS_404, tags=["learners"])
def get_learner_by_username(username: Annotated[str, Path(max_length=64)], service: Service) -> LearnerOut:
    return LearnerOut(**asdict(service.get_learner_by_username(username)))


@router.get("/learners/{learner_id}", response_model=LearnerOut, responses=ERRORS_404, tags=["learners"])
def get_learner(learner_id: LearnerId, service: Service) -> LearnerOut:
    return LearnerOut(**asdict(service.get_learner(str(learner_id))))


@router.put(
    "/learners/{learner_id}/completed-courses",
    response_model=LearnerOut,
    responses=ERRORS_404,
    tags=["learners"],
)
def set_completed_courses(learner_id: LearnerId, body: CompletedCoursesUpdate, service: Service) -> LearnerOut:
    """Replace the learner's completed courses. Waitlisted learners keep their
    position; new eligibility is applied at the next seat opening."""
    return LearnerOut(**asdict(service.set_completed_courses(str(learner_id), body.completed_courses)))


@router.post(
    "/offerings",
    response_model=OfferingOut,
    status_code=status.HTTP_201_CREATED,
    responses=ERRORS_409,
    tags=["offerings"],
)
def create_offering(body: OfferingCreate, service: Service) -> OfferingOut:
    state = service.create_offering(body.code, body.title, body.capacity, body.prerequisites)
    return _offering_out(state)


@router.get("/offerings/{offering_id}", response_model=OfferingOut, responses=ERRORS_404, tags=["offerings"])
def get_offering(offering_id: OfferingId, service: Service) -> OfferingOut:
    return _offering_out(service.get_offering_state(offering_id))


@router.post(
    "/offerings/{offering_id}/enrollments",
    response_model=EnrollOut,
    status_code=status.HTTP_201_CREATED,
    responses={**ERRORS_404, **ERRORS_409, 422: {"model": ErrorResponse}},
    tags=["registration"],
)
def enroll(offering_id: OfferingId, body: EnrollRequest, service: Service) -> EnrollOut:
    """Enroll a learner, or add them to the waitlist if the offering is full.

    Send ``learner_id`` for an existing learner, or ``learner`` to enroll by
    username, which creates the learner if the username is new.
    """
    if body.learner is not None:
        result = service.enroll_with_profile(offering_id, _profile(body.learner))
    else:
        result = service.enroll(offering_id, str(body.learner_id))
    return EnrollOut(**asdict(result))


@router.delete(
    "/offerings/{offering_id}/enrollments/{learner_id}",
    response_model=DropOut,
    responses={**ERRORS_404, **ERRORS_409},
    tags=["registration"],
)
def drop(offering_id: OfferingId, learner_id: LearnerId, service: Service) -> DropOut:
    """Drop an enrolled learner and promote the earliest eligible waitlisted learner(s)."""
    return DropOut(**asdict(service.drop(offering_id, str(learner_id))))


@router.delete(
    "/offerings/{offering_id}/waitlist/{learner_id}",
    response_model=LeaveWaitlistOut,
    responses={**ERRORS_404, **ERRORS_409},
    tags=["registration"],
)
def leave_waitlist(offering_id: OfferingId, learner_id: LearnerId, service: Service) -> LeaveWaitlistOut:
    """Remove a waitlisted learner from the queue."""
    return LeaveWaitlistOut(**asdict(service.leave_waitlist(offering_id, str(learner_id))))
