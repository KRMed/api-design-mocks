"""Submit, read, validation, idempotency, and ETags."""

from __future__ import annotations

from tests.conftest import INITECH_AUTH, NORTHWIND_AUTH, submit


def test_submit_returns_202_and_pends(client, counterparty_id):
    response = submit(client, counterparty_id)
    assert response.status_code == 202
    body = response.get_json()
    assert body["status"] == "pending"
    assert body["attempts"] == 0
    assert response.headers["Location"].endswith(body["id"])


def test_submit_sets_an_etag(client, counterparty_id):
    response = submit(client, counterparty_id)
    assert response.headers["ETag"] == 'W/"1"'


def test_validation_collects_every_error(client):
    response = client.post(
        "/v1/transfers",
        json={"amount_minor": -5, "currency": "YEN"},
        headers=NORTHWIND_AUTH,
    )
    assert response.status_code == 400
    fields = {e["field"] for e in response.get_json()["error"]["errors"]}
    assert {"counterparty_id", "amount_minor", "currency"} <= fields


def test_amount_must_be_minor_units_not_a_float(client, counterparty_id):
    response = submit(client, counterparty_id, amount_minor=250.75)
    assert response.status_code == 400
    assert response.get_json()["error"]["errors"][0]["field"] == "amount_minor"


def test_amount_rejects_a_boolean(client, counterparty_id):
    response = submit(client, counterparty_id, amount_minor=True)
    assert response.status_code == 400


def test_amount_over_the_cap_is_rejected(client, counterparty_id):
    response = submit(client, counterparty_id, amount_minor=100_000_001)
    assert response.status_code == 400


def test_submit_rejects_unknown_counterparty(client):
    response = client.post(
        "/v1/transfers",
        json={"counterparty_id": "cp_missing", "amount_minor": 100, "currency": "USD"},
        headers=NORTHWIND_AUTH,
    )
    assert response.status_code == 404


def test_submit_rejects_another_accounts_counterparty(client, counterparty_id):
    response = client.post(
        "/v1/transfers",
        json={
            "counterparty_id": counterparty_id,
            "amount_minor": 100,
            "currency": "USD",
        },
        headers=INITECH_AUTH,
    )
    assert response.status_code == 404


def test_currency_mismatch_is_409(client):
    body = client.get("/v1/counterparties", headers=NORTHWIND_AUTH).get_json()
    euro = next(c for c in body["data"] if c["currency"] == "EUR")
    response = submit(client, euro["id"], currency="USD")
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "currency_mismatch"


def test_non_object_body_is_400(client):
    response = client.post("/v1/transfers", json=["nope"], headers=NORTHWIND_AUTH)
    assert response.status_code == 400


def test_long_memo_is_rejected(client, counterparty_id):
    response = submit(client, counterparty_id, memo="x" * 141)
    assert response.status_code == 400


def test_idempotent_replay_returns_the_same_transfer(client, counterparty_id):
    headers = {"Idempotency-Key": "payout-run-7"}

    first = submit(client, counterparty_id, headers=headers)
    second = submit(client, counterparty_id, headers=headers)

    assert first.get_json()["id"] == second.get_json()["id"]
    assert second.headers["Idempotent-Replay"] == "true"


def test_without_idempotency_key_two_submits_are_distinct(client, counterparty_id):
    first = submit(client, counterparty_id)
    second = submit(client, counterparty_id)
    assert first.get_json()["id"] != second.get_json()["id"]


def test_get_transfer_returns_etag(client, counterparty_id):
    created = submit(client, counterparty_id).get_json()
    response = client.get("/v1/transfers/" + created["id"], headers=NORTHWIND_AUTH)
    assert response.status_code == 200
    assert response.headers["ETag"] == 'W/"1"'


def test_get_unknown_transfer_is_404(client):
    response = client.get("/v1/transfers/tf_nope", headers=NORTHWIND_AUTH)
    assert response.status_code == 404


def test_filter_by_status(client, counterparty_id):
    submit(client, counterparty_id)
    response = client.get("/v1/transfers?status=settled", headers=NORTHWIND_AUTH)
    data = response.get_json()["data"]
    assert data
    assert all(t["status"] == "settled" for t in data)


def test_filter_by_counterparty(client, counterparty_id):
    response = client.get(
        "/v1/transfers?counterparty_id=" + counterparty_id, headers=NORTHWIND_AUTH
    )
    data = response.get_json()["data"]
    assert data
    assert all(t["counterparty_id"] == counterparty_id for t in data)


def test_serializer_hides_internal_fields(client, counterparty_id):
    body = submit(client, counterparty_id).get_json()
    assert "account_id" not in body
