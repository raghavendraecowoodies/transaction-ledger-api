"""SQLite setup.

Integrity is enforced in the database itself, not only in application code:
  * CHECK constraints: balance >= 0, amount > 0, type in (credit, debit)
  * UNIQUE (account_id, reference_id): same reference may repeat across accounts only
  * FOREIGN KEY transactions.account_id -> accounts.id
  * Triggers that make the transactions table append-only (no UPDATE / DELETE)

Money is stored as INTEGER minor units (paise) to avoid floating point errors.
"""
import os
import sqlite3
from typing import Iterator

DEFAULT_DB_PATH = "ledger.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL CHECK (length(trim(name)) > 0),
    balance     INTEGER NOT NULL DEFAULT 0 CHECK (balance >= 0),
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    id            TEXT PRIMARY KEY,
    account_id    TEXT NOT NULL REFERENCES accounts(id),
    type          TEXT NOT NULL CHECK (type IN ('credit', 'debit')),
    amount        INTEGER NOT NULL CHECK (amount > 0),
    description   TEXT NOT NULL DEFAULT '',
    reference_id  TEXT NOT NULL CHECK (length(trim(reference_id)) > 0),
    created_at    TEXT NOT NULL,
    UNIQUE (account_id, reference_id)
);

CREATE INDEX IF NOT EXISTS idx_tx_account_created
    ON transactions (account_id, created_at DESC);

CREATE TRIGGER IF NOT EXISTS transactions_no_update
BEFORE UPDATE ON transactions
BEGIN
    SELECT RAISE(ABORT, 'transactions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS transactions_no_delete
BEFORE DELETE ON transactions
BEGIN
    SELECT RAISE(ABORT, 'transactions are immutable');
END;
"""


def db_path() -> str:
    return os.environ.get("LEDGER_DB", DEFAULT_DB_PATH)


def connect() -> sqlite3.Connection:
    # isolation_level=None -> we control BEGIN/COMMIT explicitly in the service layer.
    conn = sqlite3.connect(db_path(), isolation_level=None, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()


def get_conn() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: one connection per request."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()
