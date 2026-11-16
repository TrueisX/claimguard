"""Runs INSIDE the SageMaker XGBoost container (python 3.8). Trains the ClaimGuard fraud model.

SageMaker puts the data in /opt/ml/input/data/<channel>/ and collects whatever we write to
/opt/ml/model/. The metric lines printed below are picked up by the job's metric definitions.
"""
import argparse
import glob
import os
import pickle

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import average_precision_score, roc_auc_score

# Same names and order as ml/features.py
FEATURES = [
    "expected_cost", "amount_ratio", "procedure_mismatch", "provider_claims_30d",
    "patient_claims_30d", "provider_avg_amount", "provider_patient_count",
    "days_since_previous_claim", "duplicate_candidate",
    "same_day_provider_claim_count", "same_day_patient_provider_count",
]


def read_channel(name):
    folder = os.environ.get("SM_CHANNEL_" + name.upper(), "/opt/ml/input/data/" + name)
    files = sorted(glob.glob(os.path.join(folder, "*.csv")))
    if not files:
        raise SystemExit("no CSV files in channel '%s' (%s)" % (name, folder))
    return pd.concat([pd.read_csv(f) for f in files], ignore_index=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n_estimators", type=int, default=300)
    ap.add_argument("--max_depth", type=int, default=5)
    ap.add_argument("--learning_rate", type=float, default=0.08)
    ap.add_argument("--subsample", type=float, default=0.9)
    ap.add_argument("--colsample_bytree", type=float, default=0.9)
    ap.add_argument("--seed", type=int, default=42)
    args, _ = ap.parse_known_args()      # SageMaker also passes its own settings; ignore them

    train, valid = read_channel("train"), read_channel("validation")
    print("train rows: %d, validation rows: %d" % (len(train), len(valid)), flush=True)

    # Plain numpy arrays: the model carries no feature names, so the endpoint can be sent bare CSV rows.
    X_tr, y_tr = train[FEATURES].to_numpy(dtype="float64"), train["is_fraud"].to_numpy()
    X_va, y_va = valid[FEATURES].to_numpy(dtype="float64"), valid["is_fraud"].to_numpy()

    model = xgb.XGBClassifier(
        n_estimators=args.n_estimators, max_depth=args.max_depth, learning_rate=args.learning_rate,
        subsample=args.subsample, colsample_bytree=args.colsample_bytree,
        eval_metric="aucpr", random_state=args.seed)
    model.fit(X_tr, y_tr)

    p = model.predict_proba(X_va)[:, 1]
    flagged = p >= 0.3                       # MEDIUM and HIGH go to a person
    print("validation_pr_auc=%.4f;" % average_precision_score(y_va, p), flush=True)
    print("validation_roc_auc=%.4f;" % roc_auc_score(y_va, p), flush=True)
    print("validation_fraud_caught=%.4f;" % flagged[y_va == 1].mean(), flush=True)
    print("validation_legit_delayed=%.4f;" % flagged[y_va == 0].mean(), flush=True)

    out = os.path.join(os.environ.get("SM_MODEL_DIR", "/opt/ml/model"), "xgboost-model")
    with open(out, "wb") as f:
        pickle.dump(model.get_booster(), f)   # the container's default loader reads this file
    print("saved " + out, flush=True)


if __name__ == "__main__":
    main()
