"""FastAPI application: routes and error handlers."""
import logging
import sqlite3
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import services
from .db import get_conn, init_db
from .errors import AppError, error_body
from .schemas import AccountCreate, TransactionCreate, TransferCreate, TxType

logger = logging.getLogger("ledger")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Transaction Ledger API",
    version="1.0.0",
    description="Simple transaction ledger: accounts, credits/debits, history and transfers.",
    lifespan=lifespan,
)


# ---------------- error handlers (consistent shape) ----------------

@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(status_code=exc.status_code, content=error_body(exc.code, exc.message, exc.details))


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()

    if any(e["type"] == "json_invalid" for e in errors):
        return JSONResponse(
            status_code=400,
            content=error_body("INVALID_REQUEST_BODY", "Request body is not valid JSON"),
        )

    details = [
        {
            "field": ".".join(str(p) for p in e["loc"][1:]) or str(e["loc"][0]),
            "message": e["msg"].removeprefix("Value error, "),
        }
        for e in errors
    ]
    in_query = all(e["loc"] and e["loc"][0] == "query" for e in errors)
    if in_query:
        return JSONResponse(
            status_code=400,
            content=error_body("INVALID_QUERY_PARAMETER", "Invalid query parameters", details),
        )
    return JSONResponse(
        status_code=422,
        content=error_body("VALIDATION_ERROR", "Request validation failed", details),
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    codes = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}
    message = {
        404: "The requested resource was not found",
        405: "Method not allowed. Transactions are immutable: they cannot be edited or deleted.",
    }.get(exc.status_code, str(exc.detail))
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(codes.get(exc.status_code, "HTTP_ERROR"), message),
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(sqlite3.IntegrityError)
async def integrity_error_handler(request: Request, exc: sqlite3.IntegrityError):
    # Last line of defence: a DB constraint fired that application code did not catch.
    logger.warning("Integrity error: %s", exc)
    return JSONResponse(
        status_code=409,
        content=error_body("INTEGRITY_ERROR", "The operation violates a data integrity constraint"),
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error")
    return JSONResponse(
        status_code=500,
        content=error_body("INTERNAL_ERROR", "An unexpected error occurred"),
    )


# ---------------- routes ----------------

@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}


@app.post("/accounts", status_code=201, tags=["accounts"])
def create_account(body: AccountCreate, conn: sqlite3.Connection = Depends(get_conn)):
    return services.create_account(conn, body.name)


@app.get("/accounts/{account_id}", tags=["accounts"])
def get_account(account_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    return services.get_account(conn, account_id)


@app.get("/accounts/{account_id}/balance", tags=["accounts"])
def get_balance(account_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    return services.get_balance(conn, account_id)


@app.post("/accounts/{account_id}/transactions", status_code=201, tags=["transactions"])
def add_transaction(account_id: str, body: TransactionCreate, conn: sqlite3.Connection = Depends(get_conn)):
    return services.add_transaction(
        conn, account_id, body.type.value, body.amount, body.description, body.reference_id
    )


@app.get("/accounts/{account_id}/transactions", tags=["transactions"])
def list_transactions(
    account_id: str,
    type: Optional[TxType] = Query(None, description="Filter by credit or debit"),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    conn: sqlite3.Connection = Depends(get_conn),
):
    return services.list_transactions(conn, account_id, type.value if type else None, page, limit)


@app.post("/transfers", status_code=201, tags=["transfers"])
def create_transfer(body: TransferCreate, conn: sqlite3.Connection = Depends(get_conn)):
    return services.transfer(
        conn,
        body.source_account_id,
        body.destination_account_id,
        body.amount,
        body.description,
        body.reference_id,
    )
