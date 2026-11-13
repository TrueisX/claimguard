"""review-api: the website, reviewer endpoints, sign-in and account APIs."""
import base64
import json
import os
import re
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

import admin
import portal
from portal import respond

table = boto3.resource("dynamodb").Table(os.environ["TABLE"])
INDEX = "review-queue-index"
HERE = os.path.dirname(__file__)
CONTENT_TYPES = {".html": "text/html; charset=utf-8",
                 ".css": "text/css; charset=utf-8",
                 ".js": "application/javascript; charset=utf-8",
                 ".svg": "image/svg+xml"}


def serve_file(relative):
    """Serve one of the website files that ship inside the Lambda zip."""
    try:
        with open(os.path.join(HERE, relative), encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return respond(404, {"error": "not found"})
    return {"statusCode": 200,
            "headers": {"Content-Type": CONTENT_TYPES[os.path.splitext(relative)[1]],
                        "Cache-Control": "no-cache"},
            "body": text}


# decision -> (new status, label for retraining, new queue or None = leave queue)
DECISIONS = {
    "APPROVE": ("REVIEWER_APPROVED", 0, None),
    "REJECT": ("REVIEWER_REJECTED", 1, None),
    "ESCALATE": ("ESCALATED", None, "PRIORITY"),
    "MANUAL_VERIFICATION": ("MANUAL_VERIFICATION", None, "VERIFICATION"),
}
NOTE_REQUIRED = {"REJECT", "MANUAL_VERIFICATION"}
QUEUES = ("PRIORITY", "REVIEW", "VERIFICATION")
LIST_FIELDS = ["claim_id", "provider_id", "patient_id", "claim_date", "diagnosis",
               "procedure_code", "amount", "risk_score", "risk_band", "status",
               "review_queue", "received_at"]


def queue_items(name, limit=100):
    resp = table.query(IndexName=INDEX,
                       KeyConditionExpression=Key("review_queue").eq(name),
                       ScanIndexForward=False, Limit=limit)
    items = [{k: it.get(k) for k in LIST_FIELDS} for it in resp["Items"]]
    return sorted(items, key=lambda i: float(i.get("risk_score") or 0), reverse=True)


def list_queue(params):
    wanted = (params.get("queue") or "ALL").upper()
    found = {q: (queue_items(q) if wanted in ("ALL", q) else []) for q in QUEUES}
    return respond(200, {"counts": {q: len(found[q]) for q in QUEUES},
                         "items": [i for q in QUEUES for i in found[q]]})


def get_claim(claim_id):
    item = table.get_item(Key={"claim_id": claim_id}, ConsistentRead=True).get("Item")
    return respond(200, item) if item else respond(404, {"error": "claim not found"})


def decide(claim_id, body, p=None):
    """Record a reviewer decision. p is the signed-in principal (None while auth is not enforced)."""
    decision = str(body.get("decision", "")).upper()
    if decision not in DECISIONS:
        return respond(400, {"error": "decision must be APPROVE, REJECT, ESCALATE or MANUAL_VERIFICATION"})
    notes = str(body.get("notes", "")).strip()[:500]
    if decision in NOTE_REQUIRED and len(notes) < 5:
        return respond(400, {"error": "add a short note explaining this decision"})
    status, label, new_queue = DECISIONS[decision]

    reviewer, reviewer_id = str(body.get("reviewer", "reviewer"))[:60], None
    if p is not None:
        if p["role"] != "insurer":
            return respond(403, {"error": "only insurers can decide claims"})
        acct = portal.get_account(p["sub"]) or {}
        reviewer, reviewer_id = acct.get("full_name", p["sub"]), p["sub"]
        if decision == "APPROVE":
            claim = table.get_item(Key={"claim_id": claim_id}, ConsistentRead=True).get("Item")
            limit = acct.get("approval_limit")
            if claim and limit is not None and claim["amount"] > limit:
                return respond(403, {"error": "this claim is above your approval limit - "
                                              "escalate it to a more senior approver",
                                     "approval_limit": limit})

    sets = ["#st = :st", "reviewer_decision = :d", "review_notes = :n",
            "reviewer = :r", "reviewed_at = :t"]
    values = {":st": status, ":d": decision, ":n": notes, ":r": reviewer,
              ":t": datetime.now(timezone.utc).isoformat()}
    if reviewer_id:
        sets.append("reviewer_id = :rid")
        values[":rid"] = reviewer_id
    if label is not None:
        sets.append("reviewer_label = :l")
        values[":l"] = label
    if new_queue:
        sets.append("review_queue = :q")
        values[":q"] = new_queue
        expr = "SET " + ", ".join(sets)
    else:
        expr = "SET " + ", ".join(sets) + " REMOVE review_queue"   # leaves the queue

    try:
        table.update_item(Key={"claim_id": claim_id}, UpdateExpression=expr,
                          ConditionExpression="attribute_exists(review_queue)",
                          ExpressionAttributeNames={"#st": "status"},
                          ExpressionAttributeValues=values)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return respond(409, {"error": "claim is not in a review queue "
                                          "(already decided, or not flagged)"})
        raise
    return respond(200, {"claim_id": claim_id, "decision": decision, "status": status})


def staff_only(event, fn):
    """fn(principal) runs for signed-in staff. While ENFORCE_AUTH is off it runs open, with None."""
    if os.environ.get("ENFORCE_AUTH") == "true":
        return portal.guarded(event, portal.STAFF, fn)
    return fn(None)


def lambda_handler(event, context):
    method = event["requestContext"]["http"]["method"]
    path = event["rawPath"]
    params = event.get("queryStringParameters") or {}

    body = {}
    raw = event.get("body")
    if raw:
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode()
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            return respond(400, {"error": "body is not valid JSON"})

    # ---- the website ----
    if method == "GET" and path in ("/", ""):
        return serve_file("index.html")
    if method == "GET" and path == "/legacy":
        return serve_file("dashboard.html")
    m = re.fullmatch(r"/static/([A-Za-z0-9_-]{1,40}\.(?:css|js|svg))", path)
    if m and method == "GET":
        return serve_file("static/" + m.group(1))

    # ---- sign-in and accounts ----
    if method == "POST" and path == "/api/login":
        return portal.login(body)
    if method == "GET" and path == "/api/demo-accounts":
        return portal.demo_accounts()
    if method == "GET" and path == "/api/me":
        return portal.guarded(event, portal.ALL_ROLES, portal.me)
    if method == "GET" and path == "/api/my/claims":
        return portal.guarded(event, {"claimant"}, portal.my_claims)
    if method == "POST" and path == "/api/my/claims":
        return portal.guarded(event, {"claimant"}, portal.submit_claim, body)
    if method == "GET" and path == "/api/hospitals":
        return portal.guarded(event, portal.ALL_ROLES, portal.hospitals)
    if method == "GET" and path == "/api/config":
        return portal.guarded(event, portal.STAFF, lambda p: portal.get_config())
    m = re.fullmatch(r"/api/accounts/([A-Za-z0-9_-]{1,40})", path)
    if m and method == "GET":
        return portal.guarded(event, portal.STAFF, portal.account_detail, m.group(1))

    # ---- admin ----
    ADMIN = {"admin"}
    if method == "GET" and path == "/api/admin/overview":
        return portal.guarded(event, ADMIN, admin.overview)
    if method == "GET" and path == "/api/admin/health":
        return portal.guarded(event, ADMIN, admin.health)
    if method == "GET" and path == "/api/admin/accounts":
        return portal.guarded(event, ADMIN, admin.list_accounts, params)
    m = re.fullmatch(r"/api/admin/accounts/([A-Za-z0-9_-]{1,40})/status", path)
    if m and method == "POST":
        return portal.guarded(event, ADMIN, admin.set_status, m.group(1), body)

    # ---- reviewer endpoints ----
    if method == "GET" and path == "/api/queue":
        return staff_only(event, lambda p: list_queue(params))
    m = re.fullmatch(r"/api/claims/([A-Za-z0-9_-]{1,80})", path)
    if m and method == "GET":
        return staff_only(event, lambda p: get_claim(m.group(1)))
    m = re.fullmatch(r"/api/claims/([A-Za-z0-9_-]{1,80})/context", path)
    if m and method == "GET":
        return portal.guarded(event, portal.STAFF, portal.claim_context, m.group(1))
    m = re.fullmatch(r"/api/claims/([A-Za-z0-9_-]{1,80})/decision", path)
    if m and method == "POST":
        return staff_only(event, lambda p: decide(m.group(1), body, p))
    return respond(404, {"error": "not found"})
