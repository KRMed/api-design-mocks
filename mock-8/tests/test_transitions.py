"""The transfer state machine and optimistic concurrency."""

from __future__ import annotations

from tests.conftest import NORTHWIND_AUTH, settle, submit


def transition(client, transfer_id, body, headers=None):
    merged = dict(NORTHWIND_AUTH)
    merged.update(headers or {})
    return client.post(
        "/v1/transfers/{}/transition".format(transfer_id), json=body, headers=merged
    )


def test_submitting_counts_an_attempt(client, counterparty_id):
    transfer = submit(client, counterparty_id).get_json()
    response = transition(client, transfer["id"], {"status": "submitted"})
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "submitted"
    assert body["attempts"] == 1


def test_illegal_transition_is_409(client, counterparty_id):
    transfer = submit(client, counterparty_id).get_json()
    response = transition(client, transfer["id"], {"status": "settled"})
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "invalid_transition"


def test_unknown_status_is_400(client, counterparty_id):
    transfer = submit(client, counterparty_id).get_json()
    response = transition(client, transfer["id"], {"status": "teleported"})
    assert response.status_code == 400


def test_missing_status_is_400(client, counterparty_id):
    transfer = submit(client, counterparty_id).get_json()
    response = transition(client, transfer["id"], {})
    assert response.status_code == 400


def test_settling_is_terminal(client, counterparty_id):
    transfer = submit(client, counterparty_id).get_json()
    settle(client, transfer["id"])
    response = transition(client, transfer["id"], {"status": "submitted"})
    assert response.status_code == 409


def test_settling_records_a_posting(client, counterparty_id):
    transfer = submit(client, counterparty_id).get_json()
    settle(client, transfer["id"])
    postings = client.get(
        "/v1/postings?transfer_id=" + transfer["id"], headers=NORTHWIND_AUTH
    ).get_json()["data"]
    assert [p["type"] for p in postings] == ["transfer.settled"]


def test_return_requires_a_return_code(client, counterparty_id):
    transfer = submit(client, counterparty_id).get_json()
    transition(client, transfer["id"], {"status": "submitted"})
    response = transition(client, transfer["id"], {"status": "returned"})
    assert response.status_code == 400
    assert response.get_json()["error"]["errors"][0]["field"] == "return_code"


def test_return_stores_the_code_and_records_a_posting(client, counterparty_id):
    transfer = submit(client, counterparty_id).get_json()
    transition(client, transfer["id"], {"status": "submitted"})
    response = transition(
        client, transfer["id"], {"status": "returned", "return_code": "R01"}
    )
    assert response.get_json()["return_code"] == "R01"
    postings = client.get(
        "/v1/postings?transfer_id=" + transfer["id"], headers=NORTHWIND_AUTH
    ).get_json()["data"]
    assert postings[0]["memo"] == "R01"


def test_stale_if_match_is_409(client, counterparty_id):
    transfer = submit(client, counterparty_id).get_json()
    transition(client, transfer["id"], {"status": "submitted"})
    response = transition(
        client, transfer["id"], {"status": "settled"}, headers={"If-Match": 'W/"1"'}
    )
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "version_conflict"


def test_current_if_match_succeeds(client, counterparty_id):
    transfer = submit(client, counterparty_id).get_json()
    response = transition(
        client, transfer["id"], {"status": "submitted"}, headers={"If-Match": 'W/"1"'}
    )
    assert response.status_code == 200
    assert response.headers["ETag"] == 'W/"2"'


def test_malformed_if_match_is_400(client, counterparty_id):
    transfer = submit(client, counterparty_id).get_json()
    response = transition(
        client, transfer["id"], {"status": "submitted"}, headers={"If-Match": "banana"}
    )
    assert response.status_code == 400


def test_transition_on_another_accounts_transfer_is_404(client, counterparty_id):
    transfer = submit(client, counterparty_id).get_json()
    response = client.post(
        "/v1/transfers/{}/transition".format(transfer["id"]),
        json={"status": "submitted"},
        headers={"Authorization": "Bearer key_initech"},
    )
    assert response.status_code == 404
