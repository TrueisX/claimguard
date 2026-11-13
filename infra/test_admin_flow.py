"""Live check of the admin console endpoints and account suspension."""
import json
import urllib.error
import urllib.request

import boto3

API_URL = next(l.split("=", 1)[1].strip() for l in open(".env") if l.startswith("API_URL="))
BASE = API_URL.rsplit("/claims", 1)[0].rstrip("/")
failures = []


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


# the admin passcode lives in the Lambda's settings; read it here without printing it
env = boto3.client("lambda", region_name="us-east-1").get_function_configuration(FunctionName="review-api")["Environment"]["Variables"]
s, d = call("POST", "/api/login", {"account_id": "ADM-001", "passcode": env.get("ADMIN_PASSCODE", "")})
assert s == 200, f"admin sign-in failed: {s} {d}"
admin = d["token"]
insurer = call("POST", "/api/login", {"account_id": "INS-002"})[1]["token"]
claimant = call("POST", "/api/login", {"account_id": "PT00120"})[1]["token"]

print("Who may call the admin endpoints")
for path in ("/api/admin/overview", "/api/admin/health", "/api/admin/accounts"):
    check(f"{path} without a token", call("GET", path)[0], 401)
    check(f"{path} as insurer", call("GET", path, token=insurer)[0], 403)
    check(f"{path} as claimant", call("GET", path, token=claimant)[0], 403)
check("wrong admin passcode", call("POST", "/api/login", {"account_id": "ADM-001", "passcode": "nope"})[0], 401)

print("\nOverview")
s, o = call("GET", "/api/admin/overview", token=admin)
check("overview loads", s, 200)
check("pipeline claims counted", o["totals"]["live_claims"] >= 1, True)
check("14 days of volume", len(o["volume"]), 14)
check("10 risk buckets", len(o["risk_histogram"]), 10)
check("accounts counted", (o["accounts"].get("CLAIMANT"), o["accounts"].get("HOSPITAL")), (200, 300))
print(f"  live claims {o['totals']['live_claims']}, auto-approved {o['rates']['auto_approved_pct']}%, flagged {o['rates']['flagged_pct']}%")

print("\nHealth (each probe is allowed to say 'unavailable' if the lab role lacks permission)")
s, hl = call("GET", "/api/admin/health", token=admin)
check("health loads", (s, len(hl["components"])), (200, 8))
for c in hl["components"]:
    print(f"  {'ok ' if c['ok'] else 'N/A'}  {c['name']}: {c.get('detail', '')[:110]}")

print("\nAccounts")
s, a = call("GET", "/api/admin/accounts?kind=HOSPITAL", token=admin)
check("hospital filter", (s, a["total"]), (200, 300))
s, a = call("GET", "/api/admin/accounts?q=PT00042", token=admin)
check("search by id", (s, a["total"]), (200, 1))
try:
    check("suspend a policy", call("POST", "/api/admin/accounts/PT00042/status", {"status": "Suspended"}, token=admin)[0], 200)
    check("suspended claimant cannot sign in", call("POST", "/api/login", {"account_id": "PT00042"})[0], 403)
finally:
    check("reactivate the policy", call("POST", "/api/admin/accounts/PT00042/status", {"status": "Active"}, token=admin)[0], 200)
check("claimant can sign in again", call("POST", "/api/login", {"account_id": "PT00042"})[0], 200)
check("bad status refused", call("POST", "/api/admin/accounts/PT00042/status", {"status": "Deleted"}, token=admin)[0], 400)
check("hospitals cannot be suspended", call("POST", "/api/admin/accounts/P0010/status", {"status": "Suspended"}, token=admin)[0], 404)
check("insurer cannot suspend", call("POST", "/api/admin/accounts/PT00042/status", {"status": "Suspended"}, token=insurer)[0], 403)

print("\n" + ("ALL CHECKS PASSED" if not failures else f"FAILED: {failures}"))
raise SystemExit(1 if failures else 0)
