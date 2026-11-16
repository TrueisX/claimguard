"""Delete the ClaimGuard SageMaker endpoint, its config and its model. Safe to run twice.

Run from the project root:  python sagemaker_jobs/teardown_endpoint.py
"""
import json
import os
import sys

import boto3
from botocore.exceptions import ClientError

HERE = os.path.dirname(os.path.abspath(__file__))
ENDPOINT_FILE = os.path.join(HERE, "endpoint.json")


def remove(label, fn, **kwargs):
    try:
        fn(**kwargs)
        print(f"  deleted {label}")
    except ClientError as e:
        err = e.response["Error"]
        if "Could not find" in err.get("Message", "") or err.get("Code") == "ValidationException":
            print(f"  {label} was already gone")
        else:
            print(f"  could not delete {label}: {err.get('Code')}: {err.get('Message')}")
            return False
    return True


def main():
    if not os.path.exists(ENDPOINT_FILE):
        print("No endpoint is recorded, nothing to delete.")
        return 0
    names = json.load(open(ENDPOINT_FILE))
    sm = boto3.client("sagemaker", region_name="us-east-1")
    ok = remove("endpoint", sm.delete_endpoint, EndpointName=names["endpoint"])
    ok &= remove("endpoint config", sm.delete_endpoint_config, EndpointConfigName=names["config"])
    ok &= remove("model", sm.delete_model, ModelName=names["model"])
    if ok:
        os.remove(ENDPOINT_FILE)
        print("Everything is removed. Nothing is running and nothing is being billed.")
        return 0
    print("Some parts could not be removed. Send me the lines above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
