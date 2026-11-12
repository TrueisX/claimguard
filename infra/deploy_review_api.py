"""Create/update the review-api Lambda (website + APIs) and attach it to the HTTP API."""
import io
import os
import secrets
import zipfile

import boto3

REGION, NAME = "us-east-1", "review-api"
account = boto3.client("sts").get_caller_identity()["Account"]
role = f"arn:aws:iam::{account}:role/LabRole"


def build_zip():
    """handler + portal, the new website (index.html and static/), and the old page as /legacy."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write("lambdas/review_api/handler.py", "handler.py")
        z.write("lambdas/review_api/portal.py", "portal.py")
        z.write("dashboard/index.html", "index.html")
        if os.path.exists("dashboard/dashboard.html"):
            z.write("dashboard/dashboard.html", "dashboard.html")      # served at /legacy
        for name in sorted(os.listdir("dashboard/static")):
            z.write(os.path.join("dashboard/static", name), f"static/{name}")
    return buf.getvalue()


code = build_zip()
print(f"Built zip: {len(code) / 1024:.1f} KB")

lam = boto3.client("lambda", region_name=REGION)
try:
    old_env = lam.get_function_configuration(FunctionName=NAME).get(
        "Environment", {}).get("Variables", {})
    exists = True
except lam.exceptions.ResourceNotFoundException:
    old_env, exists = {}, False

# Secrets are created once and then kept, so signed-in users stay signed in.
env = {
    "TABLE": "Claims",
    "ACCOUNTS_TABLE": "ClaimGuardAccounts",
    "TOKEN_SECRET": old_env.get("TOKEN_SECRET") or secrets.token_hex(32),
    "ADMIN_PASSCODE": old_env.get("ADMIN_PASSCODE") or secrets.token_urlsafe(6),
    "ENFORCE_AUTH": os.environ.get("ENFORCE_AUTH") or old_env.get("ENFORCE_AUTH", "false"),
}
ENV = {"Variables": env}

if exists:
    lam.update_function_code(FunctionName=NAME, ZipFile=code)
    lam.get_waiter("function_updated_v2").wait(FunctionName=NAME)
    lam.update_function_configuration(FunctionName=NAME, Timeout=30,
                                      MemorySize=256, Environment=ENV)
    print("Updated review-api")
else:
    lam.create_function(FunctionName=NAME, Runtime="python3.12", Role=role,
                        Handler="handler.lambda_handler", Code={"ZipFile": code},
                        Timeout=30, MemorySize=256, Environment=ENV)
    print("Created review-api")
lam.get_waiter("function_active_v2").wait(FunctionName=NAME)
lam.get_waiter("function_updated_v2").wait(FunctionName=NAME)

if "ADMIN_PASSCODE" not in old_env:
    print(f"\nADMIN PASSCODE (save it somewhere private): {env['ADMIN_PASSCODE']}\n")
else:
    print("Admin passcode unchanged (it is stored in the Lambda's settings)")
print(f"Reviewer endpoints require sign-in: {env['ENFORCE_AUTH']}")

fn_arn = lam.get_function(FunctionName=NAME)["Configuration"]["FunctionArn"]

apigw = boto3.client("apigatewayv2", region_name=REGION)
api_id = next(a["ApiId"] for a in apigw.get_apis()["Items"]
              if a["Name"] == "claimguard-api")

integ = next((i for i in apigw.get_integrations(ApiId=api_id)["Items"]
              if i.get("IntegrationUri") == fn_arn), None)
if integ is None:
    integ = apigw.create_integration(ApiId=api_id, IntegrationType="AWS_PROXY",
                                     IntegrationUri=fn_arn,
                                     PayloadFormatVersion="2.0")
target = f"integrations/{integ['IntegrationId']}"

route = next((r for r in apigw.get_routes(ApiId=api_id)["Items"]
              if r["RouteKey"] == "$default"), None)
if route:
    apigw.update_route(ApiId=api_id, RouteId=route["RouteId"], Target=target)
else:
    apigw.create_route(ApiId=api_id, RouteKey="$default", Target=target)

try:
    lam.add_permission(FunctionName=NAME, StatementId="apigw-review",
                       Action="lambda:InvokeFunction",
                       Principal="apigateway.amazonaws.com",
                       SourceArn=f"arn:aws:execute-api:{REGION}:{account}:{api_id}/*/*")
except lam.exceptions.ResourceConflictException:
    pass
print("review-api is attached to the API")
