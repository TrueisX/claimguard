
from collections import defaultdict

import pandas as pd

from features import build_features, FEATURES

COLS = ["claim_id", "provider_id", "patient_id", "claim_date",
        "diagnosis", "procedure_code", "amount", "received_at"]


def load_records():
    """Training features + the same claims as plain dicts, with the same
    arrival order (received_at) that backfill_history.py stored."""
    df = build_features(pd.read_csv("data/claims.csv"))
    date_s = df["claim_date"].dt.strftime("%Y-%m-%d")
    k = df.groupby("claim_date").cumcount()
    df["received_at"] = [f"{d}T00:00:00.{i:06d}+00:00" for d, i in zip(date_s, k)]
    records = df.assign(claim_date=date_s)[COLS].to_dict("records")
    return df, records


if __name__ == "__main__":
    import features as F
    import online_features as O
    from scorer import FraudScorer

    # The copies of the constants must not drift apart
    assert O.COST == F.COST and O.VALID_PROCEDURES == F.VALID_PROCEDURES
    assert O.FEATURES == F.FEATURES == FraudScorer("ml/model.json").features
    print("Constants and feature order match features.py and the model")

    df, records = load_records()
    by_prov, by_pat = defaultdict(list), defaultdict(list)
    for r in records:
        by_prov[r["provider_id"]].append(r)
        by_pat[r["patient_id"]].append(r)

    sample = df.sample(5000, random_state=1).index
    bad = {f: 0 for f in FEATURES}
    examples = []
    for i in sample:
        c = records[i]
        got = O.online_features(c, by_prov[c["provider_id"]], by_pat[c["patient_id"]])
        for f in FEATURES:
            want = float(df.at[i, f])
            if abs(got[f] - want) > 1e-6 * max(1.0, abs(want)):
                bad[f] += 1
                if len(examples) < 8:
                    examples.append((c["claim_id"][:8], f, want, got[f]))

    print(f"Checked {len(sample):,} claims x {len(FEATURES)} features")
    for f in FEATURES:
        print(f"  {f:34s} mismatches: {bad[f]}")
    for e in examples:
        print("  example (claim, feature, training, online):", e)
    assert sum(bad.values()) == 0, "Online features differ from training!"
    print("PASS: online features match training features")