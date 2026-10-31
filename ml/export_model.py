
import json
import os

import joblib

from features import FEATURES

model = joblib.load("ml/model.joblib")
booster = model.get_booster()
assert list(booster.feature_names) == FEATURES, "feature order mismatch"

# Native format keeps full-precision thresholds (unlike get_dump text)
booster.save_model("ml/model_full.json")
with open("ml/model_full.json") as f:
    learner = json.load(f)["learner"]

raw = str(learner["learner_model_param"]["base_score"])
base_score = float(raw.strip("[]").split(",")[0])

trees = [
    {"left": t["left_children"], "right": t["right_children"],
     "cond": t["split_conditions"], "feat": t["split_indices"],
     "dl": t["default_left"]}
    for t in learner["gradient_booster"]["model"]["trees"]
]

with open("ml/model.json", "w") as f:
    json.dump({"features": FEATURES, "base_score": base_score,
               "trees": trees}, f)
os.remove("ml/model_full.json")

print(f"Exported {len(trees)} trees, base_score={base_score!r}")
print(f"ml/model.json is {os.path.getsize('ml/model.json') / 1024:.0f} KB")