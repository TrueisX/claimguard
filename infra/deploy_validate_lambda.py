
import io
import zipfile

import boto3

REGION, NAME = "us-east-1", "validate-claim"
account = boto3.client("sts").get_caller_identity()["Account"]
machine = f"arn:aws:states:{REGION}:{account}:stateMachine:claimguard-triage"

buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
    z.write("lambdas/validate_claim/handler.py", "handler.py")

lam = boto3.client("lambda", region_name=REGION)
lam.update_function_code(FunctionName=NAME, ZipFile=buf.getvalue())
lam.get_waiter("function_updated_v2").wait(FunctionName=NAME)
lam.update_function_configuration(
    FunctionName=NAME, Timeout=15,
    Environment={"Variables": {"BUCKET": "claimguard-trush-2510", "TABLE": "Claims",
                               "STATE_MACHINE_ARN": machine}})
lam.get_waiter("function_updated_v2").wait(FunctionName=NAME)
print("validate-claim updated and wired to the workflow")