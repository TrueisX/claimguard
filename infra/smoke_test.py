
import json
import random
import urllib.error
import urllib.request
from datetime import date

import boto3

API_URL = next(l.split("=", 1)[1].strip() for l in open(".env")
               if l.startswith("API_URL="))
lam = boto3.client("lambda", region_name="us-east-1")
today = date.today().isoformat()
pid = lambda: f"PT9{random.randint(0, 9999):04d}"     # fresh patient each run


def post(claim):
    req = urllib.request.Request(API_URL, data=json.dumps(claim).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return json.load(e)


def score(claim_id):
    resp = lam.invoke(FunctionName="score-claim",
                      Payload=json.dumps({"claim_id": claim_id}).encode())
    return json.load(resp["Payload"])


dup_patient = pid()
dup = {"provider_id": "P0030", "patient_id": dup_patient, "claim_date": today,
       "diagnosis": "fracture", "procedure_code": "XRAY02", "amount": 1550}

tests = [
    ("Normal visit", {"provider_id": "P0010", "patient_id": pid(), "claim_date": today,
                      "diagnosis": "fever", "procedure_code": "CONS01", "amount": 820}),
    ("Upcoding: fever billed as heart surgery",
     {"provider_id": "P0020", "patient_id": pid(), "claim_date": today,
      "diagnosis": "fever", "procedure_code": "SURG06", "amount": 310000}),
    ("Original X-ray claim", dup),
    ("Same X-ray claim resubmitted (duplicate)", dup),
]

for name, claim in tests:
    stored = post(claim)
    out = score(stored["claim_id"])
    if "errorMessage" in out:
        print(f"{name}\n  ERROR: {out['errorMessage']}")
        continue
    f = out["features"]
    print(f"{name}\n  -> {out['risk_band']}  (score {out['risk_score']})"
          f" | mismatch={f['procedure_mismatch']}  amount_ratio={f['amount_ratio']:.2f}"
          f"  duplicate={f['duplicate_candidate']}"
          f"  days_since_prev={f['days_since_previous_claim']}")