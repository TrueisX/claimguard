"""admin: platform overview, system health, account management (admin role only)."""
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

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
            return [i for i in items if not str(i["account_id"]).startswith("CONFIG#")]
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
