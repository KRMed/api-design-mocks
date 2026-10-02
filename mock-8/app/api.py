"""Every route in the service: transfers, counterparties, postings, health."""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from app.errors import ConflictError, ValidationError
from app.store import page_response, parse_etag, parse_limit, store
from app.validation import (
    TERMINAL_STATUSES,
    validate_counterparty_patch,
    validate_transfer,
    validate_transition,
)

bp = Blueprint("api", __name__)

MAX_BATCH = 100

POSTING_TYPES = ("transfer.settled", "transfer.returned", "transfer.held")


def serialize_transfer(transfer):
    return {
        "id": transfer["id"],
        "counterparty_id": transfer["counterparty_id"],
        "amount_minor": transfer["amount_minor"],
        "currency": transfer["currency"],
        "memo": transfer["memo"],
        "status": transfer["status"],
        "attempts": transfer["attempts"],
        "return_code": transfer["return_code"],
        "created_at": transfer["created_at"],
        "updated_at": transfer["updated_at"],
        "version": transfer["version"],
    }


def serialize_counterparty(counterparty):
    return {
        "id": counterparty["id"],
        "name": counterparty["name"],
        "email": counterparty["email"],
        "currency": counterparty["currency"],
        "account_number_last4": counterparty["account_number"][-4:],
        "created_at": counterparty["created_at"],
        "updated_at": counterparty["updated_at"],
        "version": counterparty["version"],
    }


def serialize_posting(posting):
    return {
        "id": posting["id"],
        "transfer_id": posting["transfer_id"],
        "type": posting["type"],
        "memo": posting["memo"],
        "created_at": posting["created_at"],
    }


def etag_for(record):
    return 'W/"{}"'.format(record["version"])


@bp.post("/v1/transfers")
def submit_transfer():
    payload = validate_transfer(request.get_json(silent=True))

    counterparty = store.get(
        "counterparties", payload["counterparty_id"], account_id=g.account["id"]
    )
    if counterparty["currency"] != payload["currency"]:
        raise ConflictError(
            "Counterparty '{}' is set up for {}, not {}.".format(
                counterparty["id"], counterparty["currency"], payload["currency"]
            ),
            code="currency_mismatch",
        )

    idempotency_key = request.headers.get("Idempotency-Key")
    if idempotency_key:
        existing = store.lookup_idempotency(idempotency_key)
        if existing:
            kind, resource_id = existing
            transfer = store.get(kind, resource_id, account_id=g.account["id"])
            response = jsonify(serialize_transfer(transfer))
            response.status_code = 202
            response.headers["ETag"] = etag_for(transfer)
            response.headers["Idempotent-Replay"] = "true"
            return response

    payload["account_id"] = g.account["id"]
    transfer = store.create("transfers", payload)

    if idempotency_key:
        store.remember_idempotency(idempotency_key, "transfers", transfer["id"])

    response = jsonify(serialize_transfer(transfer))
    response.status_code = 202
    response.headers["ETag"] = etag_for(transfer)
    response.headers["Location"] = "/v1/transfers/{}".format(transfer["id"])
    return response


@bp.get("/v1/transfers")
def list_transfers():
    """Filtering happens before pagination. See SPEC rule 2."""
    records = store.list("transfers", account_id=g.account["id"])

    status = request.args.get("status")
    if status:
        records = [r for r in records if r["status"] == status]

    counterparty_id = request.args.get("counterparty_id")
    if counterparty_id:
        records = [r for r in records if r["counterparty_id"] == counterparty_id]

    return jsonify(
        page_response(
            records,
            serialize_transfer,
            limit=parse_limit(request.args.get("limit")),
            cursor=request.args.get("cursor"),
        )
    )


@bp.get("/v1/transfers/<transfer_id>")
def get_transfer(transfer_id):
    """Keyed lookup, scoped to the account. Never loads the collection."""
    transfer = store.get("transfers", transfer_id, account_id=g.account["id"])
    response = jsonify(serialize_transfer(transfer))
    response.headers["ETag"] = etag_for(transfer)
    return response


@bp.post("/v1/transfers/<transfer_id>/transition")
def transition_transfer(transfer_id):
    """Move a transfer through the state machine.

    An action sub-resource rather than a `status` field write: a transition
    bumps the attempt counter, records return codes, and is refused outright
    when the machine says no. `If-Match` is optional but enforced when present.
    """
    transfer = store.get("transfers", transfer_id, account_id=g.account["id"])

    if_match = request.headers.get("If-Match")
    expected_version = None
    if if_match:
        try:
            expected_version = parse_etag(if_match)
        except ValueError:
            raise ValidationError(
                "Malformed If-Match header. Pass the ETag from a prior read.",
                errors=[{"field": "If-Match", "message": "not a version tag"}],
            )

    body = request.get_json(silent=True)
    target = validate_transition(body, transfer["status"])

    changes = {"status": target}

    if target == "submitted":
        changes["attempts"] = transfer["attempts"] + 1
    elif target == "settled":
        changes["return_code"] = None
    elif target == "returned":
        return_code = (body or {}).get("return_code")
        if not return_code:
            raise ValidationError(
                "A return must carry the bank's return code.",
                errors=[
                    {"field": "return_code", "message": "required when returning"}
                ],
            )
        changes["return_code"] = return_code

    updated = store.update(
        "transfers",
        transfer_id,
        changes,
        expected_version=expected_version,
        account_id=g.account["id"],
    )

    if target in TERMINAL_STATUSES:
        store.create(
            "postings",
            {
                "account_id": g.account["id"],
                "transfer_id": transfer_id,
                "type": "transfer.{}".format(target),
                "memo": updated["return_code"],
            },
        )

    response = jsonify(serialize_transfer(updated))
    response.headers["ETag"] = etag_for(updated)
    return response


@bp.get("/v1/counterparties")
def list_counterparties():
    records = store.list("counterparties", account_id=g.account["id"])
    return jsonify(
        page_response(
            records,
            serialize_counterparty,
            limit=parse_limit(request.args.get("limit")),
            cursor=request.args.get("cursor"),
        )
    )


@bp.patch("/v1/counterparties/<counterparty_id>")
def update_counterparty(counterparty_id):
    """Partial update, guarded by a required `If-Match`.

    Two people editing the same payee a second apart should get a 409 rather
    than one silently losing their edit. The submit path takes the opposite
    stance because workers retry automatically.
    """
    store.get("counterparties", counterparty_id, account_id=g.account["id"])

    if_match = request.headers.get("If-Match")
    if not if_match:
        raise ValidationError(
            "If-Match is required. Read the counterparty and send its ETag back.",
            code="precondition_required",
            status_code=428,
        )
    try:
        expected_version = parse_etag(if_match)
    except ValueError:
        raise ValidationError(
            "Malformed If-Match header. Pass the ETag from a prior read.",
            errors=[{"field": "If-Match", "message": "not a version tag"}],
        )

    changes = validate_counterparty_patch(request.get_json(silent=True))

    updated = store.update(
        "counterparties",
        counterparty_id,
        changes,
        expected_version=expected_version,
        account_id=g.account["id"],
    )
    response = jsonify(serialize_counterparty(updated))
    response.headers["ETag"] = etag_for(updated)
    return response


@bp.post("/v1/postings")
def ingest_postings():
    """Batched settlement postings from the bank partner."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("postings"), list):
        raise ValidationError(
            "Request body must be an object with a `postings` array.",
            errors=[{"field": "postings", "message": "expected an array"}],
        )

    batch = payload["postings"]
    if not batch:
        raise ValidationError(
            "`postings` must not be empty.",
            errors=[
                {"field": "postings", "message": "must contain at least one posting"}
            ],
        )
    if len(batch) > MAX_BATCH:
        raise ValidationError(
            "A batch may carry at most {} postings.".format(MAX_BATCH),
            errors=[{"field": "postings", "message": "batch too large"}],
        )

    accepted, rejected = [], []

    for index, item in enumerate(batch):
        if not isinstance(item, dict):
            rejected.append(
                {"index": index, "code": "invalid", "message": "expected an object"}
            )
            continue

        transfer_id = item.get("transfer_id")
        posting_type = item.get("type")

        if not transfer_id:
            rejected.append(
                {
                    "index": index,
                    "code": "validation_error",
                    "message": "`transfer_id` is required",
                }
            )
            continue
        if posting_type not in POSTING_TYPES:
            rejected.append(
                {
                    "index": index,
                    "code": "validation_error",
                    "message": "`type` must be one of: {}".format(
                        ", ".join(POSTING_TYPES)
                    ),
                }
            )
            continue

        # Scoped to the account: a posting naming another account's transfer is
        # rejected exactly like an unknown one.
        transfer = store.find_one(
            "transfers", id=transfer_id, account_id=g.account["id"]
        )
        if transfer is None:
            rejected.append(
                {
                    "index": index,
                    "code": "not_found",
                    "message": "unknown transfer '{}'".format(transfer_id),
                }
            )
            continue

        posting = store.create(
            "postings",
            {
                "account_id": g.account["id"],
                "transfer_id": transfer_id,
                "type": posting_type,
                "memo": item.get("memo"),
            },
        )
        accepted.append({"index": index, "id": posting["id"]})

    response = jsonify(
        {
            "accepted": len(accepted),
            "rejected": len(rejected),
            "results": accepted,
            "errors": rejected,
        }
    )
    response.status_code = 202
    return response


@bp.get("/v1/postings")
def list_postings():
    records = store.list("postings", account_id=g.account["id"])

    # Filter before paginating, never after. See SPEC rule 2.
    transfer_id = request.args.get("transfer_id")
    if transfer_id:
        records = [r for r in records if r["transfer_id"] == transfer_id]

    return jsonify(
        page_response(
            records,
            serialize_posting,
            limit=parse_limit(request.args.get("limit")),
            cursor=request.args.get("cursor"),
        )
    )


@bp.get("/healthz")
def healthz():
    """Liveness only. Checks nothing downstream."""
    return jsonify({"status": "ok"})
