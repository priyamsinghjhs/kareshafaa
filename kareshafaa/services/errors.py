from __future__ import annotations


class DomainError(Exception):
    status_code = 409

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class Forbidden(DomainError):
    status_code = 403


class NotFound(DomainError):
    status_code = 404


class Invalid(DomainError):
    status_code = 422


class ServiceBusy(Exception):
    """The server is saturated (no DB connection / writer lock within the configured wait). Answered with 503 and
    Retry-After; nothing was saved, so the client can safely retry (round 21: graceful degradation)."""

    def __init__(self, message: str = "the service is busy - nothing was saved; please retry shortly"):
        super().__init__(message)
        self.message = message
