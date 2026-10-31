
import io
import zipfile

import boto3

REGION, NAME = "us-east-1", "score-claim"
FILES = {                       # name inside the zip -> file on disk
    "handler.py": "lambdas/score_claim/handler.py",
    "online_features.py": "ml/online_features.py",
    "dynamo_history.py": "ml/dynamo_history.py",
    "scorer.py": "ml/scorer.py",
}
ENV = {"Variables": {"BUCKET": "claimguard-trush-2510", "TABLE": "Claims",
                     "MODEL_KEY": "models/model.json"}}

buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
    for arcname, path in FILES.items():
        z.write(path, arcname)
code = buf.getvalue()
print(f"Built zip: {len(code) / 1024:.1f} KB")

lam = boto3.client("lambda", region_name=REGION)
account = boto3.client("sts").get_caller_identity()["Account"]
role = f"arn:aws:iam::{account}:role/LabRole"

try:
    lam.get_function(FunctionName=NAME)
    exists = True
except lam.exceptions.ResourceNotFoundException:
    exists = False

if exists:
    lam.update_function_code(FunctionName=NAME, ZipFile=code)
    lam.get_waiter("function_updated_v2").wait(FunctionName=NAME)
    lam.update_function_configuration(FunctionName=NAME, Timeout=30,
                                      MemorySize=256, Environment=ENV)
    print("Updated existing function")
else:
    lam.create_function(FunctionName=NAME, Runtime="python3.12", Role=role,
                        Handler="handler.lambda_handler", Code={"ZipFile": code},
                        Timeout=30, MemorySize=256, Environment=ENV)
    print("Created new function")

lam.get_waiter("function_updated_v2").wait(FunctionName=NAME)
print("score-claim is ready")