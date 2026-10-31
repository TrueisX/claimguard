
from datetime import date

# Copies of the constants in features.py (the test checks they stay identical)
COST = {
    "CONS01": 800, "XRAY02": 1500, "BLOOD03": 2000,
    "MRI04": 9000, "SURG05": 35000, "SURG06": 150000,
}
VALID_PROCEDURES = {
    "fever": ["CONS01", "BLOOD03"],
    "fracture": ["XRAY02", "CONS01", "SURG05"],
    "back_pain": ["CONS01", "XRAY02", "MRI04"],
    "appendicitis": ["SURG05", "BLOOD03"],
    "cardiac": ["CONS01", "BLOOD03", "SURG06"],
}
FEATURES = [
    "expected_cost", "amount_ratio", "procedure_mismatch",
    "provider_claims_30d", "patient_claims_30d", "provider_avg_amount",
    "provider_patient_count", "days_since_previous_claim",
    "duplicate_candidate", "same_day_provider_claim_count",
    "same_day_patient_provider_count",
]


def online_features(claim, provider_hist, patient_hist):
    """claim: provider_id, patient_id, claim_date (YYYY-MM-DD), diagnosis,
    procedure_code, amount, plus claim_id / received_at when known.
    provider_hist: stored claims for this provider (any patient)
    patient_hist:  stored claims for this patient (any provider)"""
    today = date.fromisoformat(claim["claim_date"])
    amount = float(claim["amount"])
    cid = claim.get("claim_id")
    mine = claim.get("received_at")

    def prior(hist):
        """Keep only claims that came BEFORE this one."""
        out = []
        for h in hist:
            if h["claim_id"] == cid:                 # not the claim itself
                continue
            d = date.fromisoformat(h["claim_date"])
            if d > today:                            # dated in the future
                continue
            r = h.get("received_at")
            if mine and r and r >= mine:             # arrived later
                continue
            out.append((d, h))
        return out

    prov = prior(provider_hist)
    pat = prior(patient_hist)

    expected = COST.get(claim["procedure_code"], 0)
    mismatch = int(claim["procedure_code"]
                   not in VALID_PROCEDURES.get(claim["diagnosis"], []))

    def in_window(d):                                # within the last 30 days
        return (today - d).days <= 29

    amounts = [float(h["amount"]) for _, h in prov]
    pair = [(d, h) for d, h in prov if h["patient_id"] == claim["patient_id"]]

    if pair:
        days_since = (today - max(d for d, _ in pair)).days
    else:
        days_since = 999

    same = [d for d, h in pair
            if h["diagnosis"] == claim["diagnosis"]
            and h["procedure_code"] == claim["procedure_code"]
            and abs(float(h["amount"]) - amount) < 0.005]
    duplicate = int(bool(same) and (today - max(same)).days <= 3)

    return {
        "expected_cost": expected,
        "amount_ratio": amount / expected if expected else 0,
        "procedure_mismatch": mismatch,
        "provider_claims_30d": sum(1 for d, _ in prov if in_window(d)),
        "patient_claims_30d": sum(1 for d, _ in pat if in_window(d)),
        "provider_avg_amount": sum(amounts) / len(amounts) if amounts else expected,
        "provider_patient_count": len(pair),
        "days_since_previous_claim": days_since,
        "duplicate_candidate": duplicate,
        "same_day_provider_claim_count": sum(1 for d, _ in prov if d == today),
        "same_day_patient_provider_count": sum(1 for d, _ in pair if d == today),
    }