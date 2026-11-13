"""Write the container's secret settings to an env file. Nothing is printed.

Takes the current AWS keys from your session and the token secret and admin passcode from the
review-api Lambda, so people signed in on the cloud site and the container share the same rules.
"""
import os
import sys

import boto3

out = sys.argv[1]
creds = boto3.Session(region_name="us-east-1").get_credentials().get_frozen_credentials()
cfg = boto3.client("lambda", region_name="us-east-1").get_function_configuration(
    FunctionName="review-api")["Environment"]["Variables"]
values = {"AWS_ACCESS_KEY_ID": creds.access_key, "AWS_SECRET_ACCESS_KEY": creds.secret_key,
          "TOKEN_SECRET": cfg["TOKEN_SECRET"], "ADMIN_PASSCODE": cfg["ADMIN_PASSCODE"]}
if creds.token:
    values["AWS_SESSION_TOKEN"] = creds.token
with open(out, "w") as f:
    for key, value in values.items():
        f.write(f"{key}={value}\n")
try:
    os.chmod(out, 0o600)
except OSError:
    pass
