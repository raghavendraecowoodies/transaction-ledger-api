"""Application errors with a single, consistent JSON shape.

Every error response looks like:
    {"error": {"code": "INSUFFICIENT_BALANCE", "message": "...", "details": {...}}}
"""
from typing import Any, Optional


class AppError(Exception):
    status_code = 400
    code = "BAD_REQUEST"

    def __init__(self, message: str, details: Optional[Any] = None):
        super().__init__(message)
        self.message = message
        self.details = details


class AccountNotFound(AppError):
    status_code = 404
    code = "ACCOUNT_NOT_FOUND"


class InsufficientBalance(AppError):
    status_code = 422
    code = "INSUFFICIENT_BALANCE"


class DuplicateTransaction(AppError):
    status_code = 409
    code = "DUPLICATE_TRANSACTION"


class InvalidTransfer(AppError):
    status_code = 422
    code = "INVALID_TRANSFER"


def error_body(code: str, message: str, details: Any = None) -> dict:
    body = {"code": code, "message": message}
    if details is not None:
        body["details"] = details
    return {"error": body}
