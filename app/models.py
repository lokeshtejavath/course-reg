"""ORM models.

A learner has at most one ``Registration`` row per offering (unique constraint).
That single row carries the learner's whole lifecycle for the offering, which is
what enforces "a learner may enroll only once": once the row is DROPPED or
WITHDRAWN it is never reused, so later enroll requests are rejected.
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RegistrationStatus(str, enum.Enum):
    ENROLLED = "ENROLLED"
    WAITLISTED = "WAITLISTED"
    DROPPED = "DROPPED"  # was enrolled, then dropped
    WITHDRAWN = "WITHDRAWN"  # was waitlisted, then left the waitlist


def new_learner_id() -> str:
    return str(uuid.uuid4())


class Learner(Base):
    __tablename__ = "learners"

    # Server-generated UUID; the only identifier used by registrations and the API.
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_learner_id)
    # Human-chosen handle for reference and lookup; never used as a foreign key.
    username: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))

    completed_courses: Mapped[list["LearnerCompletedCourse"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def completed_codes(self) -> set[str]:
        return {c.course_code for c in self.completed_courses}


class LearnerCompletedCourse(Base):
    __tablename__ = "learner_completed_courses"

    learner_id: Mapped[str] = mapped_column(ForeignKey("learners.id", ondelete="CASCADE"), primary_key=True)
    course_code: Mapped[str] = mapped_column(String(32), primary_key=True)


class Offering(Base):
    __tablename__ = "offerings"
    __table_args__ = (CheckConstraint("capacity >= 1", name="ck_offering_capacity_positive"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    capacity: Mapped[int] = mapped_column(Integer)

    prerequisites: Mapped[list["OfferingPrerequisite"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def prerequisite_codes(self) -> set[str]:
        return {p.course_code for p in self.prerequisites}


class OfferingPrerequisite(Base):
    __tablename__ = "offering_prerequisites"

    offering_id: Mapped[int] = mapped_column(ForeignKey("offerings.id", ondelete="CASCADE"), primary_key=True)
    course_code: Mapped[str] = mapped_column(String(32), primary_key=True)


class Registration(Base):
    __tablename__ = "registrations"
    __table_args__ = (UniqueConstraint("offering_id", "learner_id", name="uq_registration_offering_learner"),)

    # Monotonic id doubles as the FIFO key: waitlisted rows are created at the
    # moment the learner joins the queue, so ordering WAITLISTED rows by id is
    # join order.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    offering_id: Mapped[int] = mapped_column(ForeignKey("offerings.id", ondelete="CASCADE"), index=True)
    learner_id: Mapped[str] = mapped_column(ForeignKey("learners.id", ondelete="CASCADE"))
    status: Mapped[RegistrationStatus] = mapped_column(Enum(RegistrationStatus, native_enum=False, length=16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
