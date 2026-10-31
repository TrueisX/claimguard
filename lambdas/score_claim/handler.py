
import os
from datetime import datetime, timezone
from decimal import Decimal

import boto3

from dynamo_history import fetch_history
from online_features import online_features
from scorer import FraudScorer

BUCKET = os.environ["BUCKET"]
TABLE = os.environ["TABLE"]
MODEL_KEY = os.environ.get("MODEL_KEY", "models/model.json")
LOW_MAX = float(os.environ.get("LOW_MAX", "0.3"))     # below this: LOW
HIGH_MIN = float(os.environ.get("HIGH_MIN", "0.7"))   # at/above this: HIGH

table = boto3.resource("dynamodb").Table(TABLE)
s3 = boto3.client("s3")
_scorer = None          # cached between invocations (warm starts skip the download)


def get_scorer():
    global _scorer
    if _scorer is None:
        s3.download_file(BUCKET, MODEL_KEY, "/tmp/model.json")
        _scorer = FraudScorer("/tmp/model.json")
    return _scorer


def band(score):
    if score < LOW_MAX:
        return "LOW"
    return "MEDIUM" if score < HIGH_MIN else "HIGH"


def lambda_handler(event, context):
    claim_id = event["claim_id"]
    claim = table.get_item(Key={"claim_id": claim_id}).get("Item")
    if claim is None:
        raise ValueError(f"claim not found: {claim_id}")

    prov_hist, pat_hist = fetch_history(table, claim["provider_id"], claim["patient_id"])
    feats = online_features(claim, prov_hist, pat_hist)

    score = get_scorer().score(feats)
    risk_band = band(score)

    table.update_item(
        Key={"claim_id": claim_id},
        UpdateExpression=("SET risk_score = :s, risk_band = :b, scored_at = :t, "
                          "#st = :st, features = :f"),
        ExpressionAttributeNames={"#st": "status"},    # "status" is a reserved word
        ExpressionAttributeValues={
            ":s": Decimal(str(round(score, 6))),
            ":b": risk_band,
            ":t": datetime.now(timezone.utc).isoformat(),
            ":st": "SCORED",
            ":f": {k: Decimal(str(round(float(v), 6))) for k, v in feats.items()},
        },
    )
    return {"claim_id": claim_id, "risk_score": round(score, 4),
            "risk_band": risk_band, "features": feats}