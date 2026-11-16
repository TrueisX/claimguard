"""Step 8c: train ClaimGuard's XGBoost model as a SageMaker training job.

Run from the project root:  python sagemaker_jobs/run_training.py
It uploads the training data and code, runs the job, waits, prints the metrics and checks them.
"""
import io
import json
import os
import re
import sys
import tarfile
import time
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "analytics"))
import lake  # noqa: E402  (also puts ml/ on the path)
from features import FEATURES  # noqa: E402

REGION = "us-east-1"
BUCKET = lake.BUCKET
IMAGE = "683313688378.dkr.ecr.us-east-1.amazonaws.com/sagemaker-xgboost:1.7-1"
# Cheapest first. The lab only allows some instance types, so we try the next one if one is refused.
INSTANCE_TYPES = ["ml.m5.large", "ml.c5.xlarge", "ml.m5.xlarge", "ml.t3.medium"]
LOG_GROUP = "/aws/sagemaker/TrainingJobs"
METRICS = ["validation_pr_auc", "validation_roc_auc", "validation_fraud_caught", "validation_legit_delayed"]
STATE_FILE = os.path.join(HERE, "last_training.json")
failures = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name} {detail}".rstrip())
    if not ok:
        failures.append(name)


def upload_data(s3, df, stamp):
    prefix = f"sagemaker/data/{stamp}"
    uris = {}
    for channel, split in (("train", "train"), ("validation", "test")):
        part = df[df["split"] == split][["is_fraud"] + FEATURES]
        key = f"{prefix}/{channel}/{channel}.csv"
        s3.put_object(Bucket=BUCKET, Key=key, Body=part.to_csv(index=False).encode(), ContentType="text/csv")
        uris[channel] = f"s3://{BUCKET}/{prefix}/{channel}/"
        print(f"  {channel}: {len(part):,} rows ({int(part['is_fraud'].sum()):,} fraud)")
    return uris


def package_source(s3, stamp):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(os.path.join(HERE, "train_sm.py"), arcname="train_sm.py")
    key = f"sagemaker/code/{stamp}/sourcedir.tar.gz"
    s3.put_object(Bucket=BUCKET, Key=key, Body=buf.getvalue())
    return f"s3://{BUCKET}/{key}"


def job_request(name, instance, role, data_uris, source_uri):
    return {
        "TrainingJobName": name,
        "RoleArn": role,
        "AlgorithmSpecification": {
            "TrainingImage": IMAGE, "TrainingInputMode": "File"},   # no MetricDefinitions: SageMaker forbids them here
        "InputDataConfig": [
            {"ChannelName": ch, "ContentType": "text/csv", "CompressionType": "None",
             "DataSource": {"S3DataSource": {"S3DataType": "S3Prefix", "S3Uri": uri,
                                             "S3DataDistributionType": "FullyReplicated"}}}
            for ch, uri in data_uris.items()],
        "OutputDataConfig": {"S3OutputPath": f"s3://{BUCKET}/sagemaker/output/"},
        "ResourceConfig": {"InstanceType": instance, "InstanceCount": 1, "VolumeSizeInGB": 5},
        "StoppingCondition": {"MaxRuntimeInSeconds": 1200},
        "HyperParameters": {
            # tells the container to run our script (script mode)
            "sagemaker_program": json.dumps("train_sm.py"),
            "sagemaker_submit_directory": json.dumps(source_uri),
            "sagemaker_region": json.dumps(REGION),
            "sagemaker_job_name": json.dumps(name),
            "sagemaker_container_log_level": "20",
            # our own settings, same values as ml/train.py
            "n_estimators": "300", "max_depth": "5", "learning_rate": "0.08",
            "subsample": "0.9", "colsample_bytree": "0.9", "seed": "42"},
        "Tags": [{"Key": "project", "Value": "claimguard"}],
    }


def start_job(sm, name, role, data_uris, source_uri):
    refused = []
    for instance in INSTANCE_TYPES:
        try:
            sm.create_training_job(**job_request(name, instance, role, data_uris, source_uri))
            return instance
        except ClientError as e:
            err = e.response["Error"]
            refused.append(f"{instance}: {err.get('Code')}: {err.get('Message')}")
            print(f"  {instance} was refused ({err.get('Code')}), trying the next type")
            if "ResourceInUse" in str(err.get("Code")):
                break
    raise SystemExit("SageMaker refused every instance type:\n  " + "\n  ".join(refused))


def wait(sm, name, poll=15, limit=1500):
    last, waited = None, 0
    while True:
        d = sm.describe_training_job(TrainingJobName=name)
        state = (d["TrainingJobStatus"], d.get("SecondaryStatus"))
        if state != last:
            print(f"  [{int(waited):>4} s] {state[0]} / {state[1]}", flush=True)
            last = state
        if d["TrainingJobStatus"] in ("Completed", "Failed", "Stopped"):
            return d
        if waited > limit:
            raise SystemExit("The job is taking too long. Check it in the SageMaker console.")
        time.sleep(poll)
        waited += poll


def read_log_lines(logs, name):
    """All log lines of the job, oldest first. Returns [] if the log cannot be read."""
    lines = []
    try:
        streams = logs.describe_log_streams(logGroupName=LOG_GROUP, logStreamNamePrefix=name)["logStreams"]
        for s in streams:
            kwargs, token = dict(logGroupName=LOG_GROUP, logStreamName=s["logStreamName"], startFromHead=True), None
            while True:
                if token:
                    kwargs["nextToken"] = token
                r = logs.get_log_events(**kwargs)
                lines += [e["message"].rstrip() for e in r["events"]]
                nxt = r.get("nextForwardToken")
                if not r["events"] or nxt == token:
                    break
                token = nxt
    except ClientError as e:
        print(f"  (could not read logs: {e.response['Error'].get('Code')})")
    return lines


def parse_metrics(lines):
    found = {}
    for line in lines:
        for key, value in re.findall(r"(validation_[a-z_]+)=([0-9.]+);", line):
            found[key] = float(value)
    return found


def metrics_from_logs(logs, name, attempts=8, wait=10):
    """The metric lines our script printed. The log can lag a little behind the job, so retry."""
    lines = []
    for _ in range(attempts):
        lines = read_log_lines(logs, name)
        found = parse_metrics(lines)
        if all(k in found for k in METRICS):
            return found, lines
        time.sleep(wait)
    return parse_metrics(lines), lines


def tail_logs(logs, name, lines=40):
    for line in read_log_lines(logs, name)[-lines:]:
        print("  | " + line)


def final_metrics(d):
    return {m["MetricName"]: m["Value"] for m in d.get("FinalMetricDataList", [])}


def run(s3, sm, logs, df, role, poll=15, log_wait=10):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    name = f"claimguard-xgb-{stamp}"

    print("1) Training data and code to S3")
    data_uris = upload_data(s3, df, stamp)
    source_uri = package_source(s3, stamp)

    print(f"\n2) Starting training job {name}")
    instance = start_job(sm, name, role, data_uris, source_uri)
    print(f"  running on {instance} (one machine, stops by itself when finished)")

    print("\n3) Waiting for the job")
    d = wait(sm, name, poll=poll)
    if d["TrainingJobStatus"] != "Completed":
        print(f"\n  The job ended as {d['TrainingJobStatus']}: {d.get('FailureReason', 'no reason given')}")
        print("  Last lines of the job's log:")
        tail_logs(logs, name)
        raise SystemExit(2)

    print("\n4) Result")
    m = final_metrics(d)
    if not all(k in m for k in METRICS):
        m, _ = metrics_from_logs(logs, name, wait=log_wait)       # read them from the job's log
    secs = d.get("BillableTimeInSeconds", 0)
    print(f"  billed for {secs} s on {instance} (a few cents at most)")
    artifact = d["ModelArtifacts"]["S3ModelArtifacts"]
    print(f"  model: {artifact}")
    for k in METRICS:
        print(f"  {k:<26} {m.get(k, 'missing')}")

    print("\n5) Checks")
    check("training job completed", d["TrainingJobStatus"] == "Completed")
    check("PR-AUC is high", m.get("validation_pr_auc", 0) >= 0.9, f"(got {m.get('validation_pr_auc')}, want 0.9 or more)")
    check("fraud caught is at least 85%", m.get("validation_fraud_caught", 0) >= 0.85,
          f"(got {m.get('validation_fraud_caught')})")
    check("legit claims delayed is at most 2%", m.get("validation_legit_delayed", 1) <= 0.02,
          f"(got {m.get('validation_legit_delayed')})")
    bucket, key = artifact[5:].split("/", 1)
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as tar:
        check("model file is in the artifact", "xgboost-model" in tar.getnames(), f"(contains {tar.getnames()})")

    with open(STATE_FILE, "w") as f:
        json.dump({"job_name": name, "model_artifact": artifact, "instance_type": instance, "metrics": m}, f, indent=2)
    print(f"\n  saved {os.path.relpath(STATE_FILE)}")
    return name


if __name__ == "__main__":
    csv_path = os.path.join(os.getcwd(), "data", "claims.csv")
    if not os.path.exists(csv_path):
        sys.exit("data/claims.csv not found. Run:  python ml/generate_claims.py")
    try:
        account = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
        run(boto3.client("s3", region_name=REGION), boto3.client("sagemaker", region_name=REGION),
            boto3.client("logs", region_name=REGION), lake.history_frame(csv_path),
            f"arn:aws:iam::{account}:role/LabRole")
    except ClientError as e:
        err = e.response["Error"]
        print(f"\nAWS refused a call: {err.get('Code')}: {err.get('Message')}")
        print("Send me this line and I will adjust the step.")
        sys.exit(2)
    print("\n" + ("ALL CHECKS PASSED" if not failures else f"FAILED: {failures}"))
    sys.exit(1 if failures else 0)
