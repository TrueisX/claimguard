"""Live check of Attack mode: fake fraud goes through the real pipeline, insurers see it, then it is cleared."""
import json
import time
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


env = boto3.client("lambda", region_name="us-east-1").get_function_configuration(FunctionName="review-api")["Environment"]["Variables"]
admin = call("POST", "/api/login", {"account_id": "ADM-001", "passcode": env.get("ADMIN_PASSCODE", "")})[1]["token"]
insurer = call("POST", "/api/login", {"account_id": "INS-004"})[1]["token"]

print("Who may use Attack mode")
check("insurer cannot start it", call("POST", "/api/admin/attack", token=insurer)[0], 403)
check("no token, no attack", call("POST", "/api/admin/attack")[0], 401)

call("POST", "/api/admin/attack/clear", token=admin)          # start from a clean board
try:
    print("\nLaunching 9 fake claims (this takes about 10 seconds)")
    s, d = call("POST", "/api/admin/attack", token=admin)
    check("attack accepted", s, 202)
    print(f"  sent {d.get('started')} of {d.get('planned')}")
    check("most claims were accepted by validation", d.get("started", 0) >= 6, True)
    check("a second attack is refused until cleared", call("POST", "/api/admin/attack", token=admin)[0], 409)

    print("  waiting for the pipeline", end="", flush=True)
    st = {}
    for _ in range(40):
        time.sleep(1.5)
        print(".", end="", flush=True)
        st = call("GET", "/api/admin/attack", token=admin)[1]
        if st.get("done"):
            break
    print()
    check("all claims scored", st.get("done"), True)
    for c in sorted(st["claims"], key=lambda c: c["pattern"]):
        print(f"    {c['pattern']:<18} {str(c['amount']):>8}  risk {float(c['risk_score'] or 0):.3f}  {c['status']}")
    check("the three upcoding claims were all sent to a human", sum(1 for c in st["claims"] if c["pattern"] == "Upcoding" and c["review_queue"]), 3)
    print(f"  caught {st['caught']} of {st['total']}, slipped through {st['missed']}")

    s, q = call("GET", "/api/queue", token=insurer)
    waiting = {i["claim_id"] for i in q["items"]}
    flagged = [c["claim_id"] for c in st["claims"] if c["review_queue"]]
    check("insurer sees every flagged attack claim in the queue", all(c in waiting for c in flagged), True)
finally:
    s, d = call("POST", "/api/admin/attack/clear", token=admin)
    check("attack cleared", s, 200)
    print(f"  removed {d.get('cleared')} demo claims")
check("board is empty again", call("GET", "/api/admin/attack", token=admin)[1].get("active"), False)

print("\n" + ("ALL CHECKS PASSED" if not failures else f"FAILED: {failures}"))
raise SystemExit(1 if failures else 0)
