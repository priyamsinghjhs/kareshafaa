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
