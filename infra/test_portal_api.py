"""Exercise sign-in, role checks and the account endpoints on the live API."""
import base64
import json
import urllib.error
import urllib.request

import boto3

API_URL = next(l.split("=", 1)[1].strip() for l in open(".env")
               if l.startswith("API_URL="))
BASE = API_URL.rsplit("/claims", 1)[0]
cfg = boto3.client("lambda", region_name="us-east-1").get_function_configuration(
    FunctionName="review-api")
PASSCODE = cfg["Environment"]["Variables"].get("ADMIN_PASSCODE", "")
failures = []


def call(method, path, body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name} (got {got}, want {want})")
    if not ok:
        failures.append(name)


def b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


print("Demo accounts")
s, d = call("GET", "/api/demo-accounts")
check("status", s, 200)
print("  counts:", {k: len(v) for k, v in d.items()})

print("\nClaimant PT00000")
s, d = call("POST", "/api/login", {"account_id": "PT00000"})
check("login", s, 200)
claimant = d["token"]
print(f"  signed in: {d['profile']['full_name']} | {d['role']} | {d['profile']['plan']} plan")
s, d = call("GET", "/api/me", token=claimant)
check("/api/me", s, 200)
print("  summary:", d["summary"])
s, d = call("GET", "/api/my/claims", token=claimant)
check("/api/my/claims", s, 200)
print(f"  {d['count']} claims; newest three:")
for c in d["claims"][:3]:
    print(f"    {c['claim_date']} {c['procedure_code']:7s} Rs {float(c['amount']):>9,.0f}"
          f"  {c['status_label']:14s} {c['hospital_name']}")
leaks = [k for c in d["claims"] for k in ("risk_score", "risk_band", "features") if k in c]
check("claims hide risk data", leaks, [])
check("claimant -> /api/config", call("GET", "/api/config", token=claimant)[0], 403)
check("claimant -> /api/accounts/P0020", call("GET", "/api/accounts/P0020", token=claimant)[0], 403)

print("\nSecurity checks")
check("no token -> /api/me", call("GET", "/api/me")[0], 401)
check("unknown account login", call("POST", "/api/login", {"account_id": "NOPE"})[0], 401)
check("hospital account cannot sign in", call("POST", "/api/login", {"account_id": "P0020"})[0], 401)
good_body, good_sig = claimant.split(".")
payload = json.loads(base64.urlsafe_b64decode(good_body + "=" * (-len(good_body) % 4)))
payload["role"] = "admin"
forged = b64(json.dumps(payload).encode()) + "." + good_sig
check("forged admin token rejected", call("GET", "/api/config", token=forged)[0], 401)

print("\nInsurer INS-002")
s, d = call("POST", "/api/login", {"account_id": "INS-002"})
check("login", s, 200)
insurer = d["token"]
print(f"  signed in: {d['profile']['full_name']} | limit Rs {float(d['profile']['approval_limit']):,.0f}")
s, d = call("GET", "/api/config", token=insurer)
check("/api/config", s, 200)
check("criteria count", len(d["criteria"]), 16)
print("  fraud criteria:", sum(c["verdict"] == "FRAUD" for c in d["criteria"]),
      "| not-fraud criteria:", sum(c["verdict"] == "LEGIT" for c in d["criteria"]))
s, d = call("GET", "/api/accounts/PT00000", token=insurer)
check("claimant detail", s, 200)
print("  claimant:", d["profile"]["full_name"], d["summary"])
s, d = call("GET", "/api/accounts/P0020", token=insurer)
check("hospital detail", s, 200)
print("  hospital:", d["profile"]["name"], d["summary"])
check("insurer -> staff account", call("GET", "/api/accounts/ADM-001", token=insurer)[0], 404)
check("insurer -> /api/my/claims", call("GET", "/api/my/claims", token=insurer)[0], 403)

print("\nAdmin ADM-001")
if PASSCODE:
    check("login without passcode", call("POST", "/api/login", {"account_id": "ADM-001"})[0], 401)
    check("login with wrong passcode",
          call("POST", "/api/login", {"account_id": "ADM-001", "passcode": "wrong"})[0], 401)
s, d = call("POST", "/api/login", {"account_id": "ADM-001", "passcode": PASSCODE})
check("login with passcode", s, 200)
admin = d["token"]
check("admin -> staff account INS-001", call("GET", "/api/accounts/INS-001", token=admin)[0], 200)

print("\nOld reviewer endpoints (still open until Step 4)")
check("/api/queue without a token", call("GET", "/api/queue")[0], 200)

print("\n" + ("ALL CHECKS PASSED" if not failures else f"FAILED: {failures}"))