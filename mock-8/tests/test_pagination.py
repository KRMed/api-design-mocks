"""Cursor pagination over the transfer list."""

from __future__ import annotations

from tests.conftest import NORTHWIND_AUTH, submit


def bulk(client, counterparty_id, count):
    for index in range(count):
        submit(client, counterparty_id, memo="Invoice {}".format(index))


def test_default_page_size(client, counterparty_id):
    bulk(client, counterparty_id, 25)
    body = client.get("/v1/transfers", headers=NORTHWIND_AUTH).get_json()
    assert len(body["data"]) == 20
    assert body["has_more"] is True
    assert body["next_cursor"]


def test_explicit_limit(client, counterparty_id):
    bulk(client, counterparty_id, 5)
    body = client.get("/v1/transfers?limit=3", headers=NORTHWIND_AUTH).get_json()
    assert len(body["data"]) == 3


def test_limit_is_clamped(client, counterparty_id):
    bulk(client, counterparty_id, 3)
    body = client.get("/v1/transfers?limit=5000", headers=NORTHWIND_AUTH).get_json()
    assert body["has_more"] is False


def test_walking_every_page_yields_each_row_once(client, counterparty_id):
    bulk(client, counterparty_id, 12)
    seen, cursor = [], None
    while True:
        url = "/v1/transfers?limit=5"
        if cursor:
            url += "&cursor=" + cursor
        body = client.get(url, headers=NORTHWIND_AUTH).get_json()
        seen.extend(t["id"] for t in body["data"])
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen))
    assert len(seen) == 14  # 12 submitted here, 2 from the seed


def test_last_page_has_no_cursor(client, counterparty_id):
    bulk(client, counterparty_id, 2)
    body = client.get("/v1/transfers?limit=100", headers=NORTHWIND_AUTH).get_json()
    assert body["next_cursor"] is None
    assert body["has_more"] is False


def test_malformed_cursor_is_400(client):
    response = client.get("/v1/transfers?cursor=!!!", headers=NORTHWIND_AUTH)
    assert response.status_code == 400
    assert response.get_json()["error"]["errors"][0]["field"] == "cursor"


def test_cursor_without_a_sort_key_is_400(client):
    response = client.get("/v1/transfers?cursor=e30", headers=NORTHWIND_AUTH)
    assert response.status_code == 400


def test_non_integer_limit_is_400(client):
    response = client.get("/v1/transfers?limit=lots", headers=NORTHWIND_AUTH)
    assert response.status_code == 400


def test_zero_limit_is_400(client):
    response = client.get("/v1/transfers?limit=0", headers=NORTHWIND_AUTH)
    assert response.status_code == 400


def test_filtering_applies_across_pages(client, counterparty_id):
    bulk(client, counterparty_id, 24)
    body = client.get(
        "/v1/transfers?status=settled&limit=5", headers=NORTHWIND_AUTH
    ).get_json()
    assert len(body["data"]) == 1
    assert body["has_more"] is False
