"""Live check of the insurer console: a suspicious claim goes in, insurers work it, the claimant sees only the safe view."""
import json
import time
import urllib.error
import urllib.request
from datetime import date

API_URL = next(l.split("=", 1)[1].strip() for l in open(".env") if l.startswith("API_URL="))
BASE = API_URL.rsplit("/claims", 1)[0].rstrip("/")
failures = []
NAMES = {}


def call(method, path, body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        text = e.read().decode()
        try:
            return e.code, json.loads(text)
        except ValueError:
            return e.code, {"raw": text}


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name} (got {got}, want {want})")
    if not ok:
        failures.append(name)


def login(account):
    s, d = call("POST", "/api/login", {"account_id": account})
    assert s == 200, f"login failed for {account}: {s} {d}"
    NAMES[account] = d["profile"]["full_name"]
    return d["token"]


print("Sign-in is now required for reviewer endpoints")
check("queue without a token", call("GET", "/api/queue")[0], 401)
claimant = login("PT00000")
check("claimant cannot read the queue", call("GET", "/api/queue", token=claimant)[0], 403)
junior, senior = login("INS-001"), login("INS-004")          # limits 50,000 and 5,000,000
check("insurer can read the queue", call("GET", "/api/queue", token=junior)[0], 200)
s, d = call("GET", "/api/config", token=junior)
check("criteria list loads", (s, len(d.get("criteria", [])) >= 10), (200, True))

print("\nA suspicious claim from PT00000")
claim = {"provider_id": "P0020", "claim_date": date.today().isoformat(), "diagnosis": "fever",
         "procedure_code": "SURG06", "amount": 450000}
s, d = call("POST", "/api/my/claims", claim, token=claimant)
check("claim accepted", s, 202)
cid = d.get("claim_id")
print("  waiting for the pipeline", end="", flush=True)
item = None
for _ in range(40):
    time.sleep(1.5)
    print(".", end="", flush=True)
    s, q = call("GET", "/api/queue", token=senior)
    item = next((i for i in q["items"] if i["claim_id"] == cid), None)
    if item:
        break
print()
check("claim lands in a review list", item is not None, True)
if item is None:
    print("\nThe model did not flag this claim, so the rest cannot run. Send me this output.")
    raise SystemExit(1)
print(f"  list: {item['review_queue']}, risk score: {item['risk_score']}")

s, ctx = call("GET", f"/api/claims/{cid}/context", token=junior)
check("case context loads", s, 200)
check("context has claimant and hospital", (ctx["claimant"] is not None, ctx["hospital"] is not None), (True, True))
check("context has the model inputs", isinstance(ctx["claim"].get("features"), dict), True)
check("claimant cannot open the case context", call("GET", f"/api/claims/{cid}/context", token=claimant)[0], 403)

print("\nDecisions")
dec = f"/api/claims/{cid}/decision"
check("claimant cannot decide", call("POST", dec, {"decision": "APPROVE"}, token=claimant)[0], 403)
s, d = call("POST", dec, {"decision": "APPROVE"}, token=junior)
check("INS-001 cannot approve above their limit", s, 403)
check("claim still waiting after refused approval", call("GET", f"/api/claims/{cid}/context", token=senior)[1]["claim"].get("review_queue") is not None, True)
check("reject needs a note", call("POST", dec, {"decision": "REJECT"}, token=senior)[0], 400)
check("manual verification needs a note", call("POST", dec, {"decision": "MANUAL_VERIFICATION", "notes": ""}, token=senior)[0], 400)
check("unknown decision refused", call("POST", dec, {"decision": "PAY_TWICE", "notes": "xxxxx"}, token=senior)[0], 400)

s, d = call("POST", dec, {"decision": "MANUAL_VERIFICATION", "notes": "Call the hospital to confirm the surgery."}, token=senior)
check("manual verification accepted", (s, d.get("status")), (200, "MANUAL_VERIFICATION"))
s, q = call("GET", "/api/queue", token=senior)
check("claim moved to the Verification list", next((i["review_queue"] for i in q["items"] if i["claim_id"] == cid), None), "VERIFICATION")
s, mine = call("GET", "/api/my/claims", token=claimant)
seen = next(c for c in mine["claims"] if c["claim_id"] == cid)
check("claimant sees 'Verification in progress'", seen["status_label"], "Verification in progress")
check("claimant sees no notes, score or reviewer", [k for k in ("review_notes", "risk_score", "reviewer", "features") if k in seen], [])

s, d = call("POST", dec, {"decision": "REJECT", "notes": "Hospital could not confirm the surgery."}, token=senior)
check("reject accepted", (s, d.get("status")), (200, "REVIEWER_REJECTED"))
check("second decision on the same claim refused", call("POST", dec, {"decision": "APPROVE"}, token=senior)[0], 409)
s, ctx = call("GET", f"/api/claims/{cid}/context", token=senior)
check("decision is recorded under the insurer's name", (ctx["claim"].get("reviewer"), ctx["claim"].get("reviewer_label")), (NAMES["INS-004"], 1))
s, mine = call("GET", "/api/my/claims", token=claimant)
check("claimant now sees Rejected", next(c for c in mine["claims"] if c["claim_id"] == cid)["status_label"], "Rejected")

print("\n" + ("ALL CHECKS PASSED" if not failures else f"FAILED: {failures}"))
raise SystemExit(1 if failures else 0)
