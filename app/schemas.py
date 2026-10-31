"""HTTP request/response models."""

from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.models import RegistrationStatus

ID_PATTERN = r"^[A-Za-z0-9_-]+$"

# Surrounding whitespace is stripped; codes are upper-cased by the service.
CourseCode = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32, pattern=ID_PATTERN)]


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


class EnrollRequest(BaseModel):
    learner_id: str = Field(min_length=1, max_length=64, pattern=ID_PATTERN, examples=["L001"])


class WaitlistEntryOut(BaseModel):
    learner_id: str
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
    status: RegistrationStatus
    waitlist_position: int | None


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
