
import json
import os

import joblib

from features import FEATURES

model = joblib.load("ml/model.joblib")
booster = model.get_booster()

# XGBoost starts every prediction from a base score; we must replicate it.
cfg = json.loads(booster.save_config())
raw = str(cfg["learner"]["learner_model_param"]["base_score"])
base_score = float(raw.strip("[]").split(",")[0])

trees = [json.loads(t) for t in booster.get_dump(dump_format="json")]

with open("ml/model.json", "w") as f:
    json.dump({"features": FEATURES, "base_score": base_score,
               "trees": trees}, f)

print(f"Exported {len(trees)} trees, base_score={base_score:.4f}")
print(f"ml/model.json is {os.path.getsize('ml/model.json') / 1024:.0f} KB")