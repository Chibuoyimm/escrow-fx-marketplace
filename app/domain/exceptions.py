"""Application and domain exception types."""

from __future__ import annotations

from dataclasses import dataclass


class ErrorCode:
    """Stable machine-readable error codes."""

    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    VALIDATION_ERROR = "validation_error"
    AUTHORIZATION_ERROR = "authorization_error"
    PRECONDITION_FAILED = "precondition_failed"
    INVARIANT_VIOLATION = "invariant_violation"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"
    INTERNAL_ERROR = "internal_error"


@dataclass(slots=True)
class AppError(Exception):
    """Base class for expected application errors."""

    title: str
    detail: str
    error_code: str
    status_code: int


class NotFoundError(AppError):
    """Raised when a resource cannot be found."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            title="Resource Not Found",
            detail=detail,
            error_code=ErrorCode.NOT_FOUND,
            status_code=404,
        )


class ConflictError(AppError):
    """Raised when a resource conflicts with existing state."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            title="Conflict",
            detail=detail,
            error_code=ErrorCode.CONFLICT,
            status_code=409,
        )


class AuthorizationError(AppError):
    """Raised when a caller is not allowed to perform an action."""

    def __init__(self, detail: str = "You are not authorized to perform this action.") -> None:
        super().__init__(
            title="Authorization Error",
            detail=detail,
            error_code=ErrorCode.AUTHORIZATION_ERROR,
            status_code=403,
        )


class PreconditionFailedError(AppError):
    """Raised when a precondition blocks an action."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            title="Precondition Failed",
            detail=detail,
            error_code=ErrorCode.PRECONDITION_FAILED,
            status_code=412,
        )


class InvariantViolationError(AppError):
    """Raised when domain invariants are violated."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            title="Invariant Violation",
            detail=detail,
            error_code=ErrorCode.INVARIANT_VIOLATION,
            status_code=422,
        )

