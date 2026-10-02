from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from app.store import store  # noqa: E402
from seed import INITECH, NORTHWIND, seed  # noqa: E402

NORTHWIND_AUTH = {"Authorization": "Bearer key_northwind"}
INITECH_AUTH = {"Authorization": "Bearer key_initech"}


@pytest.fixture
def app():
    application = create_app()
    application.config.update(TESTING=True)
    store.reset()
    seed()
    yield application
    store.reset()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def counterparty(client):
    body = client.get("/v1/counterparties", headers=NORTHWIND_AUTH).get_json()
    return next(c for c in body["data"] if c["currency"] == "USD")


@pytest.fixture
def counterparty_id(counterparty):
    return counterparty["id"]


def submit(client, counterparty_id, **overrides):
    headers = dict(NORTHWIND_AUTH)
    headers.update(overrides.pop("headers", {}))
    payload = {
        "counterparty_id": counterparty_id,
        "amount_minor": 25000,
        "currency": "USD",
        "memo": "Invoice 9000",
    }
    payload.update(overrides)
    return client.post("/v1/transfers", json=payload, headers=headers)


def settle(client, transfer_id):
    client.post(
        "/v1/transfers/{}/transition".format(transfer_id),
        json={"status": "submitted"},
        headers=NORTHWIND_AUTH,
    )
    return client.post(
        "/v1/transfers/{}/transition".format(transfer_id),
        json={"status": "settled"},
        headers=NORTHWIND_AUTH,
    )
