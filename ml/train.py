
import joblib
import pandas as pd
import xgboost as xgb
from sklearn.metrics import average_precision_score, roc_auc_score

from features import build_features, FEATURES

df = build_features(pd.read_csv("data/claims.csv"))

cut = df["day"].quantile(0.7)                  # time-based split
train, test = df[df["day"] <= cut], df[df["day"] > cut]
X_tr, y_tr = train[FEATURES], train["is_fraud"]
X_te, y_te = test[FEATURES], test["is_fraud"]
print(f"Train: {len(train):,} claims | Test: {len(test):,} claims")

model = xgb.XGBClassifier(
    n_estimators=300, max_depth=5, learning_rate=0.08,
    subsample=0.9, colsample_bytree=0.9,
    eval_metric="aucpr", random_state=42,
)
model.fit(X_tr, y_tr)

p = model.predict_proba(X_te)[:, 1]
print(f"\nPR-AUC : {average_precision_score(y_te, p):.3f} "
      f"(a random guesser scores {y_te.mean():.3f})")
print(f"ROC-AUC: {roc_auc_score(y_te, p):.3f}")

# Triage view: low < 0.3, medium 0.3-0.7, high > 0.7
t = test.assign(score=p)
t["band"] = pd.cut(t["score"], [-1, 0.3, 0.7, 2], labels=["LOW", "MEDIUM", "HIGH"])
print("\nClaims per band (rows) vs. fraud / legit (columns):")
print(pd.crosstab(t["band"], t["is_fraud"]))

flagged = t["score"] >= 0.3
print(f"\nFraud caught (MEDIUM+HIGH): {flagged[t.is_fraud == 1].mean():.1%}")
print(f"Legit claims delayed      : {flagged[t.is_fraud == 0].mean():.1%}")

print("\nRecall by fraud type (score >= 0.3):")
print(t[t.is_fraud == 1].groupby("fraud_type")["score"]
        .apply(lambda s: (s >= 0.3).mean()).round(3))

print("\nFeature importance:")
print(pd.Series(model.feature_importances_, index=FEATURES)
        .sort_values(ascending=False).round(3))

joblib.dump(model, "ml/model.joblib")
print("\nSaved ml/model.joblib")