"""Counterparty reads and guarded partial updates."""

from __future__ import annotations

from tests.conftest import INITECH_AUTH, NORTHWIND_AUTH


def patch(client, counterparty_id, body, headers=None):
    merged = dict(NORTHWIND_AUTH)
    merged.update(headers or {})
    return client.patch(
        "/v1/counterparties/" + counterparty_id, json=body, headers=merged
    )


def test_payload_hides_the_account_number(client, counterparty):
    assert "account_number" not in counterparty
    assert "routing_number" not in counterparty
    assert counterparty["account_number_last4"] == "6789"


def test_patch_requires_if_match(client, counterparty_id):
    response = patch(client, counterparty_id, {"name": "Harbor Logistics LLC"})
    assert response.status_code == 428
    assert response.get_json()["error"]["code"] == "precondition_required"


def test_patch_updates_a_field_and_bumps_the_version(client, counterparty):
    response = patch(
        client,
        counterparty["id"],
        {"name": "Harbor Logistics LLC"},
        headers={"If-Match": 'W/"1"'},
    )
    assert response.status_code == 200
    assert response.get_json()["name"] == "Harbor Logistics LLC"
    assert response.headers["ETag"] == 'W/"2"'


def test_patch_with_a_stale_if_match_is_409(client, counterparty):
    patch(
        client,
        counterparty["id"],
        {"name": "Harbor One"},
        headers={"If-Match": 'W/"1"'},
    )
    response = patch(
        client,
        counterparty["id"],
        {"name": "Harbor Two"},
        headers={"If-Match": 'W/"1"'},
    )
    assert response.status_code == 409


def test_patch_rejects_bank_detail_changes(client, counterparty_id):
    response = patch(
        client,
        counterparty_id,
        {"account_number": "000000000001"},
        headers={"If-Match": 'W/"1"'},
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["errors"][0]["field"] == "account_number"


def test_patch_rejects_an_empty_body(client, counterparty_id):
    response = patch(client, counterparty_id, {}, headers={"If-Match": 'W/"1"'})
    assert response.status_code == 400


def test_patch_validates_the_email(client, counterparty_id):
    response = patch(
        client, counterparty_id, {"email": "nope"}, headers={"If-Match": 'W/"1"'}
    )
    assert response.status_code == 400


def test_patch_on_another_accounts_counterparty_is_404(client, counterparty_id):
    response = client.patch(
        "/v1/counterparties/" + counterparty_id,
        json={"name": "Nope"},
        headers=dict(INITECH_AUTH, **{"If-Match": 'W/"1"'}),
    )
    assert response.status_code == 404


def test_counterparties_are_scoped_to_the_caller(client):
    northwind = client.get(
        "/v1/counterparties", headers=NORTHWIND_AUTH
    ).get_json()["data"]
    initech = client.get("/v1/counterparties", headers=INITECH_AUTH).get_json()["data"]
    assert len(northwind) == 2
    assert len(initech) == 1
