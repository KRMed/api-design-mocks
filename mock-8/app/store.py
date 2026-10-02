"""In-memory store and cursor pagination."""

from __future__ import annotations

import base64
import json
import threading
import uuid
from datetime import datetime, timezone

from app.errors import ConflictError, NotFoundError, ValidationError

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def utcnow():
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def new_id(prefix):
    return "{}_{}".format(prefix, uuid.uuid4().hex[:12])


def parse_etag(raw):
    """Accepts both `W/"3"` and `"3"`."""
    value = raw.strip()
    if value.startswith("W/"):
        value = value[2:]
    return int(value.strip('"'))


class Store:
    def __init__(self):
        self._lock = threading.RLock()
        self._data = {"transfers": {}, "counterparties": {}, "postings": {}}
        self._prefixes = {
            "transfers": "tf",
            "counterparties": "cp",
            "postings": "pst",
        }
        # Idempotency-Key -> (kind, resource_id)
        self._idempotency = {}

    def create(self, kind, record):
        with self._lock:
            record = dict(record)
            record.setdefault("id", new_id(self._prefixes[kind]))
            now = utcnow()
            record["created_at"] = now
            record["updated_at"] = now
            record["version"] = 1
            self._data[kind][record["id"]] = record
            return dict(record)

    def get(self, kind, resource_id, *, account_id=None):
        """A record owned by another account raises NotFound, not Forbidden."""
        with self._lock:
            record = self._data[kind].get(resource_id)
            if record is None or (
                account_id is not None and record.get("account_id") != account_id
            ):
                raise NotFoundError(
                    "{} '{}' was not found.".format(
                        kind[:-1].capitalize(), resource_id
                    )
                )
            return dict(record)

    def update(self, kind, resource_id, changes, *, expected_version=None,
               account_id=None):
        with self._lock:
            record = self._data[kind].get(resource_id)
            if record is None or (
                account_id is not None and record.get("account_id") != account_id
            ):
                raise NotFoundError(
                    "{} '{}' was not found.".format(
                        kind[:-1].capitalize(), resource_id
                    )
                )
            if expected_version is not None and record["version"] != expected_version:
                raise ConflictError(
                    "Record was modified by another request; re-read and retry.",
                    code="version_conflict",
                )
            record.update(changes)
            record["updated_at"] = utcnow()
            record["version"] += 1
            return dict(record)

    def list(self, kind, *, account_id=None):
        """Newest first, ordered by `(created_at, id)`."""
        with self._lock:
            records = [
                dict(r)
                for r in self._data[kind].values()
                if account_id is None or r.get("account_id") == account_id
            ]
        records.sort(key=lambda r: (r["created_at"], r["id"]), reverse=True)
        return records

    def find_one(self, kind, **predicates):
        with self._lock:
            for record in self._data[kind].values():
                if all(record.get(k) == v for k, v in predicates.items()):
                    return dict(record)
        return None

    def remember_idempotency(self, key, kind, resource_id):
        with self._lock:
            self._idempotency[key] = (kind, resource_id)

    def lookup_idempotency(self, key):
        with self._lock:
            return self._idempotency.get(key)

    def reset(self):
        with self._lock:
            for bucket in self._data.values():
                bucket.clear()
            self._idempotency.clear()


store = Store()


def encode_cursor(payload):
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor):
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(cursor + padding))
        if not isinstance(payload, dict):
            raise ValueError("cursor must decode to an object")
        return payload
    except Exception:
        raise ValidationError(
            "Invalid cursor. Pass the `next_cursor` from a previous page.",
            errors=[{"field": "cursor", "message": "malformed"}],
        )


def parse_limit(raw):
    if raw is None:
        return DEFAULT_LIMIT
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        raise ValidationError(
            "`limit` must be an integer.",
            errors=[{"field": "limit", "message": "not an integer"}],
        )
    if limit < 1:
        raise ValidationError(
            "`limit` must be at least 1.",
            errors=[{"field": "limit", "message": "must be >= 1"}],
        )
    return min(limit, MAX_LIMIT)


def page_response(records, serialize, *, limit, cursor):
    page, next_cursor = paginate(records, limit=limit, cursor=cursor)
    return {
        "data": [serialize(r) for r in page],
        "next_cursor": next_cursor,
        "has_more": next_cursor is not None,
    }


def paginate(records, *, limit, cursor):
    """`records` must already be sorted by `(created_at, id)` descending."""
    start = 0
    if cursor:
        payload = decode_cursor(cursor)
        after_created = payload.get("created_at")
        after_id = payload.get("id")
        if after_created is None or after_id is None:
            raise ValidationError(
                "Invalid cursor.",
                errors=[{"field": "cursor", "message": "missing sort key"}],
            )
        for index, record in enumerate(records):
            if (record["created_at"], record["id"]) == (after_created, after_id):
                start = index + 1
                break
        else:
            start = len(records)
            for index, record in enumerate(records):
                if (record["created_at"], record["id"]) < (after_created, after_id):
                    start = index
                    break

    window = records[start : start + limit + 1]
    page = window[:limit]
    next_cursor = None
    if len(window) > limit and page:
        last = page[-1]
        next_cursor = encode_cursor(
            {"created_at": last["created_at"], "id": last["id"]}
        )
    return page, next_cursor
