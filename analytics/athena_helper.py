"""Small helpers around Athena: run a query, wait for it, return the rows."""
import time

import boto3
from botocore.exceptions import ClientError

REGION = "us-east-1"


class AthenaError(Exception):
    pass


def pick_workgroup(athena, bucket, name="claimguard"):
    """Use our own workgroup (with a 1 GB scan cap per query). Fall back to 'primary' if the lab forbids it."""
    try:
        athena.get_work_group(WorkGroup=name)
        return name
    except ClientError:
        pass
    try:
        athena.create_work_group(
            Name=name, Description="ClaimGuard analytics",
            Configuration={"ResultConfiguration": {"OutputLocation": f"s3://{bucket}/athena-results/"},
                           "EnforceWorkGroupConfiguration": True,
                           "BytesScannedCutoffPerQuery": 1_000_000_000})
        return name
    except ClientError as e:
        print(f"  note: could not create workgroup '{name}' ({e.response['Error']['Code']}); using 'primary'")
        return "primary"


def run_query(athena, sql, bucket, workgroup, database="claimguard", timeout=90):
    """Returns (header, rows). Raises AthenaError with Athena's own reason if the query fails."""
    args = dict(QueryString=sql, WorkGroup=workgroup,
                ResultConfiguration={"OutputLocation": f"s3://{bucket}/athena-results/"})
    if database:
        args["QueryExecutionContext"] = {"Database": database}
    start = athena.start_query_execution(**args)
    qid, waited = start["QueryExecutionId"], 0.0
    while True:
        status = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]
        state = status["State"]
        if state == "SUCCEEDED":
            break
        if state in ("FAILED", "CANCELLED"):
            raise AthenaError(status.get("StateChangeReason", state))
        if waited > timeout:
            raise AthenaError(f"query still {state} after {timeout} s")
        time.sleep(0.8)
        waited += 0.8
    return parse_results(athena, qid)


def parse_results(athena, qid):
    rows = []
    for page in athena.get_paginator("get_query_results").paginate(QueryExecutionId=qid):
        for r in page["ResultSet"]["Rows"]:
            rows.append([c.get("VarCharValue") for c in r["Data"]])
    if not rows:
        return [], []
    return rows[0], rows[1:]


def show(header, rows, limit=14):
    if not header:
        print("  (no columns)")
        return
    shown = rows[:limit]
    cells = [[("" if v is None else str(v)) for v in r] for r in [header] + shown]
    widths = [max(len(r[i]) for r in cells) for i in range(len(header))]
    for n, r in enumerate(cells):
        print("  " + "  ".join(v.ljust(widths[i]) for i, v in enumerate(r)))
        if n == 0:
            print("  " + "  ".join("-" * w for w in widths))
    if not rows:
        print("  (no rows)")
    elif len(rows) > limit:
        print(f"  ... {len(rows) - limit} more rows")


def client():
    return boto3.client("athena", region_name=REGION)
