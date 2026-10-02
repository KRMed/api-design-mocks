"""Starting data. Two accounts, so a cross-tenant read is observable by hand."""

from __future__ import annotations

from app.store import store

NORTHWIND = "acct_northwind"
INITECH = "acct_initech"


def seed():
    store.reset()

    harbor = store.create(
        "counterparties",
        {
            "account_id": NORTHWIND,
            "name": "Harbor Logistics",
            "email": "ap@harborlogistics.test",
            "currency": "USD",
            "account_number": "000123456789",
            "routing_number": "021000021",
        },
    )
    store.create(
        "counterparties",
        {
            "account_id": NORTHWIND,
            "name": "Lumon Facilities",
            "email": "billing@lumon.test",
            "currency": "EUR",
            "account_number": "000987654321",
            "routing_number": "021000021",
        },
    )
    bluth = store.create(
        "counterparties",
        {
            "account_id": INITECH,
            "name": "Bluth Construction",
            "email": "invoices@bluth.test",
            "currency": "USD",
            "account_number": "000555000111",
            "routing_number": "026009593",
        },
    )

    settled = store.create(
        "transfers",
        {
            "account_id": NORTHWIND,
            "counterparty_id": harbor["id"],
            "amount_minor": 125000,
            "currency": "USD",
            "memo": "Invoice 4410",
            "status": "pending",
            "attempts": 0,
            "return_code": None,
        },
    )
    store.update("transfers", settled["id"], {"status": "submitted", "attempts": 1})
    store.update("transfers", settled["id"], {"status": "settled"})
    store.create(
        "postings",
        {
            "account_id": NORTHWIND,
            "transfer_id": settled["id"],
            "type": "transfer.settled",
            "memo": None,
        },
    )

    store.create(
        "transfers",
        {
            "account_id": NORTHWIND,
            "counterparty_id": harbor["id"],
            "amount_minor": 4999,
            "currency": "USD",
            "memo": "Invoice 4411",
            "status": "pending",
            "attempts": 0,
            "return_code": None,
        },
    )

    store.create(
        "transfers",
        {
            "account_id": INITECH,
            "counterparty_id": bluth["id"],
            "amount_minor": 77500,
            "currency": "USD",
            "memo": "Site prep",
            "status": "pending",
            "attempts": 0,
            "return_code": None,
        },
    )
