"""Domain errors. Each carries a stable machine-readable code and an HTTP status."""

from typing import Any


class DomainError(Exception):
    code: str = "DOMAIN_ERROR"
    http_status: int = 400

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class OfferingNotFound(DomainError):
    code = "OFFERING_NOT_FOUND"
    http_status = 404


class LearnerNotFound(DomainError):
    code = "LEARNER_NOT_FOUND"
    http_status = 404


class UsernameTaken(DomainError):
    code = "USERNAME_TAKEN"
    http_status = 409


class RegistrationNotFound(DomainError):
    code = "NOT_REGISTERED"
    http_status = 404


class DuplicateOfferingCode(DomainError):
    code = "OFFERING_CODE_EXISTS"
    http_status = 409


class PrerequisitesNotMet(DomainError):
    code = "PREREQUISITES_NOT_MET"
    http_status = 422


class AlreadyEnrolled(DomainError):
    code = "ALREADY_ENROLLED"
    http_status = 409


class AlreadyWaitlisted(DomainError):
    code = "ALREADY_WAITLISTED"
    http_status = 409


class DuplicateRegistration(DomainError):
    """Backstop when the database unique constraint rejects a second registration."""

    code = "ALREADY_REGISTERED"
    http_status = 409


class ReenrollmentNotAllowed(DomainError):
    code = "REENROLLMENT_NOT_ALLOWED"
    http_status = 409


class NotEnrolled(DomainError):
    code = "NOT_ENROLLED"
    http_status = 409


class AlreadyDropped(DomainError):
    code = "ALREADY_DROPPED"
    http_status = 409


class NotWaitlisted(DomainError):
    code = "NOT_WAITLISTED"
    http_status = 409


class AlreadyWithdrawn(DomainError):
    code = "ALREADY_WITHDRAWN"
    http_status = 409
