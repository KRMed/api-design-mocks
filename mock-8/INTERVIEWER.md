# Interviewer guide — mock-8, Payouts API

A business sends money to its vendors. A transfer is accepted and queued, a
settlement worker reports what the bank did, the transfer ends settled or
returned.

**Candidate gets:** the `mock-8/` tree, `SPEC.md`, and `PR.md` at minute 46.
**Candidate does not get:** a README, search, or grep. They read files.
**No code is written.** Everything is verbal.

| Phase | Time | What |
|---|---|---|
| Background | 0–20 | Their project, what they want from the internship |
| 1. Explore | 20–33 | Orient, then trace the data flow |
| 2. Design | 33–46 | Add a feature, out loud |
| 3. Review | 46–59 | `PR.md` — what's off and why it matters |

Setup: `cd mock-8 && python -m venv .venv`, activate it
(`source .venv/bin/activate`, or `.venv\Scripts\activate` on Windows),
`pip install -r requirements.txt`, `python wsgi.py` → port **5001**.
`pytest -q` → **66 passed**.

The whole API is `app/api.py`. Four other modules: `__init__.py` (factory and
auth), `store.py` (storage and pagination), `validation.py` (fields and the
state machine), `errors.py` (the envelope).

---

## 2. Dataflow — the answer key for task 1

```
POST /v1/transfers
  │
  ├─ before_request: authenticate()      Bearer key -> g.account      (401)
  │
  ├─ validate_transfer()                 collects ALL field errors    (400)
  │     amount_minor must be an int, > 0, <= 100_000_000
  ├─ store.get("counterparties", ..., account_id=)  unknown/other tenant -> 404
  ├─ counterparty currency != requested currency -> 409 currency_mismatch
  ├─ Idempotency-Key? -> replay original, 202 + Idempotent-Replay: true
  ├─ store.create("transfers")            status=pending, version=1
  └─ 202 + ETag + Location          <<< ASYNC BOUNDARY: no money has moved
                │
                │   (a settlement worker, outside this codebase, sends the file
                │    to the bank and reports the outcome)
                ▼
POST /v1/transfers/{id}/transition       the worker reports the outcome
  ├─ validate_transition() vs ALLOWED_TRANSITIONS   illegal -> 409
  ├─ If-Match present? -> version check             stale   -> 409
  ├─ side effects by target:
  │     submitted -> attempts += 1
  │     settled   -> return_code = None
  │     returned  -> requires `return_code`, stores it
  │     held      -> ** NOTHING **   (see the planted gap below)
  └─ terminal? -> store.create("postings", ...)

POST /v1/postings        bank partner settlement file, batched
  └─ per item: validate -> find_one(scoped) -> create posting
     202 {accepted, rejected, results[], errors[{index, code, message}]}
```

**State machine**

```
pending ──> submitted ──> settled    (terminal)
                │  ▲──┐
                │     │
                ├──> held ──┘        (soft hold: funding or compliance)
                └──> returned        (terminal, needs a return_code)
                       ▲
                  held ┘
```

**Three deliberate gaps.** Strong candidates ask about these; weak ones narrate
them as if they exist.

1. **Nothing moves money.** No bank client, no worker, no file generation. The
   service only records what someone else claims happened.
2. **Ingesting a posting does not move the transfer.** `POST /v1/postings`
   writes a posting row; the transfer's status is untouched unless a worker
   separately calls `/transition`. The two paths never meet.
3. **Nothing checks that the money exists.** There is no balance, no funding
   check, no reserved amount. A business can submit transfers forever.

---

## 3. The trace question — NON-CUTTABLE

Ask this one verbatim. It is what the round exists to ask.

> A customer's accounting system calls `POST /v1/transfers` to pay a vendor
> $1,250. Walk me through every piece of code that request touches, from the
> moment it arrives to the moment the customer sees a response. Then tell me:
> at what point has the money actually left their account?

| Band | What it sounds like |
|---|---|
| **Weak** | Goes straight to the handler. Misses `authenticate` entirely. Says the money moves when the endpoint returns 200. Cannot say where `g.account` comes from. |
| **Adequate** | Finds the `before_request` hook, follows `submit_transfer`, names validation, the counterparty lookup and the store write, notices the `202`. |
| **Strong** | All of the above, *and*: explains why `202` rather than `201`, identifies that nothing in this codebase moves money, and points at `/transition` as where the outcome comes back. Asks who calls it. |

**Follow-ups, escalating:**

1. *Why is the amount an integer called `amount_minor` instead of a decimal
   amount?* → floats lose cents; `1250` is ambiguous without a currency, which
   is why both fields are required and SPEC 7 freezes them after creation.
2. *A customer asks for a transfer that belongs to a different business. What do
   they get, and why not `403`?* → `404`. `403` confirms the ID exists, which
   turns the endpoint into an enumeration oracle for other companies' payouts.
3. *The bank sends us a settlement file saying this transfer settled. Does the
   transfer change?* → No. Gap 2. Ask what they would do about it, and whether
   the fix belongs in the ingest handler.
4. *Where could a transfer get stuck forever?* → the `held` gap. Best question
   in the set; almost nobody gets it unhinted. Nudge: *"read the status list in
   `validation.py` against the branches in `transition_transfer`."*

---

## 4. Design — task 2

Pick **one** primary. Each has a hidden coupling; finding it *is* the answer.

### Primary option A — a daily payout cap per counterparty

> Finance wants a limit: no more than $50,000 a day to any one vendor. Design
> it.

**This is a rule for all future transfers, not a cleanup of what is pending.**
The candidate may read it as "fix the backlog." That misreading is expected and
is a real signal — let them run with it for a moment, then say, verbatim:

> "To be clear — this should apply to every transfer we accept from now on, not
> just the ones already pending."

**The hidden coupling:** a daily cap needs the sum of today's transfers per
counterparty. There is no index on counterparty, and `store.list()` is the only
read — so the naive design scans the collection on every submit, which is
exactly what SPEC rule 1 prohibits and what the PR gets wrong. Strong
candidates propose a counter keyed by `(counterparty_id, date)`, maintained on
write.

Also good: *where* does the limit live — reject with `409` at submit time, or
accept and park the transfer inside the async boundary? The `202` contract
makes the second one legal, and it is the more interesting answer.

### Primary option B — let a customer edit a transfer before it goes out

> Customers want to fix a transfer after submitting it. Design that.

Scope-correcting line, verbatim, if they start designing a repair job for
transfers that are already out the door:

> "To be clear — this is for a transfer that hasn't gone to the bank yet. You
> don't have to fix anything that already settled."

**What the question is really testing:** *which* fields, in *which* states.

| Field | Right answer |
|---|---|
| `memo` | Editable. A field write with no side effects — `PATCH` is correct. |
| `amount_minor`, `currency` | **Not** editable. SPEC 7 freezes both. Changing the amount is a cancel plus a new transfer, not a field write, because it has to re-run every check the original passed. |
| `counterparty_id` | Not editable. Paying a different vendor is a different payment. |
| `status` | Never. SPEC 6 — that is what `/transition` is for. |
| `created_at` | Never, and this is the trap. |

**The hidden coupling:** pagination orders by `(created_at, id)` (SPEC 5). A
candidate who says "bump the timestamp so the edited transfer shows at the top
of the list" has just broken every open paging walk. Ask what the edit does to
`updated_at` versus `created_at` if they do not raise it.

Follow-ups worth asking: should the edit require `If-Match`? (Yes — a human in
a dashboard, same argument as `update_counterparty`, which is the one place in
this codebase where `If-Match` is mandatory.) Should an edit be visible in the
postings trail? (On money data, yes — ask them who needs to see it.) Is
`pending` the only legal state? (Yes; once `submitted` the file is with the
bank, so a `409` is the honest answer.)

### Primary option C — change a counterparty's bank details

> A vendor switched banks. Design the flow that updates their account number.

`validate_counterparty_patch` already refuses `account_number` with
`immutable`, so the question is what the real flow looks like.

**The hidden coupling is security, not shape.** Editing a payee's bank details
is the highest-value write in the whole API: whoever can do it silently
redirects every future payout. Strong candidates get there on their own and
propose a verification sub-resource
(`POST /v1/counterparties/{id}/verification`) with a challenge, a verified state
the counterparty has to reach before it can receive money, and an audit record.
Then push: **what happens to transfers already pending against the old
details?** They must not silently retarget.

A candidate who treats this as "just add the field to the PATCH allowlist" has
missed the question.

### Secondary questions

| Question | The answer that matters |
|---|---|
| Add `?sort=updated_at` to `GET /v1/transfers`. | Breaks cursor pagination: the cursor encodes `(created_at, id)`. SPEC rule 5 — `updated_at` is mutable, so a paging walk can repeat or skip rows. Finding that coupling *is* the answer. |
| Add a way to cancel a pending transfer. | Sub-resource, not `DELETE`: money records are never deleted, support needs the history. Then the race — the worker may submit it in the same second, so cancelling a non-`pending` transfer is a `409`, and the window is real. |
| Notify customers when a transfer is returned. | Gap 2. At-least-once delivery, retries, and why the customer's endpoint must be idempotent. Careful: this is where candidates start larping about queues — steer back to the API contract. |
| The bank posts a settled amount three cents lower than we sent. | There is nowhere to put it: a posting has no amount. Does the transfer settle, partially settle, or go to a reconciliation state? Any defensible answer is fine; noticing that the ingest shape cannot represent the fact is the point. |

---

## 5. Optional sections — bring these up only if there is room

Neither is required to pass. Both are worth credit when the candidate raises
them unprompted, and **neither costs anything when they do not.**

### 5a. Middleware and cross-cutting concerns

This service has exactly one request hook: `authenticate` in
`app/__init__.py`. There is no rate limiter, no request-ID propagation, no
access log, no timing header.

> "Notice there is only one `before_request` hook. What else would normally
> live there, and where would you put it?"

| What they should reach for | Why it matters |
|---|---|
| Request IDs | Nothing correlates a support ticket to a log line today. |
| Rate limiting | And it has to run **after** auth, so the counter keys on the account rather than the IP — one customer behind a NAT would otherwise exhaust another's budget, and an attacker just rotates IPs. |
| Hooks on the app, not per-route decorators | A new route is covered by default. Forgetting a decorator on one route is how tenant-scoping bugs ship. |
| Access logs | And immediately: never log the `Authorization` header. SPEC 8. |

Credit the candidate who says "auth has to come first, because the limiter
needs an identity." That one sentence is the whole point of the section.

### 5b. Idempotency — brownie points

`submit_transfer` honours `Idempotency-Key` and the store remembers key →
transfer. **Treat this as a bonus topic.** Do not grade a candidate down for
not raising it, and do not hint toward it.

If they do raise it, the gaps are real and all four are in `store.py`:

| Gap | Consequence |
|---|---|
| No body fingerprint | Same key, different amount → returns the first transfer, second amount silently dropped. The sharpest one in a payouts API. |
| No TTL | Keys live forever. |
| No in-flight marker | Two concurrent retries can both miss the lookup and both create a transfer. The customer pays the vendor twice. |
| Global, not per-account | One business's key can collide with another's. |

The question that separates the good answer: *"what should happen when the same
key arrives with a different body?"* → `409`, not a replay. Returning the first
transfer is lying about what the second request asked for.

---

## 6. PR answer key

Hand over `PR.md` at ~minute 46. **Six defects, three false positives.**

**The standard nudge**, if they stall: *"have another look at the spec's
constraints."*

Every defect below was reproduced by `repro_pr.py` (apply the PR first).
Verified output is in §7.

| # | Tier | Where | Defect | SPEC |
|---|---|---|---|---|
| **D1** | **Security** | `api.py` `list_transfers` | `X-Ledger-Account` header overrides tenant scope | **3** |
| **D2** | Security | `api.py` `ingest_postings` | `find_one` lost `account_id=` — cross-tenant write | **4** |
| **D3** | **Deep** | `api.py` `submit_transfer` | Return guard returns `200` with no transfer; payout silently dropped | shape, **1** |
| **D4** | Mid | `api.py` `get_transfer` | Loads whole collection to compute `counterparty_history` | **1** |
| **D5** | Mid | `api.py` `list_postings` | Filters *after* paginating | **2** |
| **D6** | Surface | `api.py` `serialize_counterparty` | Full bank account number in the payload | **8** |

### D1 — lead with this one

Four lines. The comment claims "our internal proxy strips this header," which
is exactly the kind of assumption that is false in practice and unverifiable
from the code. Any customer sets `X-Ledger-Account: acct_initech` and reads
another company's payout history.

> **A candidate who lists six defects flat has reviewed a diff. One who leads
> with D1, says it outranks the others because it is a live data breach rather
> than a latency problem, and notes the comment is an unverifiable claim — has
> done code review.** That is the discriminator for this case.

Ask: *"the comment says the proxy strips it — does that make it safe?"*
(No. Defense in depth; the header is trusted input either way, and SPEC 3 says
scope comes from `g`, full stop.)

### D2

Note the comment directly above still says "scoped to the account" while the
code no longer is. Comment-code contradictions are free evidence. Compounds
with D3: a forged `transfer.returned` posting is one tenant writing into
another's settlement history.

### D3 — the deep one

`200` with `{"status": "blocked", "reason": "prior_return"}`. Every other path
returns `202` with an id, an ETag and a Location. A client that stores
`response["id"]` gets a `KeyError`; one that checks only the status code
believes the vendor was paid. Nothing is recorded, so the transfer is invisible
in `GET /v1/transfers` — finance cannot tell a blocked payout from a request
that never arrived, and the vendor simply does not get paid.

It is also a full-collection scan on the hottest write path in the service
(SPEC 1), which is a second, separate finding in the same six lines.

Strong answer: return `202` with a real record in a `blocked` state, or `409`
with the error envelope. Either way it must be visible and must not masquerade
as a shape the client cannot parse. Blocking repeat payouts may well be the
right *feature*; this is the wrong *contract* for it.

### D4 — the mandatory full scan

The docstring one line above literally says *"Never loads the collection."* The
diff adds `store.list()` directly beneath it. Same defect in the submit path
(D3), which makes every submit O(collection).

### D5

The PR description calls this an optimization — "one pass over the page instead
of scanning every record." It is the opposite: correctness is gone. The filter
now runs on 20 already-sliced rows, so a matching posting on page 2 is reported
as not existing. Watch for candidates who read the description and accept the
framing without checking.

Note the asymmetry worth pointing out: `list_transfers` has a test for exactly
this (`test_filtering_applies_across_pages`); `list_postings` does not. That is
why the suite stays green.

### D6

Everyone should find this. A candidate who misses it did not read the diff.

The follow-up is better than the defect: *"there is a test asserting
`'account_number' not in body`, and it still passes. Why?"* → the leak ships
under a different key. A test that asserts on key names rather than values does
not actually guard the rule in SPEC 8.

### False positives — do not let them count these

| Looks wrong | Actually fine |
|---|---|
| `item.get("memo", None)` — redundant default | Identical to `.get("memo")`. Harmless, arguably clearer. A candidate who calls this a *bug* has not distinguished style from defect. |
| The two filters in `list_transfers` swapped order | Both filters are conjunctive and both still run before pagination, so the result set is identical. Reordering them changes nothing. |
| Test renamed `test_ingest_rejects_...` → `test_ingest_accepts_...` | The rename is *correct* for the new behavior. The defect is D2, the behavior change it documents — not the rename. **A changed test means a changed contract: the right move is to ask why, and follow it to D2.** Credit the candidate who uses it as a thread, not the one who flags the rename itself. |

---

## 7. Verified reproduction

`cd interviewer && python repro_pr.py` with the PR applied:

```
D1  SECURITY: X-Ledger-Account forges tenant scope (SPEC 3)
northwind sees own transfers:      2
northwind + forged header sees:    1  -> [(77500, 'Site prep')]

D2  SECURITY: ingest lost account scoping (SPEC 4)
initech posting on northwind's transfer: accepted=1

D3  DEEP: the return guard silently drops a payout, returns 200
re-send after a return -> HTTP 200  body={'reason': 'prior_return', 'status': 'blocked'}
pending transfers recorded for the retry: 0

D4  FULL SCAN: GET /transfers/{id} loads the whole collection (SPEC 1)
store.list() calls for ONE keyed GET: 1

D5  CORRECTNESS: postings filtered after pagination (SPEC 2)
postings for target transfer: 0   (correct answer: 1)
has_more=True

D6  SECURITY: full bank account number in the payload (SPEC 8)
counterparty payload: {'account_number_last4': '6789',
                       'destination_account': '000123456789',
                       'name': 'Harbor Logistics'}
```

**The planted logic gap** (in the clean codebase, not the PR) — `held` is a
legal transition target that hits no branch in `transition_transfer`, and no
test covers it:

```
after submitted: status=submitted  attempts=1
after held:      status=held       attempts=1   (HTTP 200)
retry submitted: status=submitted  attempts=2
held again:      status=held       attempts=2
postings for this transfer: 0
```

Status changes, but the held leg counts no attempt, writes no posting, and
never sets `return_code` — so a transfer can loop `submitted → held →
submitted` forever and the only counter that could stop it is the one that path
never touches. Reachable, untested, invisible unless you read `STATUSES`
against the branches in the handler.

---

## 8. Signals

**Strong**
- Opens `app/__init__.py` or `app/api.py` first to find the routes, not a
  random module.
- Asks "what calls `/transition`?" — finds the async boundary unprompted.
- Reads `SPEC.md` before reviewing, and cites rule numbers.
- Leads the review with D1 and ranks by blast radius, not by file order.
- Notices the comment in D2 contradicts the code beneath it.
- Treats the changed test as a thread to pull, not a nit to flag.
- Asks what a `202` means for money that has not moved.
- Says "I'd check X" instead of asserting something they haven't read.

**Weak**
- Narrates the gaps as if implemented ("then it sends the payment to the bank").
- Lists defects in diff order with no severity ranking.
- Flags the `.get("memo", None)` default as a bug.
- Accepts the PR description's "optimization" framing for D5.
- Redesigns the storage layer when asked about a payout cap (larping).
- Proposes editing `amount_minor` in place, or bumping `created_at` on edit.
- Cannot say where `g.account` is set after tracing a request.

---

## 9. Timing and cut order

| Minute | Item |
|---|---|
| 0–20 | Background |
| 20–24 | Orientation — let them read. Do not narrate the tree. |
| 24–33 | **Trace question** + follow-ups 1–2 |
| 33–46 | Design: one primary, then one secondary |
| 46–59 | `PR.md` |
| 59–60 | Their questions |

**Cut in this order when short:**

1. §5 entirely — middleware and idempotency are optional by design.
2. Secondary design questions (keep the primary).
3. Trace follow-ups 3–4.
4. D6 and the false positives — if they found D1 and D3, you have your signal.

**Never cut:** the trace question, and D1 in the review. Those two carry the
round.
