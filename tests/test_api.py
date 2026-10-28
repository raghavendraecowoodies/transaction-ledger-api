import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def db_file(tmp_path, monkeypatch):
    path = str(tmp_path / "test.db")
    monkeypatch.setenv("LEDGER_DB", path)
    return path


@pytest.fixture()
def client(db_file):
    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def make_account(client, name="Alice"):
    r = client.post("/accounts", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


def tx(client, account_id, type_, amount, ref, description="t"):
    return client.post(
        f"/accounts/{account_id}/transactions",
        json={"type": type_, "amount": amount, "reference_id": ref, "description": description},
    )


def balance(client, account_id):
    return client.get(f"/accounts/{account_id}/balance").json()["balance"]


# ---------- accounts ----------

def test_create_account(client):
    acc = make_account(client, "  Alice  ")
    assert acc["name"] == "Alice"
    assert acc["balance"] == 0
    assert acc["id"] and acc["created_at"]


def test_account_ids_are_unique(client):
    assert make_account(client)["id"] != make_account(client)["id"]


@pytest.mark.parametrize("body", [{}, {"name": ""}, {"name": "   "}, {"name": 123}])
def test_account_name_required(client, body):
    r = client.post("/accounts", json=body)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_client_cannot_set_initial_balance(client):
    r = client.post("/accounts", json={"name": "Bob", "balance": 500})
    assert r.status_code == 422


def test_invalid_json_body(client):
    r = client.post("/accounts", content="{not json", headers={"content-type": "application/json"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_REQUEST_BODY"


# ---------- transactions ----------

def test_credit_and_debit(client):
    a = make_account(client)["id"]
    r = tx(client, a, "credit", 100, "r1")
    assert r.status_code == 201
    assert r.json()["balance"] == 100
    assert r.json()["transaction"]["type"] == "credit"
    assert tx(client, a, "credit", 50, "r2").json()["balance"] == 150
    assert tx(client, a, "debit", 30, "r3").json()["balance"] == 120
    assert balance(client, a) == 120


def test_balance_consistency_example_from_brief(client):
    a = make_account(client)["id"]
    for i, (t, amt) in enumerate([("credit", 100), ("credit", 50), ("debit", 30), ("debit", 20)]):
        assert tx(client, a, t, amt, f"r{i}").status_code == 201
    assert balance(client, a) == 100


def test_decimal_amounts(client):
    a = make_account(client)["id"]
    tx(client, a, "credit", 10.10, "r1")
    tx(client, a, "credit", 0.20, "r2")
    assert balance(client, a) == 10.30


def test_insufficient_balance_rejected_and_balance_unchanged(client):
    a = make_account(client)["id"]
    tx(client, a, "credit", 100, "r1")
    r = tx(client, a, "debit", 150, "r2")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INSUFFICIENT_BALANCE"
    assert balance(client, a) == 100
    # the rejected reference was not consumed: it can be reused with a valid amount
    assert tx(client, a, "debit", 40, "r2").status_code == 201
    history = client.get(f"/accounts/{a}/transactions").json()
    assert history["pagination"]["total_records"] == 2


def test_duplicate_reference_same_account(client):
    a = make_account(client)["id"]
    first = tx(client, a, "credit", 100, "dup")
    assert first.status_code == 201
    again = tx(client, a, "credit", 100, "dup")
    assert again.status_code == 409
    body = again.json()["error"]
    assert body["code"] == "DUPLICATE_TRANSACTION"
    assert body["details"]["transaction"]["id"] == first.json()["transaction"]["id"]
    assert balance(client, a) == 100
    assert client.get(f"/accounts/{a}/transactions").json()["pagination"]["total_records"] == 1


def test_same_reference_allowed_on_different_accounts(client):
    a, b = make_account(client)["id"], make_account(client)["id"]
    assert tx(client, a, "credit", 10, "same").status_code == 201
    assert tx(client, b, "credit", 10, "same").status_code == 201


def test_transaction_validation(client):
    a = make_account(client)["id"]
    bad = [
        {"type": "refund", "amount": 10, "reference_id": "x"},
        {"type": "credit", "amount": 0, "reference_id": "x"},
        {"type": "credit", "amount": -5, "reference_id": "x"},
        {"type": "credit", "amount": "abc", "reference_id": "x"},
        {"type": "credit", "amount": 1.234, "reference_id": "x"},
        {"type": "credit", "amount": 10},
        {"type": "credit", "amount": 10, "reference_id": "  "},
        {"amount": 10, "reference_id": "x"},
    ]
    for body in bad:
        r = client.post(f"/accounts/{a}/transactions", json=body)
        assert r.status_code == 422, body
        assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert balance(client, a) == 0


def test_transaction_unknown_account(client):
    r = tx(client, "does-not-exist", "credit", 10, "r1")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"


# ---------- balance ----------

def test_balance_response_and_not_found(client):
    a = make_account(client)["id"]
    r = client.get(f"/accounts/{a}/balance")
    assert r.status_code == 200
    assert r.json() == {"account_id": a, "balance": 0}
    r = client.get("/accounts/nope/balance")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"


# ---------- history ----------

def test_history_filter_sort_pagination(client):
    a = make_account(client)["id"]
    tx(client, a, "credit", 500, "c1")
    for i in range(1, 6):
        tx(client, a, "debit", 10, f"d{i}")
    tx(client, a, "credit", 5, "c2")

    all_ = client.get(f"/accounts/{a}/transactions?limit=100").json()
    refs = [t["reference_id"] for t in all_["data"]]
    assert refs == ["c2", "d5", "d4", "d3", "d2", "d1", "c1"]  # latest first
    assert all_["pagination"] == {"page": 1, "limit": 100, "total_records": 7, "total_pages": 1}

    debits = client.get(f"/accounts/{a}/transactions?type=debit").json()
    assert debits["pagination"]["total_records"] == 5
    assert {t["type"] for t in debits["data"]} == {"debit"}

    credits = client.get(f"/accounts/{a}/transactions?type=credit").json()
    assert [t["reference_id"] for t in credits["data"]] == ["c2", "c1"]

    p2 = client.get(f"/accounts/{a}/transactions?limit=3&page=2").json()
    assert [t["reference_id"] for t in p2["data"]] == ["d3", "d2", "d1"]
    assert p2["pagination"] == {"page": 2, "limit": 3, "total_records": 7, "total_pages": 3}

    beyond = client.get(f"/accounts/{a}/transactions?limit=3&page=9").json()
    assert beyond["data"] == []


def test_history_empty_account(client):
    a = make_account(client)["id"]
    body = client.get(f"/accounts/{a}/transactions").json()
    assert body["data"] == []
    assert body["pagination"]["total_records"] == 0
    assert body["pagination"]["total_pages"] == 0


@pytest.mark.parametrize(
    "qs", ["page=0", "page=-1", "page=abc", "limit=0", "limit=101", "limit=x", "type=refund"]
)
def test_history_invalid_params(client, qs):
    a = make_account(client)["id"]
    r = client.get(f"/accounts/{a}/transactions?{qs}")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_QUERY_PARAMETER"


def test_history_unknown_account(client):
    assert client.get("/accounts/nope/transactions").status_code == 404


# ---------- immutability ----------

def test_transactions_cannot_be_edited_or_deleted_via_api(client):
    a = make_account(client)["id"]
    t = tx(client, a, "credit", 10, "r1").json()["transaction"]
    for method in ("put", "patch", "delete"):
        for url in (f"/accounts/{a}/transactions/{t['id']}", f"/accounts/{a}/transactions"):
            r = getattr(client, method)(url)
            assert r.status_code in (404, 405), (method, url, r.status_code)
    assert balance(client, a) == 10


def test_database_blocks_update_and_delete(client, db_file):
    a = make_account(client)["id"]
    tx(client, a, "credit", 10, "r1")
    conn = sqlite3.connect(db_file)
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        conn.execute("UPDATE transactions SET amount = 999")
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        conn.execute("DELETE FROM transactions")
    conn.close()


def test_database_constraints(client, db_file):
    a = make_account(client)["id"]
    conn = sqlite3.connect(db_file)
    conn.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE accounts SET balance = -1 WHERE id = ?", (a,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO transactions VALUES ('x', 'ghost', 'credit', 1, '', 'r', 'now')"
        )
    conn.close()


# ---------- atomicity / concurrency ----------

def test_failure_after_balance_update_rolls_back(client, db_file, monkeypatch):
    """If inserting the transaction row fails, the balance update must not persist."""
    from app import services

    a = make_account(client)["id"]
    tx(client, a, "credit", 100, "r1")

    original_uuid = services.uuid.uuid4

    def boom():
        raise RuntimeError("simulated crash between balance update and insert")

    monkeypatch.setattr(services.uuid, "uuid4", boom)
    r = tx(client, a, "credit", 50, "r2")
    monkeypatch.setattr(services.uuid, "uuid4", original_uuid)

    assert r.status_code == 500
    assert r.json()["error"]["code"] == "INTERNAL_ERROR"
    assert balance(client, a) == 100
    assert client.get(f"/accounts/{a}/transactions").json()["pagination"]["total_records"] == 1


def test_concurrent_debits_never_overdraw(client):
    a = make_account(client)["id"]
    tx(client, a, "credit", 100, "seed")

    def debit(i):
        return tx(client, a, "debit", 30, f"d{i}").status_code

    with ThreadPoolExecutor(max_workers=10) as pool:
        codes = list(pool.map(debit, range(10)))

    assert codes.count(201) == 3
    assert balance(client, a) == 10


def test_concurrent_duplicate_reference_creates_one(client):
    a = make_account(client)["id"]

    def credit(_):
        return tx(client, a, "credit", 10, "same-ref").status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(credit, range(8)))

    assert codes.count(201) == 1
    assert codes.count(409) == 7
    assert balance(client, a) == 10


# ---------- bonus: transfers ----------

def transfer(client, src, dst, amount, ref="t1"):
    return client.post(
        "/transfers",
        json={"source_account_id": src, "destination_account_id": dst, "amount": amount, "reference_id": ref},
    )


def test_transfer_success(client):
    a, b = make_account(client, "A")["id"], make_account(client, "B")["id"]
    tx(client, a, "credit", 100, "seed-a")
    tx(client, b, "credit", 50, "seed-b")
    r = transfer(client, a, b, 30)
    assert r.status_code == 201
    assert balance(client, a) == 70
    assert balance(client, b) == 80
    assert r.json()["source"]["transaction"]["type"] == "debit"
    assert r.json()["destination"]["transaction"]["type"] == "credit"


def test_transfer_insufficient_funds_changes_nothing(client):
    a, b = make_account(client)["id"], make_account(client)["id"]
    tx(client, a, "credit", 20, "seed")
    r = transfer(client, a, b, 30)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INSUFFICIENT_BALANCE"
    assert balance(client, a) == 20
    assert balance(client, b) == 0


def test_transfer_unknown_accounts(client):
    a = make_account(client)["id"]
    tx(client, a, "credit", 50, "seed")
    assert transfer(client, a, "ghost", 10).status_code == 404
    assert transfer(client, "ghost", a, 10).status_code == 404
    assert balance(client, a) == 50


def test_transfer_to_same_account_rejected(client):
    a = make_account(client)["id"]
    tx(client, a, "credit", 50, "seed")
    r = transfer(client, a, a, 10)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_TRANSFER"


def test_transfer_duplicate_reference_is_not_applied_twice(client):
    a, b = make_account(client)["id"], make_account(client)["id"]
    tx(client, a, "credit", 100, "seed")
    assert transfer(client, a, b, 30, "same").status_code == 201
    assert transfer(client, a, b, 30, "same").status_code == 409
    assert balance(client, a) == 70
    assert balance(client, b) == 30


def test_transfer_rolls_back_if_destination_leg_fails(client):
    """Destination already used this reference_id -> whole transfer must roll back."""
    a, b = make_account(client)["id"], make_account(client)["id"]
    tx(client, a, "credit", 100, "seed")
    tx(client, b, "credit", 5, "clash")
    r = transfer(client, a, b, 30, "clash")
    assert r.status_code == 409
    assert balance(client, a) == 100  # debit leg rolled back
    assert balance(client, b) == 5


# ---------- misc ----------

def test_unknown_route_uses_error_format(client):
    r = client.get("/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"
