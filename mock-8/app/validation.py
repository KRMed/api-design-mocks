"""Request-body validation and the transfer state machine."""

from __future__ import annotations

import re

from app.errors import ValidationError

STATUSES = ("pending", "submitted", "settled", "returned", "held")

TERMINAL_STATUSES = ("settled", "returned")

ALLOWED_TRANSITIONS = {
    "pending": ("submitted",),
    "submitted": ("settled", "returned", "held"),
    "held": ("submitted", "returned"),
    "settled": (),
    "returned": (),
}

SUPPORTED_CURRENCIES = ("USD", "EUR", "GBP")

# 1,000,000.00 in minor units.
MAX_AMOUNT_MINOR = 100_000_000

MAX_MEMO_LENGTH = 140

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Bank details change through a re-verification flow, not a PATCH.
IMMUTABLE_COUNTERPARTY_FIELDS = ("account_number", "routing_number", "currency")


def _require_object(payload):
    if not isinstance(payload, dict):
        raise ValidationError(
            "Request body must be a JSON object.",
            errors=[{"field": "body", "message": "expected an object"}],
        )
    return payload


def validate_transfer(payload):
    """Validate a submit request, collecting every problem before raising."""
    payload = _require_object(payload)
    errors = []

    counterparty_id = payload.get("counterparty_id")
    if not counterparty_id:
        errors.append({"field": "counterparty_id", "message": "required"})
    elif not isinstance(counterparty_id, str):
        errors.append({"field": "counterparty_id", "message": "must be a string"})

    amount = payload.get("amount_minor")
    if amount is None:
        errors.append({"field": "amount_minor", "message": "required"})
    elif isinstance(amount, bool) or not isinstance(amount, int):
        # `True` is an instance of int, so the bool check comes first.
        errors.append(
            {"field": "amount_minor", "message": "must be an integer of minor units"}
        )
    elif amount <= 0:
        errors.append({"field": "amount_minor", "message": "must be greater than 0"})
    elif amount > MAX_AMOUNT_MINOR:
        errors.append(
            {
                "field": "amount_minor",
                "message": "must be <= {}".format(MAX_AMOUNT_MINOR),
            }
        )

    currency = payload.get("currency")
    if not currency:
        errors.append({"field": "currency", "message": "required"})
    elif currency not in SUPPORTED_CURRENCIES:
        errors.append(
            {
                "field": "currency",
                "message": "must be one of: {}".format(
                    ", ".join(SUPPORTED_CURRENCIES)
                ),
            }
        )

    memo = payload.get("memo")
    if memo is not None:
        if not isinstance(memo, str):
            errors.append({"field": "memo", "message": "must be a string"})
        elif len(memo) > MAX_MEMO_LENGTH:
            errors.append(
                {
                    "field": "memo",
                    "message": "must be <= {} chars".format(MAX_MEMO_LENGTH),
                }
            )

    if errors:
        raise ValidationError("The request body failed validation.", errors=errors)

    return {
        "counterparty_id": counterparty_id,
        "amount_minor": amount,
        "currency": currency,
        "memo": memo,
        "status": "pending",
        "attempts": 0,
        "return_code": None,
    }


def validate_counterparty_patch(payload):
    """Validate a partial counterparty update.

    Absent means "leave alone"; present-but-empty is a mistake worth a 400.
    """
    payload = _require_object(payload)
    errors, changes = [], {}

    for field in IMMUTABLE_COUNTERPARTY_FIELDS:
        if field in payload:
            errors.append(
                {
                    "field": field,
                    "message": "immutable; re-verify the counterparty instead",
                }
            )

    for field in ("name", "email"):
        if field not in payload:
            continue
        value = payload[field]
        if not isinstance(value, str) or not value.strip():
            errors.append({"field": field, "message": "must be a non-empty string"})
        elif field == "email" and not EMAIL_RE.match(value):
            errors.append({"field": field, "message": "must be an email address"})
        else:
            changes[field] = value

    if errors:
        raise ValidationError("The request body failed validation.", errors=errors)
    if not changes:
        raise ValidationError(
            "No updatable fields supplied.",
            errors=[{"field": "body", "message": "expected name or email"}],
        )
    return changes


def validate_transition(payload, current_status):
    payload = _require_object(payload)
    target = payload.get("status")

    if not target:
        raise ValidationError(
            "The request body failed validation.",
            errors=[{"field": "status", "message": "required"}],
        )
    if target not in STATUSES:
        raise ValidationError(
            "The request body failed validation.",
            errors=[
                {
                    "field": "status",
                    "message": "must be one of: {}".format(", ".join(STATUSES)),
                }
            ],
        )
    if target not in ALLOWED_TRANSITIONS.get(current_status, ()):
        raise ValidationError(
            "Cannot move a transfer from '{}' to '{}'.".format(
                current_status, target
            ),
            code="invalid_transition",
            status_code=409,
        )
    return target
