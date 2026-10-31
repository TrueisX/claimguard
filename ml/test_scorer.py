
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
print(f"Checked {len(sample):,} claims")
print(f"Max difference : {diffs.max():.2e}")
print(f"Mean difference: {diffs.mean():.2e}")
assert diffs.max() < 1e-3, "Scorer does NOT match XGBoost!"
print("PASS: pure-Python scorer matches XGBoost")