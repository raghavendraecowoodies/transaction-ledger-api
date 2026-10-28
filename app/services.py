"""Business logic. All balance-changing work happens inside one DB transaction.

Atomicity / concurrency strategy
--------------------------------
Every write runs inside `atomic()`, which issues BEGIN IMMEDIATE. SQLite then takes
the write lock up front, so two concurrent debits can never both read the same
balance and overdraw the account. If anything raises, the whole transaction is
rolled back: the transaction row and the balance update are persisted together or
not at all.
"""
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from math import ceil
from typing import Optional

from .errors import (
    AccountNotFound,
    DuplicateTransaction,
    InsufficientBalance,
    InvalidTransfer,
)


# ---------- helpers ----------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def to_minor(amount: Decimal) -> int:
    return int((amount * 100).to_integral_value())


def from_minor(minor: int) -> float:
    return round(minor / 100, 2)


def account_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "balance": from_minor(row["balance"]),
        "created_at": row["created_at"],
    }


def tx_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "account_id": row["account_id"],
        "type": row["type"],
        "amount": from_minor(row["amount"]),
        "description": row["description"],
        "reference_id": row["reference_id"],
        "created_at": row["created_at"],
    }


@contextmanager
def atomic(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _get_account_row(conn: sqlite3.Connection, account_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if row is None:
        raise AccountNotFound(f"Account '{account_id}' was not found")
    return row


# ---------- accounts ----------

def create_account(conn: sqlite3.Connection, name: str) -> dict:
    account_id = str(uuid.uuid4())
    # Balance is never taken from the client: it always starts at 0.
    with atomic(conn):
        conn.execute(
            "INSERT INTO accounts (id, name, balance, created_at) VALUES (?, ?, 0, ?)",
            (account_id, name, now_iso()),
        )
    return account_dict(_get_account_row(conn, account_id))


def get_account(conn: sqlite3.Connection, account_id: str) -> dict:
    return account_dict(_get_account_row(conn, account_id))


def get_balance(conn: sqlite3.Connection, account_id: str) -> dict:
    row = _get_account_row(conn, account_id)
    return {"account_id": row["id"], "balance": from_minor(row["balance"])}


# ---------- transactions ----------

def _apply_transaction(
    conn: sqlite3.Connection,
    account_id: str,
    tx_type: str,
    amount_minor: int,
    description: str,
    reference_id: str,
) -> dict:
    """Insert one transaction and update the balance. MUST run inside atomic()."""
    account = _get_account_row(conn, account_id)

    existing = conn.execute(
        "SELECT * FROM transactions WHERE account_id = ? AND reference_id = ?",
        (account_id, reference_id),
    ).fetchone()
    if existing is not None:
        raise DuplicateTransaction(
            f"A transaction with reference_id '{reference_id}' already exists for this account",
            details={
                "transaction": tx_dict(existing),
                "balance": from_minor(account["balance"]),
            },
        )

    if tx_type == "debit":
        # Guarded update: succeeds only if enough balance remains (belt and braces
        # together with the CHECK (balance >= 0) constraint).
        cur = conn.execute(
            "UPDATE accounts SET balance = balance - ? WHERE id = ? AND balance >= ?",
            (amount_minor, account_id, amount_minor),
        )
        if cur.rowcount == 0:
            raise InsufficientBalance(
                "Insufficient balance for this debit",
                details={
                    "available_balance": from_minor(account["balance"]),
                    "requested_amount": from_minor(amount_minor),
                },
            )
    else:
        conn.execute(
            "UPDATE accounts SET balance = balance + ? WHERE id = ?",
            (amount_minor, account_id),
        )

    tx_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO transactions (id, account_id, type, amount, description, reference_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (tx_id, account_id, tx_type, amount_minor, description, reference_id, now_iso()),
    )
    return tx_dict(conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone())


def add_transaction(
    conn: sqlite3.Connection,
    account_id: str,
    tx_type: str,
    amount: Decimal,
    description: str,
    reference_id: str,
) -> dict:
    with atomic(conn):
        tx = _apply_transaction(conn, account_id, tx_type, to_minor(amount), description, reference_id)
    balance = get_balance(conn, account_id)["balance"]
    return {"transaction": tx, "balance": balance}


def list_transactions(
    conn: sqlite3.Connection,
    account_id: str,
    tx_type: Optional[str],
    page: int,
    limit: int,
) -> dict:
    _get_account_row(conn, account_id)

    where, params = "account_id = ?", [account_id]
    if tx_type:
        where += " AND type = ?"
        params.append(tx_type)

    total = conn.execute(f"SELECT COUNT(*) FROM transactions WHERE {where}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM transactions WHERE {where} "
        "ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
        params + [limit, (page - 1) * limit],
    ).fetchall()

    return {
        "account_id": account_id,
        "data": [tx_dict(r) for r in rows],
        "pagination": {
            "page": page,
            "limit": limit,
            "total_records": total,
            "total_pages": ceil(total / limit) if total else 0,
        },
    }


# ---------- bonus: transfer ----------

def transfer(
    conn: sqlite3.Connection,
    source_id: str,
    destination_id: str,
    amount: Decimal,
    description: str,
    reference_id: str,
) -> dict:
    if source_id == destination_id:
        raise InvalidTransfer("Source and destination accounts must be different")

    amount_minor = to_minor(amount)
    note = description or "Transfer"

    # One DB transaction for both legs: any failure rolls back everything.
    with atomic(conn):
        _get_account_row(conn, source_id)
        _get_account_row(conn, destination_id)
        debit = _apply_transaction(
            conn, source_id, "debit", amount_minor, f"{note} (to {destination_id})", reference_id
        )
        credit = _apply_transaction(
            conn, destination_id, "credit", amount_minor, f"{note} (from {source_id})", reference_id
        )

    return {
        "reference_id": reference_id,
        "amount": from_minor(amount_minor),
        "source": {"account_id": source_id, "balance": get_balance(conn, source_id)["balance"], "transaction": debit},
        "destination": {
            "account_id": destination_id,
            "balance": get_balance(conn, destination_id)["balance"],
            "transaction": credit,
        },
    }
