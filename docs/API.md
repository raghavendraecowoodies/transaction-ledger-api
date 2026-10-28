# API Documentation

Base URL: `http://127.0.0.1:8000` · Content type: `application/json`
Interactive Swagger UI is also served at `/docs` and the OpenAPI schema at `/openapi.json`.

All error responses share this shape:

```json
{ "error": { "code": "INSUFFICIENT_BALANCE", "message": "Insufficient balance for this debit", "details": { } } }
```
(`details` is optional.)

---

## 1. Create account

`POST /accounts`

Request:
```json
{ "name": "Alice" }
```
- `name` required, non-blank, max 100 chars.
- Any other field (e.g. `balance`) is rejected with 422. Balance always starts at 0.

Response `201 Created`:
```json
{
  "id": "936d03d5-f98b-4fa4-a89c-18983625eaeb",
  "name": "Alice",
  "balance": 0.0,
  "created_at": "2026-10-28T05:32:35.823085+00:00"
}
```
Errors: `422 VALIDATION_ERROR`, `400 INVALID_REQUEST_BODY`.

## 2. Get account

`GET /accounts/{account_id}` → `200` account object (same as above), or `404 ACCOUNT_NOT_FOUND`.

## 3. Get balance

`GET /accounts/{account_id}/balance`

Response `200`:
```json
{ "account_id": "936d03d5-...", "balance": 100.0 }
```
Errors: `404 ACCOUNT_NOT_FOUND`.

## 4. Add transaction

`POST /accounts/{account_id}/transactions`

Request:
```json
{
  "type": "credit",
  "amount": 100.50,
  "reference_id": "salary-oct-2026",
  "description": "October salary"
}
```
| Field | Rules |
|---|---|
| `type` | required, `credit` or `debit` |
| `amount` | required, > 0, max 2 decimal places |
| `reference_id` | required, non-blank, max 100 chars, unique per account |
| `description` | optional, max 500 chars |

Response `201 Created`:
```json
{
  "transaction": {
    "id": "3f867236-...",
    "account_id": "936d03d5-...",
    "type": "credit",
    "amount": 100.5,
    "description": "October salary",
    "reference_id": "salary-oct-2026",
    "created_at": "2026-10-28T05:32:35.851848+00:00"
  },
  "balance": 100.5
}
```

Errors:

| Status | Code | When |
|---|---|---|
| 404 | `ACCOUNT_NOT_FOUND` | account does not exist |
| 422 | `VALIDATION_ERROR` | bad type / amount / missing reference_id, unknown fields |
| 422 | `INSUFFICIENT_BALANCE` | debit larger than balance (balance unchanged) |
| 409 | `DUPLICATE_TRANSACTION` | `reference_id` already used on this account |
| 400 | `INVALID_REQUEST_BODY` | malformed JSON |

Insufficient balance example (`422`):
```json
{ "error": { "code": "INSUFFICIENT_BALANCE", "message": "Insufficient balance for this debit",
  "details": { "available_balance": 100.0, "requested_amount": 150.0 } } }
```

Duplicate example (`409`): no new transaction is created and the balance is unchanged. The original transaction is returned:
```json
{ "error": { "code": "DUPLICATE_TRANSACTION",
  "message": "A transaction with reference_id 'salary-oct-2026' already exists for this account",
  "details": { "transaction": { "id": "3f867236-...", "...": "..." }, "balance": 100.5 } } }
```

## 5. Transaction history

`GET /accounts/{account_id}/transactions?type=&page=&limit=`

| Param | Default | Rules |
|---|---|---|
| `type` | (both) | `credit` or `debit` |
| `page` | 1 | integer ≥ 1 |
| `limit` | 10 | integer 1–100 |

Sorted latest first by `created_at`.

Response `200`:
```json
{
  "account_id": "936d03d5-...",
  "data": [ { "id": "...", "account_id": "...", "type": "debit", "amount": 30.0,
              "description": "", "reference_id": "r3", "created_at": "..." } ],
  "pagination": { "page": 1, "limit": 10, "total_records": 25, "total_pages": 3 }
}
```
Errors: `404 ACCOUNT_NOT_FOUND`, `400 INVALID_QUERY_PARAMETER` (bad page/limit/type).

## 6. Immutability

There are no update or delete endpoints for transactions. `PUT`/`PATCH`/`DELETE` on transaction URLs return `405`/`404` in the standard error format. The database also blocks any `UPDATE`/`DELETE` on the `transactions` table with triggers.

## 7. Bonus: Transfer

`POST /transfers`

Request:
```json
{
  "source_account_id": "AAA...",
  "destination_account_id": "BBB...",
  "amount": 30,
  "reference_id": "transfer-001",
  "description": "Rent share"
}
```

Response `201 Created`:
```json
{
  "reference_id": "transfer-001",
  "amount": 30.0,
  "source":      { "account_id": "AAA...", "balance": 70.0, "transaction": { "type": "debit",  "...": "..." } },
  "destination": { "account_id": "BBB...", "balance": 80.0, "transaction": { "type": "credit", "...": "..." } }
}
```

Both legs run in one database transaction: if either fails, nothing is saved.

Errors: `404 ACCOUNT_NOT_FOUND` (either account), `422 INSUFFICIENT_BALANCE`, `422 INVALID_TRANSFER` (same source and destination), `409 DUPLICATE_TRANSACTION` (reference already used on either account), `422 VALIDATION_ERROR`.
