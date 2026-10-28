# Transaction Ledger API

A small backend for a transaction ledger: create accounts, post credits/debits, read balances and paginated history, and (bonus) transfer money atomically between accounts.

**Stack:** Python 3.10+, FastAPI, SQLite (standard library `sqlite3`), pytest.

## Run locally

```bash
# 1. (recommended) create a virtual environment
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 2. install dependencies
pip install -r requirements.txt

# 3. start the server (the database file ledger.db is created automatically)
uvicorn app.main:app --reload
```

- API base URL: `http://127.0.0.1:8000`
- Interactive docs (Swagger UI): `http://127.0.0.1:8000/docs`
- Full API reference: [`docs/API.md`](docs/API.md)
- Use another DB file with `LEDGER_DB=/path/to/file.db uvicorn app.main:app`

## Run the tests

```bash
pytest
```

40 tests cover every rule in the brief: validation, duplicates, insufficient balance, filtering/sorting/pagination, immutability, atomic rollback, concurrent debits, and transfers.

## Quick try-out

```bash
curl -X POST localhost:8000/accounts -H 'content-type: application/json' -d '{"name":"Alice"}'
# copy the "id" from the response, then:
curl -X POST localhost:8000/accounts/<ID>/transactions -H 'content-type: application/json' \
  -d '{"type":"credit","amount":100,"reference_id":"salary-oct","description":"Salary"}'
curl localhost:8000/accounts/<ID>/balance
curl "localhost:8000/accounts/<ID>/transactions?type=credit&page=1&limit=10"
```

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/accounts` | Create account (balance always starts at 0) |
| GET | `/accounts/{id}` | Get account |
| GET | `/accounts/{id}/balance` | Get current balance |
| POST | `/accounts/{id}/transactions` | Add credit / debit |
| GET | `/accounts/{id}/transactions` | History: `type`, `page`, `limit`, latest first |
| POST | `/transfers` | Bonus: atomic transfer between two accounts |
| GET | `/health` | Health check |

There are intentionally **no** PUT/PATCH/DELETE routes for transactions.

## Project structure

```
app/
  main.py       routes + error handlers
  schemas.py    request validation (Pydantic)
  services.py   business logic, atomic transactions
  db.py         schema, constraints, triggers, connection handling
  errors.py     application errors + consistent error body
tests/
  test_api.py   pytest suite
docs/API.md     API documentation
```

## Design decisions

**Atomicity.** Every write runs in a single SQLite transaction started with `BEGIN IMMEDIATE`. Inserting the transaction row and updating the balance either both commit or both roll back (a test simulates a crash between the two steps). A transfer is one DB transaction containing both legs, so a partial transfer is impossible.

**Concurrency.** `BEGIN IMMEDIATE` takes the write lock before the balance is read, and debits use a guarded `UPDATE ... WHERE balance >= amount`. Concurrent debits therefore cannot overdraw an account (tested with 10 parallel debits).

**Data integrity enforced in the database, not only in code:**
- `CHECK (balance >= 0)`, `CHECK (amount > 0)`, `CHECK (type IN ('credit','debit'))`
- `UNIQUE (account_id, reference_id)`: the same reference may repeat across accounts, never within one
- `FOREIGN KEY` from transactions to accounts
- Triggers that abort any `UPDATE` or `DELETE` on `transactions` (immutability even for direct DB access)

**Money.** Stored as integer paise (minor units) so there are no floating-point errors. The API accepts amounts with at most 2 decimal places and returns numbers (e.g. `100.0`).

**Duplicates.** A repeated `reference_id` on the same account returns **409 `DUPLICATE_TRANSACTION`** with the original transaction and current balance in `details`. Nothing new is created and the balance does not change.

**Errors** always have one shape: `{"error": {"code", "message", "details"?}}`.

| Situation | Status | Code |
|---|---|---|
| Account not found / unknown route | 404 | `ACCOUNT_NOT_FOUND` / `NOT_FOUND` |
| Invalid body fields (type, amount, name, reference_id, unknown fields) | 422 | `VALIDATION_ERROR` |
| Malformed JSON | 400 | `INVALID_REQUEST_BODY` |
| Bad `page` / `limit` / `type` query | 400 | `INVALID_QUERY_PARAMETER` |
| Insufficient balance | 422 | `INSUFFICIENT_BALANCE` |
| Duplicate `reference_id` | 409 | `DUPLICATE_TRANSACTION` |
| Invalid transfer (same account) | 422 | `INVALID_TRANSFER` |
| Edit/delete a transaction | 405 | `METHOD_NOT_ALLOWED` |
| Unexpected failure | 500 | `INTERNAL_ERROR` |

## Assumptions

1. Single currency; amounts have at most 2 decimal places (rupee/paise style).
2. Account and transaction IDs are server-generated UUIDs. A malformed or unknown account ID returns 404 `ACCOUNT_NOT_FOUND`.
3. The client may not send extra fields. For example, `{"name": "A", "balance": 500}` is rejected with 422 rather than silently ignored.
4. `description` is optional (defaults to an empty string); `name`, `type`, `amount` and `reference_id` are required. Leading/trailing whitespace is trimmed.
5. A rejected transaction (insufficient balance, validation error) is not stored and does not consume its `reference_id`, so it can be retried.
6. Duplicate submissions return 409 (not 200) so clients can distinguish "created" from "already existed". The original transaction is included so retries are safe to handle.
7. Pagination defaults: `page=1`, `limit=10`, max `limit=100`. A page beyond the last returns an empty list with correct totals.
8. Transfer: the same `reference_id` is used for both legs (it is unique per account, so this is valid). Reusing a reference on either account fails the whole transfer. Transferring to the same account is rejected.
9. No authentication or authorisation: out of scope for this assignment.
10. SQLite is used for easy local setup. It serialises writes, which is what makes the balance logic simple and safe here. For production at scale, PostgreSQL with row-level locking (`SELECT ... FOR UPDATE`) would be the natural move, using the same constraints.
11. Timestamps are UTC, ISO 8601 with microseconds.
