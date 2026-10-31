
import json
import random
import time
import urllib.error
import urllib.request
from datetime import date

API_URL = next(l.split("=", 1)[1].strip() for l in open(".env")
               if l.startswith("API_URL="))
BASE = API_URL.rsplit("/claims", 1)[0]
today = date.today().isoformat()
pid = lambda: f"PT9{random.randint(0, 9999):04d}"


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def submit(claim):
    code, body = call("POST", "/claims", claim)
    cid = body["claim_id"]
    for _ in range(90):                          # wait up to ~45 s for the workflow
        _, item = call("GET", f"/api/claims/{cid}")
        if item.get("status") not in ("RECEIVED", "SCORED"):
            break
        time.sleep(0.5)
    return cid


def show_queue(title):
    _, q = call("GET", "/api/queue")
    print(f"\n{title}: {q['counts']}")
    for i in q["items"][:8]:
        print(f"  {i['review_queue']:8s} {i['risk_band']:6s} {float(i['risk_score']):.3f}"
              f"  {i['provider_id']} {i['procedure_code']:7s} Rs {float(i['amount']):,.0f}")
    return {i["claim_id"] for i in q["items"]}


dup = {"provider_id": "P0030", "patient_id": pid(), "claim_date": today,
       "diagnosis": "fracture", "procedure_code": "XRAY02", "amount": 1550}
high_id = submit({"provider_id": "P0020", "patient_id": pid(), "claim_date": today,
                  "diagnosis": "fever", "procedure_code": "SURG06", "amount": 310000})
submit(dup)
med_id = submit(dup)

ids = show_queue("Queue after submitting claims")
print("\nHigh-risk claim in queue:", high_id in ids,
      "| duplicate claim in queue:", med_id in ids)

_, detail = call("GET", f"/api/claims/{high_id}")
print("\nOpened claim:", detail["status"], "| features seen by the model:")
print(" ", {k: round(float(v), 2) for k, v in detail["features"].items()})

print("\nReject high-risk:", call("POST", f"/api/claims/{high_id}/decision",
      {"decision": "REJECT", "notes": "Fever billed as cardiac surgery", "reviewer": "demo"}))
print("Approve duplicate:", call("POST", f"/api/claims/{med_id}/decision",
      {"decision": "APPROVE", "notes": "Resubmission, original was never paid"}))
print("Decide again (expect 409):", call("POST", f"/api/claims/{med_id}/decision",
      {"decision": "REJECT"}))
print("Bad decision (expect 400):", call("POST", f"/api/claims/{high_id}/decision",
      {"decision": "MAYBE"}))

ids = show_queue("Queue after decisions")
print("\nDecided claims left the queue:", high_id not in ids and med_id not in ids)