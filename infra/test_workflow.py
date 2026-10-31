
import json
import random
import time
import urllib.error
import urllib.request
from datetime import date

import boto3

REGION = "us-east-1"
API_URL = next(l.split("=", 1)[1].strip() for l in open(".env")
               if l.startswith("API_URL="))
account = boto3.client("sts").get_caller_identity()["Account"]
MACHINE = f"arn:aws:states:{REGION}:{account}:stateMachine:claimguard-triage"

sfn = boto3.client("stepfunctions", region_name=REGION)
sqs = boto3.client("sqs", region_name=REGION)
table = boto3.resource("dynamodb", region_name=REGION).Table("Claims")
today = date.today().isoformat()
pid = lambda: f"PT9{random.randint(0, 9999):04d}"


def post(claim):
    req = urllib.request.Request(API_URL, data=json.dumps(claim).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return json.load(e)


def queue_depth(name):
    url = sqs.get_queue_url(QueueName=name)["QueueUrl"]
    a = sqs.get_queue_attributes(QueueUrl=url,
                                 AttributeNames=["ApproximateNumberOfMessages"])
    return int(a["Attributes"]["ApproximateNumberOfMessages"])


def run(name, claim):
    claim_id = post(claim)["claim_id"]
    ex = sfn.start_execution(stateMachineArn=MACHINE,
                             input=json.dumps({"claim_id": claim_id}))["executionArn"]
    for _ in range(60):                       # wait up to ~60 s
        status = sfn.describe_execution(executionArn=ex)["status"]
        if status != "RUNNING":
            break
        time.sleep(1)
    item = table.get_item(Key={"claim_id": claim_id}).get("Item", {})
    print(f"{name}\n  workflow: {status} | claim status: {item.get('status')}"
          f" | band: {item.get('risk_band')} | score: {item.get('risk_score')}")


before = {q: queue_depth(q) for q in ("claimguard-review", "claimguard-priority")}

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

after = {q: queue_depth(q) for q in before}
print("\nMessages added to queues:",
      {q: after[q] - before[q] for q in before})