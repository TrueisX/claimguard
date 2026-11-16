"""Step 8d: deploy the SageMaker-trained model as an endpoint and test it.

Run from the project root:  python sagemaker_jobs/deploy_endpoint.py
Default is a SERVERLESS endpoint: you pay only per request, nothing while idle.
Use  --realtime  for a small always-on server (about $1.35 per day) if serverless is refused.
Remove everything afterwards with:  python sagemaker_jobs/teardown_endpoint.py
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_training as rt  # noqa: E402  (also loads analytics/lake.py and ml/features.py)
from features import FEATURES  # noqa: E402
from scorer import FraudScorer  # noqa: E402

REGION, IMAGE = rt.REGION, rt.IMAGE
ENDPOINT_FILE = os.path.join(HERE, "endpoint.json")
LOW_MAX, HIGH_MIN = 0.3, 0.7
failures = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name} {detail}".rstrip())
    if not ok:
        failures.append(name)


def band(score):
    return "LOW" if score < LOW_MAX else ("MEDIUM" if score < HIGH_MIN else "HIGH")


def model_request(name, artifact, role):
    return {"ModelName": name, "ExecutionRoleArn": role,
            "PrimaryContainer": {"Image": IMAGE, "ModelDataUrl": artifact},
            "Tags": [{"Key": "project", "Value": "claimguard"}]}


def config_request(name, model_name, realtime):
    if realtime:
        variant = {"VariantName": "AllTraffic", "ModelName": model_name,
                   "InstanceType": "ml.t2.medium", "InitialInstanceCount": 1}
    else:
        variant = {"VariantName": "AllTraffic", "ModelName": model_name,
                   "ServerlessConfig": {"MemorySizeInMB": 2048, "MaxConcurrency": 2}}
    return {"EndpointConfigName": name, "ProductionVariants": [variant],
            "Tags": [{"Key": "project", "Value": "claimguard"}]}


def deploy(sm, artifact, role, stamp, realtime):
    names = {"model": f"claimguard-model-{stamp}", "config": f"claimguard-config-{stamp}",
             "endpoint": f"claimguard-fraud-{stamp}"}
    sm.create_model(**model_request(names["model"], artifact, role))
    print(f"  model {names['model']} created")
    sm.create_endpoint_config(**config_request(names["config"], names["model"], realtime))
    print(f"  endpoint config created ({'always-on ml.t2.medium' if realtime else 'serverless, 2 GB, 2 at a time'})")
    sm.create_endpoint(EndpointName=names["endpoint"], EndpointConfigName=names["config"],
                       Tags=[{"Key": "project", "Value": "claimguard"}])
    print(f"  endpoint {names['endpoint']} is starting")
    return names


def wait_in_service(sm, endpoint, poll=15, limit=900):
    last, waited = None, 0
    while True:
        d = sm.describe_endpoint(EndpointName=endpoint)
        if d["EndpointStatus"] != last:
            print(f"  [{int(waited):>4} s] {d['EndpointStatus']}", flush=True)
            last = d["EndpointStatus"]
        if d["EndpointStatus"] == "InService":
            return d
        if d["EndpointStatus"] == "Failed":
            raise SystemExit(f"Endpoint failed: {d.get('FailureReason', 'no reason given')}")
        if waited > limit:
            raise SystemExit("The endpoint is taking too long. Check it in the SageMaker console.")
        time.sleep(poll)
        waited += poll


def sample_rows(df, per_class=20, seed=7):
    test = df[df["split"] == "test"]
    return (test[test["is_fraud"] == 1].sample(per_class, random_state=seed),
            test[test["is_fraud"] == 0].sample(per_class, random_state=seed))


def invoke(runtime, endpoint, row):
    body = ",".join(repr(float(row[f])) for f in FEATURES)
    t = time.time()
    resp = runtime.invoke_endpoint(EndpointName=endpoint, ContentType="text/csv", Body=body)
    return float(resp["Body"].read().decode().strip().split(",")[0]), time.time() - t


def test_endpoint(runtime, endpoint, df, local):
    fraud, legit = sample_rows(df)
    results = []                                      # (is_fraud, endpoint score, local score)
    first = None
    for frame in (fraud, legit):
        for _, row in frame.iterrows():
            score, secs = invoke(runtime, endpoint, row)
            first = first if first is not None else secs
            results.append((int(row["is_fraud"]), score, local.score({f: row[f] for f in FEATURES})))
    print(f"  first call took {first:.1f} s (a serverless endpoint wakes up on the first request)")
    flagged_fraud = sum(s >= LOW_MAX for y, s, _ in results if y == 1) / 20
    flagged_legit = sum(s >= LOW_MAX for y, s, _ in results if y == 0) / 20
    agree = sum(band(s) == band(l) for _, s, l in results) / len(results)
    diff = sum(abs(s - l) for _, s, l in results) / len(results)
    print(f"  40 test claims sent: fraud flagged {flagged_fraud:.0%}, legit flagged {flagged_legit:.0%}, "
          f"same band as the local scorer {agree:.0%}, average score difference {diff:.3f}")
    check("endpoint answers every request", len(results) == 40)
    check("most sampled fraud is flagged", flagged_fraud >= 0.8, f"(got {flagged_fraud:.0%})")
    check("few sampled legit claims are flagged", flagged_legit <= 0.1, f"(got {flagged_legit:.0%})")
    check("bands mostly match the local scorer", agree >= 0.85, f"(got {agree:.0%})")
    return results


def main(argv):
    realtime = "--realtime" in argv
    if os.path.exists(ENDPOINT_FILE):
        sys.exit("An endpoint is already recorded in sagemaker_jobs/endpoint.json. "
                 "Run python sagemaker_jobs/teardown_endpoint.py first.")
    if not os.path.exists(rt.STATE_FILE):
        sys.exit("No trained model found. Run step 8c (python sagemaker_jobs/run_training.py) first.")
    artifact = json.load(open(rt.STATE_FILE))["model_artifact"]
    csv_path = os.path.join(os.getcwd(), "data", "claims.csv")
    local = FraudScorer(os.path.join(os.getcwd(), "ml", "model.json"))
    df = rt.lake.history_frame(csv_path)
    account = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
    role = f"arn:aws:iam::{account}:role/LabRole"
    sm = boto3.client("sagemaker", region_name=REGION)
    runtime = boto3.client("sagemaker-runtime", region_name=REGION,
                           config=Config(read_timeout=70, connect_timeout=5, retries={"max_attempts": 0}))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    print("1) Creating the endpoint")
    try:
        names = deploy(sm, artifact, role, stamp, realtime)
    except ClientError as e:
        err = e.response["Error"]
        print(f"\nSageMaker refused: {err.get('Code')}: {err.get('Message')}")
        if not realtime:
            print("If serverless is not allowed in your lab, re-run with:  python sagemaker_jobs/deploy_endpoint.py --realtime")
        sys.exit(2)
    json.dump({**names, "realtime": realtime, "created": stamp}, open(ENDPOINT_FILE, "w"), indent=2)

    print("\n2) Waiting until it is in service")
    wait_in_service(sm, names["endpoint"])

    print("\n3) Testing it with real claims")
    try:
        test_endpoint(runtime, names["endpoint"], df, local)
    except ClientError as e:
        err = e.response["Error"]
        print(f"\nThe endpoint call failed: {err.get('Code')}: {err.get('Message')}")
        print("Send me this line. Remove the endpoint with: python sagemaker_jobs/teardown_endpoint.py")
        sys.exit(2)
    print(f"\n  endpoint name: {names['endpoint']}")
    if realtime:
        print("  WARNING: this endpoint costs about $1.35 per day until you run teardown_endpoint.py")
    print("  Remove it any time with: python sagemaker_jobs/teardown_endpoint.py")
    print("\n" + ("ALL CHECKS PASSED" if not failures else f"FAILED: {failures}"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
