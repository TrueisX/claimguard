
import io
import os
import zipfile

import boto3

REGION, NAME = "us-east-1", "review-api"
account = boto3.client("sts").get_caller_identity()["Account"]
role = f"arn:aws:iam::{account}:role/LabRole"
ENV = {"Variables": {"TABLE": "Claims"}}

buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
    z.write("lambdas/review_api/handler.py", "handler.py")
    if os.path.exists("dashboard/dashboard.html"):          # added in Task 13
        z.write("dashboard/dashboard.html", "dashboard.html")
code = buf.getvalue()

lam = boto3.client("lambda", region_name=REGION)
try:
    lam.get_function(FunctionName=NAME)
    lam.update_function_code(FunctionName=NAME, ZipFile=code)
    lam.get_waiter("function_updated_v2").wait(FunctionName=NAME)
    lam.update_function_configuration(FunctionName=NAME, Timeout=15,
                                      MemorySize=256, Environment=ENV)
    print("Updated review-api")
except lam.exceptions.ResourceNotFoundException:
    lam.create_function(FunctionName=NAME, Runtime="python3.12", Role=role,
                        Handler="handler.lambda_handler", Code={"ZipFile": code},
                        Timeout=15, MemorySize=256, Environment=ENV)
    print("Created review-api")
lam.get_waiter("function_active_v2").wait(FunctionName=NAME)
lam.get_waiter("function_updated_v2").wait(FunctionName=NAME)
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