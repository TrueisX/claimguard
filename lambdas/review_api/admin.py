"""admin: platform overview, system health, account management (admin role only)."""
import json
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import boto3

import portal
from portal import respond

REGION = os.environ.get("AWS_REGION", "us-east-1")
FLAGGED = portal.FLAGGED
DECIDED = {"REVIEWER_APPROVED", "REVIEWER_REJECTED"}

# From the offline benchmark on the simulated test set, not from live traffic.
MODEL_INFO = {"type": "XGBoost gradient-boosted trees", "inputs": 11,
              "fraud_caught_pct": 91.0, "legit_delayed_pct": 0.3,
              "benchmark": "simulated test set"}

_cache = {}


def _cached(key, seconds, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < seconds:
        return hit[1]
    value = fn()
    _cache[key] = (time.time(), value)
    return value


def _scan_claims():
    fields = ["claim_id", "status", "review_queue", "risk_score", "amount", "reviewer_decision",
              "reviewer_label", "received_at", "reviewer", "reviewed_at", "review_notes",
              "provider_id", "patient_id"]
    names = {f"#f{i}": f for i, f in enumerate(fields)}
    kwargs = {"ProjectionExpression": ", ".join(names), "ExpressionAttributeNames": names}
    items = []
    while True:
        resp = portal.claims.scan(**kwargs)
        items += resp["Items"]
        if "LastEvaluatedKey" not in resp:
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def _scan_accounts():
    items, kwargs = [], {}
    while True:
        resp = portal.accounts.scan(**kwargs)
        items += resp["Items"]
        if "LastEvaluatedKey" not in resp:
            return [i for i in items if not str(i["account_id"]).startswith(("CONFIG#", "DEMO#"))]
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def overview(p):
    def build():
        rows = _scan_claims()
        live = [r for r in rows if r.get("status") != "HISTORY"]
        by_status = {}
        for r in live:
            by_status[r.get("status", "UNKNOWN")] = by_status.get(r.get("status", "UNKNOWN"), 0) + 1

        scored = [r for r in live if r.get("risk_score") is not None]
        buckets = [0] * 10
        for r in scored:
            s = float(r["risk_score"])
            buckets[min(int((s if s <= 1 else s / 100) * 10), 9)] += 1

        today = datetime.now(timezone.utc).date()
        days = [(today - timedelta(days=n)).isoformat() for n in range(13, -1, -1)]
        volume = {d: 0 for d in days}
        for r in live:
            d = str(r.get("received_at", ""))[:10]
            if d in volume:
                volume[d] += 1

        approved = [r for r in live if r.get("reviewer_decision") == "APPROVE"]
        rejected = [r for r in live if r.get("reviewer_decision") == "REJECT"]
        waiting = [r for r in live if r.get("review_queue")]
        auto = by_status.get("AUTO_APPROVED", 0)
        n = len(live)
        decided = len(approved) + len(rejected)

        audit = sorted([r for r in live if r.get("reviewed_at")], key=lambda r: r["reviewed_at"], reverse=True)[:15]
        kinds = {}
        for a in _scan_accounts():
            kinds[a["kind"]] = kinds.get(a["kind"], 0) + 1
        return {
            "totals": {"all_claims": len(rows), "live_claims": n, "history_claims": len(rows) - n},
            "by_status": by_status,
            "rates": {"auto_approved_pct": round(100 * auto / n, 1) if n else None,
                      "flagged_pct": round(100 * sum(1 for r in live if r.get("status") in FLAGGED) / n, 1) if n else None,
                      "reviewer_reject_pct": round(100 * len(rejected) / decided, 1) if decided else None},
            "money": {"waiting": round(sum(float(r["amount"]) for r in waiting), 2),
                      "stopped": round(sum(float(r["amount"]) for r in rejected), 2)},
            "queue_waiting": len(waiting),
            "decisions": {"approved": len(approved), "rejected": len(rejected), "decided": decided,
                          "labels_for_retraining": sum(1 for r in live if r.get("reviewer_label") is not None)},
            "risk_histogram": buckets,
            "volume": [{"date": d, "claims": volume[d]} for d in days],
            "audit": [{k: r.get(k) for k in ("claim_id", "amount", "reviewer_decision", "reviewer", "reviewed_at",
                                              "review_notes", "patient_id", "provider_id")} for r in audit],
            "accounts": kinds,
            "model": MODEL_INFO,
        }
    return respond(200, _cached("overview", 20, build))


# ---------- health ----------
def _probe(fn):
    try:
        return dict(ok=True, **fn())
    except Exception as e:                                   # a missing permission must not break the page
        return {"ok": False, "detail": f"{type(e).__name__}: {str(e)[:140]}"}


def _dynamo(table):
    d = boto3.client("dynamodb", region_name=REGION).describe_table(TableName=table)["Table"]
    return {"detail": f"{d['TableStatus'].title()}, about {d.get('ItemCount', 0):,} items",
            "indexes": len(d.get("GlobalSecondaryIndexes", []))}


def _queue(name):
    sqs = boto3.client("sqs", region_name=REGION)
    url = sqs.get_queue_url(QueueName=name)["QueueUrl"]
    a = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"])["Attributes"]
    return {"detail": f"{a['ApproximateNumberOfMessages']} waiting, {a['ApproximateNumberOfMessagesNotVisible']} being read"}


def _workflow():
    sf = boto3.client("stepfunctions", region_name=REGION)
    machines = [m for m in sf.list_state_machines()["stateMachines"] if "claim" in m["name"].lower()]
    if not machines:
        return {"detail": "No state machine with 'claim' in its name"}
    m = machines[0]
    ex = sf.list_executions(stateMachineArn=m["stateMachineArn"], maxResults=50)["executions"]
    counts = {}
    for e in ex:
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    done = [(e["stopDate"] - e["startDate"]).total_seconds() for e in ex if e.get("stopDate")]
    avg = f", average {sum(done) / len(done):.1f}s" if done else ""
    bad = counts.get("FAILED", 0) + counts.get("TIMED_OUT", 0) + counts.get("ABORTED", 0)
    return {"detail": f"{m['name']}: last {len(ex)} runs - " + ", ".join(f"{v} {k.lower()}" for k, v in sorted(counts.items())) + avg,
            "warn": bad > 0}


def _function(name):
    lam = boto3.client("lambda", region_name=REGION)
    c = lam.get_function_configuration(FunctionName=name)
    out = {"detail": f"{c['Runtime']}, {c['MemorySize']} MB, last updated {c['LastModified'][:10]}"}
    try:
        cw = boto3.client("cloudwatch", region_name=REGION)
        end = datetime.now(timezone.utc)
        stats = {}
        for metric, stat in (("Invocations", "Sum"), ("Errors", "Sum"), ("Duration", "Average")):
            pts = cw.get_metric_statistics(Namespace="AWS/Lambda", MetricName=metric,
                                           Dimensions=[{"Name": "FunctionName", "Value": name}],
                                           StartTime=end - timedelta(hours=24), EndTime=end, Period=86400, Statistics=[stat])["Datapoints"]
            stats[metric] = sum(x[stat] for x in pts) / (len(pts) or 1) if stat == "Average" else sum(x[stat] for x in pts)
        out["detail"] += f". Last 24h: {int(stats['Invocations'])} calls, {int(stats['Errors'])} errors, {stats['Duration']:.0f} ms average"
        out["warn"] = stats["Errors"] > 0
    except Exception:
        out["detail"] += ". Usage numbers need CloudWatch access"
    return out


def health(p):
    jobs = [("Claims table", lambda: _dynamo(os.environ["TABLE"])),
            ("Accounts table", lambda: _dynamo(portal.accounts.name)),
            ("Review queue (SQS)", lambda: _queue("claimguard-review")),
            ("Priority queue (SQS)", lambda: _queue("claimguard-priority")),
            ("Triage workflow", _workflow),
            ("validate-claim", lambda: _function("validate-claim")),
            ("score-claim", lambda: _function("score-claim")),
            ("review-api", lambda: _function("review-api"))]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda j: _probe(j[1]), jobs))
    return respond(200, {"checked_at": datetime.now(timezone.utc).isoformat(),
                         "components": [dict(name=j[0], **r) for j, r in zip(jobs, results)]})


# ---------- accounts ----------
def list_accounts(p, params):
    kind = (params.get("kind") or "").upper()
    q = (params.get("q") or "").strip().lower()[:40]
    rows = _scan_accounts()
    counts = {}
    for r in rows:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    keep = ("account_id", "kind", "full_name", "name", "city", "plan", "policy_status", "network_status", "job_title")
    hits = [{k: r.get(k) for k in keep} for r in rows
            if (not kind or r["kind"] == kind)
            and (not q or q in " ".join(str(r.get(k, "")) for k in ("account_id", "full_name", "name", "city")).lower())]
    hits.sort(key=lambda r: r["account_id"])
    return respond(200, {"counts": counts, "total": len(hits), "accounts": hits[:100]})


def set_status(p, account_id, body):
    acct = portal.get_account(account_id)
    status = str(body.get("status", ""))
    if not acct or acct["kind"] != "CLAIMANT":
        return respond(404, {"error": "only policyholder accounts can be suspended"})
    if status not in ("Active", "Suspended"):
        return respond(400, {"error": "status must be Active or Suspended"})
    portal.accounts.update_item(
        Key={"account_id": account_id},
        UpdateExpression="SET policy_status = :s, status_changed_by = :by, status_changed_at = :t",
        ExpressionAttributeValues={":s": status, ":by": p["sub"], ":t": datetime.now(timezone.utc).isoformat()})
    return respond(200, {"account_id": account_id, "policy_status": status})


# ---------- attack mode: a labelled burst of fake fraud for live demos ----------
ATTACK_KEY = {"account_id": "DEMO#attack"}
PENDING = {None, "RECEIVED", "SCORED"}


def _send(claim):
    resp = portal.lam.invoke(FunctionName="validate-claim", Payload=json.dumps(claim).encode())
    out = json.loads(resp["Payload"].read())
    body = json.loads(out["body"]) if "body" in out else {}
    return out.get("statusCode"), body


def start_attack(p):
    if portal.accounts.get_item(Key=ATTACK_KEY).get("Item"):
        return respond(409, {"error": "a demo attack is already on the board - clear it first"})
    today = date.today().isoformat()
    rows = _scan_accounts()
    people = [r for r in rows if r["kind"] == "CLAIMANT" and r.get("policy_status") != "Suspended" and r["cover_from"] <= today]
    hospitals = [r for r in rows if r["kind"] == "HOSPITAL"]
    if len(people) < 9 or len(hospitals) < 5:
        return respond(500, {"error": "not enough demo accounts to run the attack"})
    random.shuffle(people)
    random.shuffle(hospitals)

    plan = []        # (pattern, claim)
    for n, amount in enumerate((310000, 275000, 420000)):
        plan.append(("Upcoding", dict(patient_id=people[n]["patient_id"], provider_id=hospitals[n]["account_id"],
                                      diagnosis="fever", procedure_code="SURG06", amount=amount)))
    dup = dict(patient_id=people[3]["patient_id"], provider_id=hospitals[3]["account_id"],
               diagnosis="fracture", procedure_code="XRAY02", amount=4200)
    plan += [("Duplicate billing", dict(dup)) for _ in range(3)]
    for n, amount in enumerate((92000, 95000, 98000)):
        plan.append(("Provider burst", dict(patient_id=people[4 + n]["patient_id"], provider_id=hospitals[4]["account_id"],
                                           diagnosis="back_pain", procedure_code="MRI04", amount=amount)))

    items = []
    for pattern, claim in plan:
        claim["claim_date"] = today
        code, body = _send(claim)
        if code == 202 and body.get("claim_id"):
            items.append({"claim_id": body["claim_id"], "pattern": pattern})
    if items:
        portal.accounts.put_item(Item={**ATTACK_KEY, "kind": "DEMO", "created_at": datetime.now(timezone.utc).isoformat(),
                                       "items": items, "started_by": p["sub"]})
    _cache.pop("graph", None)
    return respond(202 if items else 502, {"started": len(items), "planned": len(plan)})


def attack_status(p):
    rec = portal.accounts.get_item(Key=ATTACK_KEY).get("Item")
    if not rec:
        return respond(200, {"active": False})
    out = []
    for it in rec["items"]:
        c = portal.claims.get_item(Key={"claim_id": it["claim_id"]}).get("Item") or {}
        out.append({"claim_id": it["claim_id"], "pattern": it["pattern"], "amount": c.get("amount"),
                    "procedure_code": c.get("procedure_code"), "diagnosis": c.get("diagnosis"), "provider_id": c.get("provider_id"),
                    "status": c.get("status"), "risk_score": c.get("risk_score"), "risk_band": c.get("risk_band"),
                    "review_queue": c.get("review_queue")})
    done = all(c["status"] not in PENDING for c in out)
    caught = sum(1 for c in out if c["status"] in FLAGGED or c["status"] in DECIDED or c["status"] == "MANUAL_VERIFICATION")
    return respond(200, {"active": True, "done": done, "total": len(out), "caught": caught,
                         "missed": sum(1 for c in out if c["status"] == "AUTO_APPROVED"), "claims": out})


def clear_attack(p):
    rec = portal.accounts.get_item(Key=ATTACK_KEY).get("Item")
    if not rec:
        return respond(200, {"cleared": 0})
    for it in rec["items"]:
        portal.claims.delete_item(Key={"claim_id": it["claim_id"]})
    portal.accounts.delete_item(Key=ATTACK_KEY)
    _cache.pop("overview", None)
    _cache.pop("graph", None)
    return respond(200, {"cleared": len(rec["items"])})


# ---------- fraud network ----------
def graph(p):
    def build():
        route = portal.accounts.get_item(Key={"account_id": "CONFIG#routing"}).get("Item", {})
        low = float(route.get("low_max", 0.3))
        rows = [r for r in _scan_claims() if r.get("status") != "HISTORY" and r.get("risk_score") is not None
                and float(r["risk_score"]) >= low]
        pairs = {}
        for r in rows:
            e = pairs.setdefault((r["patient_id"], r["provider_id"]),
                                 {"source": r["patient_id"], "target": r["provider_id"], "claims": 0, "amount": 0.0, "max_risk": 0.0})
            e["claims"] += 1
            e["amount"] += float(r["amount"])
            e["max_risk"] = max(e["max_risk"], float(r["risk_score"]))
        edges = sorted(pairs.values(), key=lambda e: (-e["max_risk"], -e["claims"]))[:60]

        who = {a["account_id"]: a for a in _scan_accounts()}
        nodes = {}
        for e in edges:
            for nid, kind in ((e["source"], "CLAIMANT"), (e["target"], "HOSPITAL")):
                a = who.get(nid) or {}
                n = nodes.setdefault(nid, {"id": nid, "kind": kind, "label": a.get("full_name") or a.get("name") or nid,
                                           "claims": 0, "amount": 0.0, "max_risk": 0.0})
                n["claims"] += e["claims"]
                n["amount"] += e["amount"]
                n["max_risk"] = max(n["max_risk"], e["max_risk"])

        findings = []
        for n in nodes.values():
            if n["kind"] == "HOSPITAL":
                mine = [e for e in edges if e["target"] == n["id"]]
                if len(mine) >= 3:
                    findings.append({"type": "hub", "provider_id": n["id"], "label": n["label"], "patients": len(mine),
                                     "claims": n["claims"], "amount": round(n["amount"], 2), "max_risk": n["max_risk"]})
            else:
                mine = [e for e in edges if e["source"] == n["id"]]
                if len(mine) >= 3:
                    findings.append({"type": "hopper", "patient_id": n["id"], "label": n["label"], "hospitals": len(mine),
                                     "claims": n["claims"], "amount": round(n["amount"], 2), "max_risk": n["max_risk"]})
        for e in edges:
            if e["claims"] >= 2:
                findings.append({"type": "repeat", "patient_id": e["source"], "provider_id": e["target"], "claims": e["claims"],
                                 "label": (nodes[e["source"]]["label"]), "hospital": nodes[e["target"]]["label"],
                                 "amount": round(e["amount"], 2), "max_risk": e["max_risk"]})
        findings.sort(key=lambda f: -f["max_risk"])
        for e in edges:
            e["amount"], e["max_risk"] = round(e["amount"], 2), round(e["max_risk"], 4)
        for n in nodes.values():
            n["amount"], n["max_risk"] = round(n["amount"], 2), round(n["max_risk"], 4)
        return {"threshold": low, "high": float(route.get("high_min", 0.7)), "claims": len(rows),
                "nodes": list(nodes.values()), "edges": edges, "findings": findings}
    return respond(200, _cached("graph", 3, build))
