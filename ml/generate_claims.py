
import uuid
from datetime import date, timedelta

import numpy as np
import pandas as pd

rng = np.random.default_rng(42)          # fixed seed = reproducible data

N_NORMAL = 48_000
START = date(2025, 11, 1)
DAYS = 365

PROVIDERS = [f"P{i:04d}" for i in range(300)]
PATIENTS = [f"PT{i:05d}" for i in range(8000)]
RING_PROVIDERS = PROVIDERS[0:6]          # providers in the fraud ring
PHANTOM_PROVIDERS = PROVIDERS[6:14]      # providers billing phantom patients
RING_PATIENTS = PATIENTS[0:30]

# procedure code -> typical cost in INR
COST = {"CONS01": 800, "XRAY02": 1500, "BLOOD03": 2000,
        "MRI04": 9000, "SURG05": 35000, "SURG06": 150000}

# diagnosis -> (clinically sensible procedures, probabilities)
DIAGNOSES = {
    "fever":        (["CONS01", "BLOOD03"],            [0.7, 0.3]),
    "fracture":     (["XRAY02", "CONS01", "SURG05"],   [0.5, 0.3, 0.2]),
    "back_pain":    (["CONS01", "XRAY02", "MRI04"],    [0.5, 0.3, 0.2]),
    "appendicitis": (["SURG05", "BLOOD03"],            [0.7, 0.3]),
    "cardiac":      (["CONS01", "BLOOD03", "SURG06"],  [0.5, 0.4, 0.1]),
}
DX_NAMES = list(DIAGNOSES)
DX_PROBS = [0.40, 0.20, 0.20, 0.10, 0.10]

# each provider has its own price level (some are just pricier)
PROVIDER_MULT = {p: rng.lognormal(0, 0.15) for p in PROVIDERS}


def make_row(provider, patient, day, dx, proc, amount, fraud_type="none"):
    return {
        "claim_id": str(uuid.uuid4()),
        "provider_id": provider,
        "patient_id": patient,
        "claim_date": (START + timedelta(days=int(day))).isoformat(),
        "diagnosis": dx,
        "procedure_code": proc,
        "amount": round(float(amount), 2),
        "is_fraud": 0 if fraud_type == "none" else 1,
        "fraud_type": fraud_type,
    }


rows = []

# 1) Normal claims: sensible procedure for the diagnosis, normal price
for _ in range(N_NORMAL):
    prov = rng.choice(PROVIDERS)
    dx = rng.choice(DX_NAMES, p=DX_PROBS)
    procs, probs = DIAGNOSES[dx]
    proc = rng.choice(procs, p=probs)
    amount = COST[proc] * PROVIDER_MULT[prov] * rng.lognormal(0, 0.2)
    rows.append(make_row(prov, rng.choice(PATIENTS), rng.integers(DAYS),
                         dx, proc, amount))

normal_df = pd.DataFrame(rows)

# 2) Upcoding: expensive procedure that doesn't fit the diagnosis
for _ in range(600):
    proc = rng.choice(["MRI04", "SURG05", "SURG06"])
    dx = rng.choice(["fever", "back_pain"])
    prov = rng.choice(PROVIDERS)
    amount = COST[proc] * PROVIDER_MULT[prov] * rng.uniform(1.5, 3)
    rows.append(make_row(prov, rng.choice(PATIENTS), rng.integers(DAYS),
                         dx, proc, amount, "upcoding"))

# 3) Duplicates: copy real claims, resubmit within 0-3 days
for i in rng.choice(len(normal_df), 500, replace=False):
    orig = normal_df.iloc[i]
    day = (date.fromisoformat(orig.claim_date) - START).days + rng.integers(0, 4)
    rows.append(make_row(orig.provider_id, orig.patient_id, day,
                         orig.diagnosis, orig.procedure_code,
                         orig.amount, "duplicate"))

# 4) Phantom billing: burst of one-time patients over ~3 weeks
for prov in PHANTOM_PROVIDERS:
    burst_start = rng.integers(DAYS - 25)
    for _ in range(38):
        proc = rng.choice(["CONS01", "BLOOD03"])
        amount = COST[proc] * PROVIDER_MULT[prov] * rng.uniform(1.5, 2.2)
        rows.append(make_row(prov, rng.choice(PATIENTS),
                             burst_start + rng.integers(21),
                             "fever", proc, amount, "phantom"))

# 5) Ring: same providers and patients billing each other, inflated amounts
for _ in range(400):
    prov = rng.choice(RING_PROVIDERS)
    proc = rng.choice(["MRI04", "SURG05"])
    amount = COST[proc] * PROVIDER_MULT[prov] * rng.uniform(2, 4)
    rows.append(make_row(prov, rng.choice(RING_PATIENTS),
                         rng.integers(DAYS - 60) + rng.integers(60),
                         "back_pain", proc, amount, "ring"))

# 6) Unbundling: one visit split into 3-5 claims on the same day
for _ in range(100):
    prov, pat = rng.choice(PROVIDERS), rng.choice(PATIENTS)
    day = rng.integers(DAYS)
    for _ in range(rng.integers(3, 6)):
        proc = rng.choice(["BLOOD03", "XRAY02"])
        amount = COST[proc] * PROVIDER_MULT[prov] * rng.uniform(0.4, 0.7)
        rows.append(make_row(prov, pat, day, "fever", proc, amount,
                             "unbundling"))

df = (pd.DataFrame(rows)
        .sample(frac=1, random_state=42)
        .sort_values("claim_date")
        .reset_index(drop=True))
df.to_csv("data/claims.csv", index=False)

print(f"Total claims: {len(df):,}")
print(f"Fraud rate:   {df.is_fraud.mean():.2%}")
print(df.fraud_type.value_counts())