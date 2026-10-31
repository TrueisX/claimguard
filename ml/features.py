import numpy as np
import pandas as pd

# Expected procedure costs in INR
COST = {
    "CONS01": 800,
    "XRAY02": 1500,
    "BLOOD03": 2000,
    "MRI04": 9000,
    "SURG05": 35000,
    "SURG06": 150000,
}

VALID_PROCEDURES = {
    "fever": ["CONS01", "BLOOD03"],
    "fracture": ["XRAY02", "CONS01", "SURG05"],
    "back_pain": ["CONS01", "XRAY02", "MRI04"],
    "appendicitis": ["SURG05", "BLOOD03"],
    "cardiac": ["CONS01", "BLOOD03", "SURG06"],
}

FEATURES = [
    "amount",
    "day",
    "expected_cost",
    "amount_ratio",
    "procedure_mismatch",
    "provider_claim_count",
    "provider_avg_amount",
    "patient_claim_count",
    "provider_patient_count",
    "days_since_previous_claim",
    "duplicate_candidate",
    "same_day_provider_claim_count",
    "same_day_patient_provider_count",
]


def build_features(data):
    df = data.copy()

    df["claim_date"] = pd.to_datetime(df["claim_date"])

    df = df.sort_values(
        ["claim_date", "claim_id"]
    ).reset_index(drop=True)

    start_date = pd.Timestamp("2025-11-01")
    df["day"] = (df["claim_date"] - start_date).dt.days

    df["expected_cost"] = (
        df["procedure_code"].map(COST).fillna(0)
    )

    df["amount_ratio"] = (
        df["amount"] / df["expected_cost"].replace(0, np.nan)
    ).fillna(0)

    df["procedure_mismatch"] = [
        int(proc not in VALID_PROCEDURES.get(dx, []))
        for dx, proc in zip(df["diagnosis"], df["procedure_code"])
    ]

    df["provider_claim_count"] = (
        df.groupby("provider_id").cumcount()
    )

    df["provider_avg_amount"] = (
        df.groupby("provider_id")["amount"]
        .transform(lambda s: s.shift().expanding().mean())
        .fillna(df["expected_cost"])
    )

    df["patient_claim_count"] = (
        df.groupby("patient_id").cumcount()
    )

    df["provider_patient_count"] = (
        df.groupby(["provider_id", "patient_id"]).cumcount()
    )

    previous_date = (
        df.groupby(["provider_id", "patient_id"])["claim_date"]
        .shift(1)
    )

    df["days_since_previous_claim"] = (
        (df["claim_date"] - previous_date).dt.days
    )

    df["days_since_previous_claim"] = (
        df["days_since_previous_claim"].fillna(999)
    )

    duplicate_key = [
        "provider_id",
        "patient_id",
        "diagnosis",
        "procedure_code",
        "amount",
    ]

    previous_duplicate_date = (
        df.groupby(duplicate_key)["claim_date"].shift(1)
    )

    duplicate_gap = (
        df["claim_date"] - previous_duplicate_date
    ).dt.days

    df["duplicate_candidate"] = (
        duplicate_gap.between(0, 3)
    ).astype(int)

    df["same_day_provider_claim_count"] = (
        df.groupby(["provider_id", "claim_date"])["claim_id"]
        .transform("count")
    )

    df["same_day_patient_provider_count"] = (
        df.groupby([
            "provider_id",
            "patient_id",
            "claim_date",
        ])["claim_id"].transform("count")
    )

    df[FEATURES] = (
        df[FEATURES]
        .replace([np.inf, -np.inf], np.nan)
        .fillna(0)
    )

    return df