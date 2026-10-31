
import boto3

from features import FEATURES
from online_features import online_features
from dynamo_history import fetch_history
from test_online_features import load_records

df, records = load_records()
table = boto3.resource("dynamodb", region_name="us-east-1").Table("Claims")

sample = df.sample(25, random_state=7).index
bad = 0
for i in sample:
    c = records[i]
    prov, pat = fetch_history(table, c["provider_id"], c["patient_id"])
    got = online_features(c, prov, pat)
    for f in FEATURES:
        want = float(df.at[i, f])
        if abs(got[f] - want) > 1e-6 * max(1.0, abs(want)):
            bad += 1
            print(f"MISMATCH {c['claim_id'][:8]} {f}: training={want} online={got[f]}")

print(f"Checked {len(sample)} claims against live DynamoDB: {bad} mismatches")
assert bad == 0, "DynamoDB path disagrees with training!"
print("PASS: live DynamoDB features match training features")