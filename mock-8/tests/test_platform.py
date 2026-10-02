"""Auth, tenant isolation, and the error envelope."""

from __future__ import annotations

from tests.conftest import INITECH_AUTH, NORTHWIND_AUTH


def test_healthz_needs_no_auth(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_missing_auth_is_401(client):
    response = client.get("/v1/transfers")
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "unauthorized"


def test_malformed_auth_scheme_is_401(client):
    response = client.get("/v1/transfers", headers={"Authorization": "key_northwind"})
    assert response.status_code == 401


def test_unknown_key_is_401(client):
    response = client.get("/v1/transfers", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


def test_unknown_route_returns_json_envelope(client):
    response = client.get("/v1/nope", headers=NORTHWIND_AUTH)
    assert response.status_code == 404
    assert "error" in response.get_json()


def test_wrong_method_returns_json_envelope(client):
    response = client.delete("/v1/transfers", headers=NORTHWIND_AUTH)
    assert response.status_code == 405
    assert "error" in response.get_json()


def test_tenant_cannot_read_another_accounts_transfer(client):
    mine = client.get("/v1/transfers", headers=NORTHWIND_AUTH).get_json()["data"]
    response = client.get("/v1/transfers/" + mine[0]["id"], headers=INITECH_AUTH)
    assert response.status_code == 404


def test_list_is_scoped_to_the_caller(client):
    northwind = client.get("/v1/transfers", headers=NORTHWIND_AUTH).get_json()["data"]
    initech = client.get("/v1/transfers", headers=INITECH_AUTH).get_json()["data"]
    assert {t["id"] for t in northwind}.isdisjoint({t["id"] for t in initech})
