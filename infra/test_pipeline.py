
import json
import random
import time
import urllib.error
import urllib.request
from datetime import date

import boto3

API_URL = next(l.split("=", 1)[1].strip() for l in open(".env")
               if l.startswith("API_URL="))
table = boto3.resource("dynamodb", region_name="us-east-1").Table("Claims")
today = date.today().isoformat()
pid = lambda: f"PT9{random.randint(0, 9999):04d}"
IN_PROGRESS = {"RECEIVED", "SCORED"}


def post(claim):
    req = urllib.request.Request(API_URL, data=json.dumps(claim).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def run(name, claim):
    t0 = time.time()
    code, body = post(claim)
    print(name)
    if code >= 400:
        print(f"  HTTP {code}: {body.get('status')} {body.get('errors')}")
        return
    print(f"  HTTP {code} | pipeline_started={body['pipeline_started']}")
    item = {}
    while time.time() - t0 < 45:
        item = table.get_item(Key={"claim_id": body["claim_id"]},
                              ConsistentRead=True).get("Item", {})
        if item.get("status") not in IN_PROGRESS:
            break
        time.sleep(0.5)
    print(f"  final status: {item.get('status')} | band: {item.get('risk_band')}"
          f" | score: {item.get('risk_score')} | {time.time() - t0:.1f} s end to end")


dup_patient = pid()
dup = {"provider_id": "P0030", "patient_id": dup_patient, "claim_date": today,
       "diagnosis": "fracture", "procedure_code": "XRAY02", "amount": 1550}

run("Normal visit", {"provider_id": "P0010", "patient_id": pid(), "claim_date": today,
                     "diagnosis": "fever", "procedure_code": "CONS01", "amount": 820})
run("Upcoding: fever billed as heart surgery",
    {"provider_id": "P0020", "patient_id": pid(), "claim_date": today,
     "diagnosis": "fever", "procedure_code": "SURG06", "amount": 310000})
run("Original X-ray claim", dup)
run("Same X-ray claim resubmitted (duplicate)", dup)
run("Invalid claim (should be rejected, no workflow)",
    {"provider_id": "P0010", "amount": -5})