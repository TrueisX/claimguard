
import time
from decimal import Decimal

import boto3
import pandas as pd

table = boto3.resource("dynamodb", region_name="us-east-1").Table("Claims")

# 1) Wipe whatever is there
keys = []
resp = table.scan(ProjectionExpression="claim_id")
keys += resp["Items"]
while "LastEvaluatedKey" in resp:
    resp = table.scan(ProjectionExpression="claim_id",
                      ExclusiveStartKey=resp["LastEvaluatedKey"])
    keys += resp["Items"]
with table.batch_writer() as b:
    for k in keys:
        b.delete_item(Key={"claim_id": k["claim_id"]})
print(f"Removed {len(keys)} existing items")

# 2) Load history in the same order the training features used
df = (pd.read_csv("data/claims.csv")
        .sort_values(["claim_date", "claim_id"])
        .reset_index(drop=True))
df["k"] = df.groupby("claim_date").cumcount()   # order within a day

start = time.time()
with table.batch_writer() as b:
    for i, r in enumerate(df.itertuples(index=False), 1):
        b.put_item(Item={
            "claim_id": r.claim_id,
            "provider_id": r.provider_id,
            "patient_id": r.patient_id,
            "claim_date": r.claim_date,
            "diagnosis": r.diagnosis,
            "procedure_code": r.procedure_code,
            "amount": Decimal(str(round(float(r.amount), 2))),
            "status": "HISTORY",
            # arrival order = date, then position within the day
            "received_at": f"{r.claim_date}T00:00:00.{r.k:06d}+00:00",
        })
        if i % 5000 == 0:
            print(f"  {i:,} / {len(df):,}")

print(f"Done: {len(df):,} claims written in {time.time() - start:.0f} s")