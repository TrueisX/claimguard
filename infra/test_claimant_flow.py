"""Check the new website and the claimant flow on the live API."""
import json
import random
import time
import urllib.error
import urllib.request
from datetime import date, timedelta

API_URL = next(l.split("=", 1)[1].strip() for l in open(".env")
               if l.startswith("API_URL="))
BASE = API_URL.rsplit("/claims", 1)[0].rstrip("/")
failures = []
ME = f"PT{random.randint(100, 199):05d}"      # a different policyholder every run


def call(method, path, body=None, token=None, raw=False):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as r:
            text = r.read().decode()
            return r.status, (text if raw else json.loads(text))
    except urllib.error.HTTPError as e:
        text = e.read().decode()
        return e.code, (text if raw else json.loads(text))


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name} (got {got}, want {want})")
    if not ok:
        failures.append(name)


print("Website files")
for path in ("/", "/static/app.css", "/static/app.js", "/static/claimant.js", "/legacy"):
    s, text = call("GET", path, raw=True)
    check(f"GET {path}", s, 200)
s, text = call("GET", "/", raw=True)
check("home page is the new site", "ClaimGuard" in text and "/static/app.js" in text, True)
check("unknown static file", call("GET", "/static/handler.py", raw=True)[0], 404)

print("\nClaimant PT00120")
s, d = call("POST", "/api/login", {"account_id": ME})
check("login", s, 200)
token = d["token"]
s, d = call("GET", "/api/hospitals", token=token)
check("hospital list", s, 200)
check("300 hospitals", len(d["hospitals"]), 300)
check("hospitals need sign-in", call("GET", "/api/hospitals")[0], 401)

today = date.today().isoformat()
claim = {"provider_id": f"P{random.randint(0, 299):04d}", "claim_date": today, "diagnosis": "fever",
         "procedure_code": "CONS01", "amount": random.randint(700, 950), "patient_id": "PT00001"}   # tries to file as someone else
s, d = call("POST", "/api/my/claims", claim, token=token)
check("submit normal claim", s, 202)
cid = d.get("claim_id")
print("  waiting for the pipeline", end="", flush=True)
final = None
for _ in range(40):
    time.sleep(1.5)
    print(".", end="", flush=True)
    s, mine = call("GET", "/api/my/claims", token=token)
    final = next((c for c in mine["claims"] if c["claim_id"] == cid), None)
    if final and final["status"] not in ("RECEIVED", "SCORED"):
        break
print()
check("claim is listed under the signed-in claimant, not the patient_id sent", final is not None, True)
check("normal claim ends AUTO_APPROVED", final and final["status"], "AUTO_APPROVED")
leaks = [k for k in ("risk_score", "risk_band", "features") if final and k in final]
check("claimant never sees risk data", leaks, [])

bad = dict(claim, claim_date=(date.today() + timedelta(days=10)).isoformat())
s, d = call("POST", "/api/my/claims", bad, token=token)
check("future date rejected", s, 400)
s, d = call("POST", "/api/my/claims", dict(claim, provider_id="PT00001"), token=token)
check("non-hospital provider rejected", s, 400)

print("\nOther roles")
s, d = call("POST", "/api/login", {"account_id": "INS-002"})
insurer = d["token"]
check("insurer can read hospitals", call("GET", "/api/hospitals", token=insurer)[0], 200)
check("insurer cannot submit a claim", call("POST", "/api/my/claims", claim, token=insurer)[0], 403)

print("\n" + ("ALL CHECKS PASSED" if not failures else f"FAILED: {failures}"))
raise SystemExit(1 if failures else 0)
