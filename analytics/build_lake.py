"""Step 8a: build ClaimGuard's Athena data lake, run the analytics queries and check the numbers.

Run from the project root:  python analytics/build_lake.py
"""
import os
import sys

import boto3
from botocore.exceptions import ClientError

import athena_helper as ah
import lake
import queries

REGION = "us-east-1"
failures = []


def check(name, got, want):
    ok = str(got) == str(want)
    print(f"  {'PASS' if ok else 'FAIL'}  {name} (got {got}, want {want})")
    if not ok:
        failures.append(name)


def build(s3, table, athena, csv_path, bucket):
    print("1) Labelled history dataset")
    df = lake.history_frame(csv_path)
    s3.put_object(Bucket=bucket, Key=lake.HISTORY_KEY, Body=df.to_csv(index=False).encode(),
                  ContentType="text/csv")
    print(f"  uploaded {len(df):,} claims with their 11 model inputs, {int(df['is_fraud'].sum()):,} are fraud")

    print("2) Live claims from DynamoDB")
    items = lake.scan_live_claims(table)
    if items:
        s3.put_object(Bucket=bucket, Key=lake.LIVE_KEY, Body=lake.live_lines(items).encode(),
                      ContentType="application/json")
    else:
        s3.delete_object(Bucket=bucket, Key=lake.LIVE_KEY)
    print(f"  uploaded {len(items):,} live claims (notes and reviewer comments are not exported)")

    print("3) Athena database and tables")
    wg = ah.pick_workgroup(athena, bucket)
    print(f"  workgroup: {wg}")
    run = lambda sql, db="claimguard": ah.run_query(athena, sql, bucket, wg, database=db)
    run(queries.DDL_DATABASE, db=None)
    for name, ddl in (("history_labeled", queries.ddl_history(bucket)), ("live_claims", queries.ddl_live(bucket))):
        run(f"DROP TABLE IF EXISTS claimguard.{name}", db=None)
        run(ddl, db=None)
        print(f"  table claimguard.{name} ready")

    print("4) Analytics queries")
    for name, (title, sql) in queries.ALL.items():
        print(f"\n  {title}  [{name}]")
        header, rows = run(sql)
        ah.show(header, rows)

    print("\n5) Checks against the source data")
    _, rows = run("SELECT count(*), sum(is_fraud) FROM history_labeled")
    check("Athena row count matches the dataset", rows[0][0], len(df))
    check("Athena fraud count matches the dataset", rows[0][1], int(df["is_fraud"].sum()))
    _, rows = run("SELECT count(*) FROM live_claims")
    check("Athena live claim count matches DynamoDB", rows[0][0], len(items))
    return df


if __name__ == "__main__":
    root = os.getcwd()
    csv_path = os.path.join(root, "data", "claims.csv")
    if not os.path.exists(csv_path):
        sys.exit("data/claims.csv not found. Run:  python ml/generate_claims.py")
    try:
        build(boto3.client("s3", region_name=REGION),
              boto3.resource("dynamodb", region_name=REGION).Table("Claims"),
              ah.client(), csv_path, lake.BUCKET)
    except ClientError as e:
        err = e.response["Error"]
        print(f"\nAWS refused a call: {err.get('Code')}: {err.get('Message')}")
        print("Send me this line and I will adjust the step.")
        sys.exit(2)
    except ah.AthenaError as e:
        print(f"\nAthena could not run a query: {e}")
        print("Send me this line and I will adjust the step.")
        sys.exit(2)
    print("\n" + ("ALL CHECKS PASSED" if not failures else f"FAILED: {failures}"))
    sys.exit(1 if failures else 0)
