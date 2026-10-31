
import joblib
import pandas as pd

from features import build_features, FEATURES
from scorer import FraudScorer

df = build_features(pd.read_csv("data/claims.csv"))
sample = df.sample(5000, random_state=1)

model = joblib.load("ml/model.joblib")
expected = model.predict_proba(sample[FEATURES])[:, 1]

scorer = FraudScorer("ml/model.json")
got = [scorer.score(row) for row in sample[FEATURES].to_dict("records")]

diffs = abs(expected - pd.Series(got, index=sample.index).to_numpy())
bad = diffs > 1e-4
print(f"Checked {len(sample):,} claims")
print(f"Max difference : {diffs.max():.2e}")
print(f"Mean difference: {diffs.mean():.2e}")
print(f"Claims off by more than 1e-4: {int(bad.sum())}")

if bad.any():
    out = sample[bad][FEATURES + ["fraud_type"]].copy()
    out["xgboost"] = expected[bad]
    out["ours"] = [g for g, b in zip(got, bad) if b]
    print(out.round(4).to_string())

assert diffs.max() < 1e-3, "Scorer does NOT match XGBoost!"
print("PASS: pure-Python scorer matches XGBoost")