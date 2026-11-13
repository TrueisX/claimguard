"""portal: sign-in tokens, role checks, and the account/profile endpoints."""
import base64
import hashlib
import hmac
import json
import os
import re
import time
from datetime import date, timedelta
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr, Key

ddb = boto3.resource("dynamodb")
lam = boto3.client("lambda")
accounts = ddb.Table(os.environ.get("ACCOUNTS_TABLE", "ClaimGuardAccounts"))
claims = ddb.Table(os.environ["TABLE"])
SECRET = os.environ.get("TOKEN_SECRET", "").encode()
ADMIN_PASSCODE = os.environ.get("ADMIN_PASSCODE", "")
TOKEN_HOURS = 8

ROLE_OF_KIND = {"CLAIMANT": "claimant", "INSURER": "insurer", "ADMIN": "admin"}
ALL_ROLES = {"claimant", "insurer", "admin"}
STAFF = {"insurer", "admin"}

FEATURED_CLAIMANTS = ["PT00000", "PT00007", "PT00042", "PT00120"]
FEATURED_INSURERS = ["INS-001", "INS-002", "INS-003", "INS-004"]

# What a claimant sees. Deliberately vague: no scores, no "priority".
STATUS_LABELS = {
    "HISTORY": "Settled",
    "RECEIVED": "Received",
    "SCORED": "Being checked",
    "AUTO_APPROVED": "Approved",
    "PENDING_REVIEW": "Under review",
    "PRIORITY_REVIEW": "Under review",
    "ESCALATED": "Under senior review",
    "MANUAL_VERIFICATION": "Verification in progress",
    "REVIEWER_APPROVED": "Approved after review",
    "REVIEWER_REJECTED": "Rejected",
    "SCORING_FAILED": "Processing delayed",
}
FLAGGED = {"PENDING_REVIEW", "PRIORITY_REVIEW", "ESCALATED",
           "MANUAL_VERIFICATION", "REVIEWER_REJECTED"}


# ---------- responses ----------
def _json(o):
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    raise TypeError(f"cannot serialize {type(o)}")


def respond(status, body):
    return {"statusCode": status,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body, default=_json)}


# ---------- tokens ----------
def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(body):
    return _b64(hmac.new(SECRET, body.encode(), hashlib.sha256).digest())


def make_token(account_id, role):
    if not SECRET:                                   # fail closed, never sign with an empty key
        raise RuntimeError("TOKEN_SECRET is not configured")
    body = _b64(json.dumps({"sub": account_id, "role": role,
                            "exp": int(time.time()) + TOKEN_HOURS * 3600}).encode())
    return f"{body}.{_sign(body)}"


def read_token(token):
    if not SECRET or not token or token.count(".") != 1:
        return None
    body, sig = token.split(".")
    if not hmac.compare_digest(sig, _sign(body)):
        return None
    try:
        data = json.loads(_unb64(body))
    except ValueError:
        return None
    return data if data.get("exp", 0) >= time.time() else None


def principal(event):
    auth = (event.get("headers") or {}).get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    return read_token(auth[7:].strip())


def guarded(event, roles, fn, *args):
    """Run fn(principal, *args) only for a valid token whose role is allowed."""
    p = principal(event)
    if p is None:
        return respond(401, {"error": "sign in required"})
    if p["role"] not in roles:
        return respond(403, {"error": "your role cannot do this"})
    return fn(p, *args)


# ---------- data helpers ----------
def get_account(account_id):
    return accounts.get_item(Key={"account_id": account_id}).get("Item")


def query_all(index, key, value):
    kwargs = {"IndexName": index, "KeyConditionExpression": Key(key).eq(value)}
    items = []
    while True:
        resp = claims.query(**kwargs)
        items += resp["Items"]
        if "LastEvaluatedKey" not in resp:
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def hospital_names(provider_ids):
    ids, names = sorted(provider_ids), {}
    for i in range(0, len(ids), 100):
        resp = ddb.batch_get_item(RequestItems={accounts.name: {
            "Keys": [{"account_id": x} for x in ids[i:i + 100]],
            "ProjectionExpression": "account_id, #n",
            "ExpressionAttributeNames": {"#n": "name"}}})
        for it in resp["Responses"][accounts.name]:
            names[it["account_id"]] = it["name"]
    return names


def claimant_summary(acct):
    items = query_all("patient-index", "patient_id", acct["patient_id"])
    by_status = {}
    for i in items:
        lab = STATUS_LABELS.get(i.get("status"), "In progress")
        by_status[lab] = by_status.get(lab, 0) + 1
    return {"claims_total": len(items),
            "total_claimed": round(sum(float(i["amount"]) for i in items), 2),
            "by_status": by_status}


def hospital_summary(provider_id):
    items = query_all("provider-index", "provider_id", provider_id)
    cutoff = (date.today() - timedelta(days=30)).isoformat()
    amounts = [float(i["amount"]) for i in items]
    return {"claims_total": len(items),
            "claims_last_30d": sum(1 for i in items if i["claim_date"] >= cutoff),
            "avg_amount": round(sum(amounts) / len(amounts), 2) if amounts else 0,
            "flagged": sum(1 for i in items if i.get("status") in FLAGGED)}


# ---------- endpoints ----------
def login(body):
    account_id = str(body.get("account_id", "")).strip()
    acct = get_account(account_id) if account_id else None
    role = ROLE_OF_KIND.get(acct["kind"]) if acct else None
    if not role:
        return respond(401, {"error": "unknown account"})
    if role == "claimant" and acct.get("policy_status") == "Suspended":
        return respond(403, {"error": "this policy is suspended - contact your insurer"})
    if role == "admin" and ADMIN_PASSCODE:
        given = str(body.get("passcode", "")).encode()
        if not hmac.compare_digest(given, ADMIN_PASSCODE.encode()):
            return respond(401, {"error": "admin passcode required"})
    return respond(200, {"token": make_token(account_id, role),
                         "role": role, "profile": acct})


def demo_accounts():
    keys = FEATURED_CLAIMANTS + FEATURED_INSURERS + ["ADM-001"]
    resp = ddb.batch_get_item(RequestItems={
        accounts.name: {"Keys": [{"account_id": k} for k in keys]}})
    found = {i["account_id"]: i for i in resp["Responses"][accounts.name]}
    pick = lambda ids, fields: [{f: found[i].get(f) for f in fields}
                                for i in ids if i in found]
    admin = pick(["ADM-001"], ["account_id", "full_name", "job_title"])
    for a in admin:
        a["needs_passcode"] = bool(ADMIN_PASSCODE)
    return respond(200, {
        "claimants": pick(FEATURED_CLAIMANTS,
                          ["account_id", "full_name", "city", "plan"]),
        "insurers": pick(FEATURED_INSURERS,
                         ["account_id", "full_name", "job_title", "approval_limit"]),
        "admin": admin})


def me(p):
    acct = get_account(p["sub"])
    if not acct:
        return respond(404, {"error": "account not found"})
    out = {"role": p["role"], "profile": acct}
    if p["role"] == "claimant":
        out["summary"] = claimant_summary(acct)
    return respond(200, out)


def my_claims(p):
    acct = get_account(p["sub"])
    if not acct:
        return respond(404, {"error": "account not found"})
    items = query_all("patient-index", "patient_id", acct["patient_id"])
    items.sort(key=lambda i: (i["claim_date"], i.get("received_at", "")), reverse=True)
    items = items[:100]
    names = hospital_names({i["provider_id"] for i in items})
    out = [{"claim_id": i["claim_id"], "claim_date": i["claim_date"],
            "diagnosis": i["diagnosis"], "procedure_code": i["procedure_code"],
            "amount": i["amount"], "provider_id": i["provider_id"],
            "hospital_name": names.get(i["provider_id"], i["provider_id"]),
            "status": i.get("status"),
            "status_label": STATUS_LABELS.get(i.get("status"), "In progress")}
           for i in items]          # no risk score, band, features or reviewer notes
    return respond(200, {"count": len(out), "claims": out})


def hospitals(p):
    kwargs = {"FilterExpression": Attr("kind").eq("HOSPITAL"),
              "ProjectionExpression": "#id, #n, #c, #ns",
              "ExpressionAttributeNames": {"#id": "account_id", "#n": "name",
                                           "#c": "city", "#ns": "network_status"}}
    items = []
    while True:
        resp = accounts.scan(**kwargs)
        items += resp["Items"]
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    items.sort(key=lambda i: i["name"])
    return respond(200, {"hospitals": [
        {"provider_id": i["account_id"], "name": i["name"], "city": i["city"],
         "network_status": i.get("network_status")} for i in items]})


def submit_claim(p, body):
    """A claimant files a claim. The patient is always the signed-in claimant."""
    acct = get_account(p["sub"])
    if not acct:
        return respond(404, {"error": "account not found"})
    if acct.get("policy_status") == "Suspended":
        return respond(403, {"status": "REJECTED", "errors": ["this policy is suspended - contact your insurer"]})
    claim = {k: body[k] for k in ("provider_id", "claim_date", "diagnosis",
                                  "procedure_code", "amount") if k in body}
    claim["patient_id"] = acct["patient_id"]

    problems = []
    day = str(claim.get("claim_date", ""))
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        if day > (date.today() + timedelta(days=1)).isoformat():    # a day of slack for time zones
            problems.append("claim date cannot be in the future")
        if day < acct["cover_from"]:
            problems.append("claim date is before your policy started")
    pid = str(claim.get("provider_id", ""))
    hosp = get_account(pid) if pid else None
    if not hosp or hosp.get("kind") != "HOSPITAL":
        problems.append("choose a hospital from the list")
    if problems:
        return respond(400, {"status": "REJECTED", "errors": problems})

    resp = lam.invoke(FunctionName="validate-claim", Payload=json.dumps(claim).encode())
    out = json.loads(resp["Payload"].read())
    if "statusCode" not in out:
        return respond(502, {"error": "couldn't submit the claim right now"})
    return respond(out["statusCode"], json.loads(out["body"]))


def account_detail(p, account_id):
    acct = get_account(account_id)
    allowed = {"CLAIMANT", "HOSPITAL"} | ({"INSURER", "ADMIN"} if p["role"] == "admin" else set())
    if not acct or acct["kind"] not in allowed:
        return respond(404, {"error": "account not found"})
    out = {"profile": acct}
    if acct["kind"] == "CLAIMANT":
        out["summary"] = claimant_summary(acct)
    elif acct["kind"] == "HOSPITAL":
        out["summary"] = hospital_summary(account_id)
    return respond(200, out)


def get_config():
    crit = accounts.get_item(Key={"account_id": "CONFIG#criteria"}).get("Item", {})
    route = accounts.get_item(Key={"account_id": "CONFIG#routing"}).get("Item", {})
    route = {k: v for k, v in route.items() if k not in ("account_id", "kind")}
    return respond(200, {"criteria": crit.get("criteria", []), "routing": route})


def claim_context(p, claim_id):
    """Everything an insurer needs beside a claim: the claimant, the hospital, recent history."""
    claim = claims.get_item(Key={"claim_id": claim_id}, ConsistentRead=True).get("Item")
    if not claim:
        return respond(404, {"error": "claim not found"})
    out = {"claim": claim, "claimant": None, "hospital": None, "history": []}

    who = get_account(claim["patient_id"])
    if who and who.get("kind") == "CLAIMANT" and who.get("patient_id") == claim["patient_id"]:
        out["claimant"] = {"profile": who, "summary": claimant_summary(who)}
    hosp = get_account(claim["provider_id"])
    if hosp and hosp.get("kind") == "HOSPITAL":
        out["hospital"] = {"profile": hosp, "summary": hospital_summary(claim["provider_id"])}

    past = [i for i in query_all("patient-index", "patient_id", claim["patient_id"])
            if i["claim_id"] != claim_id]
    past.sort(key=lambda i: (i["claim_date"], i.get("received_at", "")), reverse=True)
    out["history"] = [{k: i.get(k) for k in ("claim_id", "claim_date", "provider_id", "diagnosis",
                                              "procedure_code", "amount", "status")}
                      for i in past[:8]]
    return respond(200, out)
