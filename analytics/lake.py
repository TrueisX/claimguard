"""Build the two datasets Athena reads: labelled history (CSV) and live claims (JSON lines)."""
import json
import os
import sys
from decimal import Decimal

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ml"))
from features import FEATURES, build_features  # noqa: E402

BUCKET = os.environ.get("BUCKET", "claimguard-trush-2510")
HISTORY_KEY = "analytics/history_labeled/claims_features.csv"
LIVE_KEY = "analytics/live_claims/live_claims.jsonl"

HISTORY_COLUMNS = (["claim_id", "provider_id", "patient_id", "claim_date", "diagnosis",
                    "procedure_code", "amount", "is_fraud", "fraud_type"] + FEATURES + ["split"])
LIVE_FIELDS = ["claim_id", "provider_id", "patient_id", "claim_date", "diagnosis", "procedure_code",
               "amount", "status", "risk_score", "risk_band", "received_at", "scored_at",
               "reviewer_decision", "reviewer", "reviewed_at", "reviewer_label"]


def history_frame(csv_path):
    """Claims with their 11 model inputs and a train/test split (same time cut as ml/train.py)."""
    df = build_features(pd.read_csv(csv_path))
    cut = df["day"].quantile(0.7)
    df["split"] = ["train" if d <= cut else "test" for d in df["day"]]
    return df[HISTORY_COLUMNS]


def _plain(value):
    return float(value) if isinstance(value, Decimal) else value


def live_lines(items):
    """One JSON object per line. Free-text notes and the nested model inputs are left out on purpose."""
    return "\n".join(json.dumps({k: _plain(it[k]) for k in LIVE_FIELDS if k in it})
                     for it in items)


def scan_live_claims(table):
    """Every claim that is not just training history: new, scored, queued or decided."""
    items, kwargs = [], dict(
        FilterExpression="#st <> :h",
        ProjectionExpression=("claim_id, provider_id, patient_id, claim_date, diagnosis, procedure_code, "
                              "amount, #st, risk_score, risk_band, received_at, scored_at, "
                              "reviewer_decision, reviewer, reviewed_at, reviewer_label"),
        ExpressionAttributeNames={"#st": "status"},
        ExpressionAttributeValues={":h": "HISTORY"})
    while True:
        resp = table.scan(**kwargs)
        items += resp["Items"]
        if "LastEvaluatedKey" not in resp:
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
