
import json
import os
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3

s3 = boto3.client("s3")
sfn = boto3.client("stepfunctions")
table = boto3.resource("dynamodb").Table(os.environ["TABLE"])
BUCKET = os.environ["BUCKET"]
MACHINE_ARN = os.environ.get("STATE_MACHINE_ARN")

PROCEDURES = {"CONS01", "XRAY02", "BLOOD03", "MRI04", "SURG05", "SURG06"}
DIAGNOSES = {"fever", "fracture", "back_pain", "appendicitis", "cardiac"}
REQUIRED = ["provider_id", "patient_id", "claim_date",
            "diagnosis", "procedure_code", "amount"]


def validate(c):
    errors = [f"missing field: {f}" for f in REQUIRED if f not in c]
    if errors:
        return errors
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(c["claim_date"])):
        errors.append("claim_date must be YYYY-MM-DD")
    else:
        try:
            datetime.strptime(c["claim_date"], "%Y-%m-%d")
        except ValueError:
            errors.append("claim_date is not a real date")
    if c["diagnosis"] not in DIAGNOSES:
        errors.append(f"unknown diagnosis: {c['diagnosis']}")
    if c["procedure_code"] not in PROCEDURES:
        errors.append(f"unknown procedure_code: {c['procedure_code']}")
    try:
        if float(c["amount"]) <= 0:
            errors.append("amount must be greater than 0")
    except (TypeError, ValueError):
        errors.append("amount must be a number")
    return errors


def respond(status, body):
    return {"statusCode": status,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body)}


def start_pipeline(claim_id):
    """Start the triage workflow. Returns True if it is running."""
    if not MACHINE_ARN:
        return False
    kwargs = {"stateMachineArn": MACHINE_ARN,
              "input": json.dumps({"claim_id": claim_id})}
    if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", claim_id):
        kwargs["name"] = claim_id          # same claim can't be processed twice
    try:
        sfn.start_execution(**kwargs)
        return True
    except sfn.exceptions.ExecutionAlreadyExists:
        return True
    except Exception as e:                 # claim stays stored as RECEIVED
        print(f"could not start pipeline for {claim_id}: {e}")
        return False


def lambda_handler(event, context):
    # API Gateway sends the claim as a JSON string in event["body"];
    # a direct test invoke sends the claim as the event itself.
    claim = event
    if isinstance(event, dict) and "body" in event:
        try:
            claim = json.loads(event["body"]) if event["body"] else {}
        except json.JSONDecodeError:
            return respond(400, {"error": "body is not valid JSON"})

    errors = validate(claim)
    if errors:
        return respond(400, {"status": "REJECTED", "errors": errors})

    claim_id = claim.get("claim_id") or str(uuid.uuid4())
    claim["claim_id"] = claim_id
    claim["amount"] = round(float(claim["amount"]), 2)
    claim["received_at"] = datetime.now(timezone.utc).isoformat()

    s3.put_object(Bucket=BUCKET, Key=f"raw/{claim_id}.json",
                  Body=json.dumps(claim).encode(),
                  ContentType="application/json")

    item = {**claim, "amount": Decimal(str(claim["amount"])),
            "status": "RECEIVED"}      # DynamoDB needs Decimal, not float
    table.put_item(Item=item)

    started = start_pipeline(claim_id)
    return respond(202, {"status": "RECEIVED", "claim_id": claim_id,
                         "pipeline_started": started})