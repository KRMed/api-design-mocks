"""Reproduce every planted defect in mock-8's PR. Run with the patch applied."""
import sys
sys.path.insert(0, "..")

from app import create_app
from app.store import store
from seed import seed

N = {"Authorization": "Bearer key_northwind"}
I = {"Authorization": "Bearer key_initech"}


def fresh():
    store.reset()
    seed()
    return create_app().test_client()


def payee(c, h=N, currency="USD"):
    data = c.get("/v1/counterparties", headers=h).get_json()["data"]
    return next(cp for cp in data if cp["currency"] == currency)["id"]


def submit(c, amount, h=N):
    return c.post("/v1/transfers", json={
        "counterparty_id": payee(c, h), "amount_minor": amount,
        "currency": "USD", "memo": "Invoice"}, headers=h)


def transition(c, tid, body, h=N):
    return c.post("/v1/transfers/%s/transition" % tid, json=body, headers=h)


print("=" * 66)
print("D1  SECURITY: X-Ledger-Account forges tenant scope (SPEC 3)")
print("=" * 66)
c = fresh()
mine = c.get("/v1/transfers", headers=N).get_json()["data"]
theirs = c.get("/v1/transfers", headers={**N, "X-Ledger-Account": "acct_initech"}
               ).get_json()["data"]
print("northwind sees own transfers:      %d" % len(mine))
print("northwind + forged header sees:    %d  -> %s" % (
    len(theirs), [(t["amount_minor"], t["memo"]) for t in theirs]))
print("Any customer reads any business's payout history by guessing an id.\n")

print("=" * 66)
print("D2  SECURITY: ingest lost account scoping (SPEC 4)")
print("=" * 66)
c = fresh()
victim = submit(c, 31000).get_json()["id"]
r = c.post("/v1/postings", json={"postings": [
    {"transfer_id": victim, "type": "transfer.returned", "memo": "forged"}]},
    headers=I).get_json()
print("initech posting on northwind's transfer: accepted=%d" % r["accepted"])
print("Cross-tenant write into another business's settlement history.\n")

print("=" * 66)
print("D3  DEEP: the return guard silently drops a payout, returns 200")
print("=" * 66)
c = fresh()
t = submit(c, 48000).get_json()["id"]
transition(c, t, {"status": "submitted"})
transition(c, t, {"status": "returned", "return_code": "R01"})
r = submit(c, 48000)
print("re-send after a return -> HTTP %s  body=%s" % (r.status_code, r.get_json()))
after = c.get("/v1/transfers?status=pending", headers=N).get_json()["data"]
print("pending transfers recorded for the retry: %d" % len(
    [x for x in after if x["amount_minor"] == 48000]))
print("No id, no Location, no 202. The caller believes the vendor was paid;")
print("nothing was recorded, so support cannot tell this from a lost request.\n")

print("=" * 66)
print("D4  FULL SCAN: GET /transfers/{id} loads the whole collection (SPEC 1)")
print("=" * 66)
c = fresh()
cp = payee(c)
for i in range(60):
    store.create("transfers", {"account_id": "acct_northwind", "counterparty_id": cp,
                               "amount_minor": 1000 + i, "currency": "USD",
                               "memo": "bulk", "status": "pending", "attempts": 0,
                               "return_code": None})
tid = c.get("/v1/transfers", headers=N).get_json()["data"][0]["id"]
calls = {"n": 0}
orig = store.list
def counted(*a, **k):
    calls["n"] += 1
    return orig(*a, **k)
store.list = counted
c.get("/v1/transfers/" + tid, headers=N)
store.list = orig
print("store.list() calls for ONE keyed GET: %d" % calls["n"])
print("Detail endpoint is now O(collection). The submit path scans too (D3).\n")

print("=" * 66)
print("D5  CORRECTNESS: postings filtered after pagination (SPEC 2)")
print("=" * 66)
c = fresh()
target = submit(c, 11100).get_json()["id"]
noise = submit(c, 22200).get_json()["id"]
def post(tid):
    store.create("postings", {"account_id": "acct_northwind", "transfer_id": tid,
                              "type": "transfer.settled", "memo": None})
post(target)          # oldest posting -> sorts LAST (newest-first order)
import time
time.sleep(0.01)      # distinct ms timestamps, so ordering is unambiguous
for _ in range(30):   # 30 newer postings push it past the 20-row page
    post(noise)
r = c.get("/v1/postings?transfer_id=" + target, headers=N).get_json()
print("postings for target transfer: %d   (correct answer: 1)" % len(r["data"]))
print("has_more=%s  -> the row exists but sits past the 20-row page, and the"
      % r["has_more"])
print("filter runs AFTER slicing, so the caller is told there are none.\n")

print("=" * 66)
print("D6  SECURITY: full bank account number in the payload (SPEC 8)")
print("=" * 66)
c = fresh()
body = c.get("/v1/counterparties", headers=N).get_json()["data"][0]
print("counterparty payload: %s" % {k: body[k] for k in sorted(body)
                                    if "account" in k or k == "name"})
print("The guard test asserts `'account_number' not in body` and still passes:")
print("the leak ships under a different key.")
