"""Bulk posting ingest: partial success, per-item indices, scoping."""

from __future__ import annotations

from tests.conftest import INITECH_AUTH, NORTHWIND_AUTH, submit


def ingest(client, postings, headers=None):
    return client.post(
        "/v1/postings", json={"postings": postings}, headers=headers or NORTHWIND_AUTH
    )


def test_ingest_returns_202(client, counterparty_id):
    transfer = submit(client, counterparty_id).get_json()
    response = ingest(
        client, [{"transfer_id": transfer["id"], "type": "transfer.settled"}]
    )
    assert response.status_code == 202
    body = response.get_json()
    assert body["accepted"] == 1
    assert body["results"][0]["index"] == 0


def test_partial_success_reports_the_failing_index(client, counterparty_id):
    transfer = submit(client, counterparty_id).get_json()
    response = ingest(
        client,
        [
            {"transfer_id": transfer["id"], "type": "transfer.settled"},
            {"type": "transfer.settled"},
            {"transfer_id": transfer["id"], "type": "nonsense"},
            {"transfer_id": "tf_missing", "type": "transfer.held"},
            "not-an-object",
        ],
    )
    body = response.get_json()
    assert body["accepted"] == 1
    assert body["rejected"] == 4
    assert [e["index"] for e in body["errors"]] == [1, 2, 3, 4]


def test_ingest_rejects_another_accounts_transfer(client, counterparty_id):
    transfer = submit(client, counterparty_id).get_json()
    response = ingest(
        client,
        [{"transfer_id": transfer["id"], "type": "transfer.settled"}],
        headers=INITECH_AUTH,
    )
    body = response.get_json()
    assert body["accepted"] == 0
    assert body["errors"][0]["code"] == "not_found"


def test_empty_batch_is_400(client):
    response = ingest(client, [])
    assert response.status_code == 400


def test_oversized_batch_is_400(client, counterparty_id):
    transfer = submit(client, counterparty_id).get_json()
    response = ingest(
        client,
        [{"transfer_id": transfer["id"], "type": "transfer.settled"}] * 101,
    )
    assert response.status_code == 400


def test_missing_postings_array_is_400(client):
    response = client.post("/v1/postings", json={}, headers=NORTHWIND_AUTH)
    assert response.status_code == 400


def test_ingest_does_not_move_the_transfer(client, counterparty_id):
    transfer = submit(client, counterparty_id).get_json()
    ingest(client, [{"transfer_id": transfer["id"], "type": "transfer.settled"}])
    after = client.get(
        "/v1/transfers/" + transfer["id"], headers=NORTHWIND_AUTH
    ).get_json()
    assert after["status"] == "pending"


def test_postings_are_filtered_by_transfer(client, counterparty_id):
    first = submit(client, counterparty_id).get_json()
    second = submit(client, counterparty_id).get_json()
    ingest(
        client,
        [
            {"transfer_id": first["id"], "type": "transfer.held", "memo": "funding"},
            {"transfer_id": second["id"], "type": "transfer.settled"},
        ],
    )
    data = client.get(
        "/v1/postings?transfer_id=" + first["id"], headers=NORTHWIND_AUTH
    ).get_json()["data"]
    assert len(data) == 1
    assert data[0]["memo"] == "funding"


def test_postings_are_scoped_to_the_caller(client, counterparty_id):
    transfer = submit(client, counterparty_id).get_json()
    ingest(client, [{"transfer_id": transfer["id"], "type": "transfer.settled"}])
    data = client.get("/v1/postings", headers=INITECH_AUTH).get_json()["data"]
    assert data == []
