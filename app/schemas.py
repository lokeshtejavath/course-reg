"""HTTP request/response models."""

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from app.models import RegistrationStatus

ID_PATTERN = r"^[A-Za-z0-9_-]+$"

# Surrounding whitespace is stripped; codes are upper-cased by the service.
CourseCode = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32, pattern=ID_PATTERN)]
# Case-sensitive, unique; for human reference only (transactions use the UUID).
Username = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")]


class OfferingCreate(BaseModel):
    code: CourseCode = Field(examples=["CS301"])
    title: str = Field(min_length=1, max_length=200)
    capacity: int = Field(ge=1, le=10_000)
    prerequisites: list[CourseCode] = Field(default_factory=list, max_length=50)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("title must not be blank")
        return v


class LearnerCreate(BaseModel):
    username: Username = Field(examples=["jdoe"])
    name: str = Field(min_length=1, max_length=200)
    completed_courses: list[CourseCode] = Field(default_factory=list, max_length=200)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must not be blank")
        return v


class CompletedCoursesUpdate(BaseModel):
    completed_courses: list[CourseCode] = Field(max_length=200)


class LearnerOut(BaseModel):
    id: str
    username: str
    name: str
    completed_courses: list[str]


class EnrollRequest(BaseModel):
    """Identify an existing learner by ``learner_id``, or pass ``learner`` to
    enroll by username (creating the learner if the username is new). Exactly one."""

    learner_id: UUID | None = None
    learner: LearnerCreate | None = None

    @model_validator(mode="after")
    def exactly_one_identifier(self) -> "EnrollRequest":
        if (self.learner_id is None) == (self.learner is None):
            raise ValueError("provide exactly one of 'learner_id' or 'learner'")
        return self


class WaitlistEntryOut(BaseModel):
    learner_id: str
    username: str
    position: int
    eligible: bool
    missing_prerequisites: list[str]


class OfferingOut(BaseModel):
    id: int
    code: str
    title: str
    capacity: int
    prerequisites: list[str]
    seats_available: int
    enrolled_learner_ids: list[str]
    waitlist: list[WaitlistEntryOut]


class EnrollOut(BaseModel):
    offering_id: int
    learner_id: str
    username: str
    status: RegistrationStatus
    waitlist_position: int | None
    learner_created: bool


class DropOut(BaseModel):
    offering_id: int
    learner_id: str
    status: RegistrationStatus
    promoted_learner_ids: list[str]


class LeaveWaitlistOut(BaseModel):
    offering_id: int
    learner_id: str
    status: RegistrationStatus


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody
