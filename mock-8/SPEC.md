# Design spec — Payouts API

The rules this service is built to. Any change is measured against them.

## Shape

- Resources are plural nouns (`/v1/transfers`). A state change with side
  effects gets an action sub-resource (`POST /v1/transfers/{id}/transition`),
  never a field write on `PATCH`.
- Every response, failures included, uses the shared error envelope.
- Storage shape and wire shape are separate: handlers return
  `serialize_transfer(...)`, never a raw record.
- Ingest is not CRUD. `POST /v1/postings` takes a batch, returns `202`, and
  reports a per-item result carrying that item's `index`. It must not abort the
  batch on the first bad item, and must not silently drop one.
- A submit returns `202`, not `201`. The transfer exists; no money has left the
  account, and the status code must not imply it has.

## Constraints

These are binding. **A change that violates one is wrong even if its tests
pass.**

1. Handlers **MUST NOT** load a collection to answer a request about a single
   record. Use `store.get()` or `store.find_one()`.
2. Filtering **MUST** happen before pagination, never after. A filter applied
   to an already-sliced page returns short pages and a cursor that walks the
   wrong rows.
3. Tenant scoping **MUST** derive from `g.account`, which is set by
   `authenticate()`. Never from a request header, query parameter or body
   field.
4. Every read of a tenant-owned record **MUST** pass `account_id=`. A record
   owned by another account is a `404`, never a `403` — `403` confirms the ID
   exists.
5. Sort keys **MUST** be immutable for the life of a paging walk. Pagination
   orders by `(created_at, id)`; neither may be rewritten after creation.
6. Status changes **MUST** go through `validate_transition()` and the
   `ALLOWED_TRANSITIONS` table. No handler may write `status` directly.
7. Money **MUST** be an integer count of minor units plus an explicit
   `currency`. No floats, and `amount_minor` and `currency` **MUST NOT** be
   rewritten after the transfer is created.
8. Full bank details **MUST NOT** appear in responses, logs, or error
   messages. A counterparty exposes `account_number_last4` and nothing more.
