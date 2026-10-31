
import json

import boto3

REGION = "us-east-1"
NAME = "claimguard-triage"

sqs = boto3.client("sqs", region_name=REGION)
sfn = boto3.client("stepfunctions", region_name=REGION)
account = boto3.client("sts").get_caller_identity()["Account"]
role = f"arn:aws:iam::{account}:role/LabRole"
score_arn = f"arn:aws:lambda:{REGION}:{account}:function:score-claim"

review_url = sqs.create_queue(QueueName="claimguard-review")["QueueUrl"]
priority_url = sqs.create_queue(QueueName="claimguard-priority")["QueueUrl"]
print("Queues ready: claimguard-review, claimguard-priority")


def set_status(status, key_path="$.scoring.claim_id", end=True, next_state=None,
               queue=None):
    """A state that writes a status (and optionally a review queue) onto the claim."""
    expr = "SET #st = :s"
    values = {":s": {"S": status}}
    if queue:
        expr += ", review_queue = :q"
        values[":q"] = {"S": queue}
    state = {
        "Type": "Task",
        "Resource": "arn:aws:states:::dynamodb:updateItem",
        "Parameters": {
            "TableName": "Claims",
            "Key": {"claim_id": {"S.$": key_path}},
            "UpdateExpression": expr,
            "ExpressionAttributeNames": {"#st": "status"},   # reserved word
            "ExpressionAttributeValues": values,
        },
        "ResultPath": None,
    }
    if end:
        state["End"] = True
    else:
        state["Next"] = next_state
    return state


def send_to(queue_url, band, next_state):
    return {
        "Type": "Task",
        "Resource": "arn:aws:states:::sqs:sendMessage",
        "Parameters": {
            "QueueUrl": queue_url,
            "MessageBody": {
                "claim_id.$": "$.scoring.claim_id",
                "risk_score.$": "$.scoring.risk_score",
                "risk_band": band,
            },
        },
        "ResultPath": None,
        "Next": next_state,
    }


definition = {
    "Comment": "ClaimGuard: score a claim, then route it by risk band",
    "StartAt": "ScoreClaim",
    "States": {
        "ScoreClaim": {
            "Type": "Task",
            "Resource": "arn:aws:states:::lambda:invoke",
            "Parameters": {"FunctionName": score_arn,
                           "Payload": {"claim_id.$": "$.claim_id"}},
            "ResultSelector": {"claim_id.$": "$.Payload.claim_id",
                               "risk_score.$": "$.Payload.risk_score",
                               "risk_band.$": "$.Payload.risk_band"},
            "ResultPath": "$.scoring",
            "Retry": [{"ErrorEquals": ["Lambda.ServiceException",
                                       "Lambda.AWSLambdaException",
                                       "Lambda.SdkClientException",
                                       "Lambda.TooManyRequestsException"],
                       "IntervalSeconds": 2, "MaxAttempts": 3, "BackoffRate": 2}],
            "Catch": [{"ErrorEquals": ["States.ALL"], "ResultPath": "$.error",
                       "Next": "MarkScoringFailed"}],
            "Next": "RouteByRisk",
        },
        "RouteByRisk": {
            "Type": "Choice",
            "Choices": [
                {"Variable": "$.scoring.risk_band", "StringEquals": "LOW",
                 "Next": "AutoApprove"},
                {"Variable": "$.scoring.risk_band", "StringEquals": "MEDIUM",
                 "Next": "QueueForReview"},
                {"Variable": "$.scoring.risk_band", "StringEquals": "HIGH",
                 "Next": "QueuePriority"},
            ],
            "Default": "MarkScoringFailed",
        },
        "AutoApprove": set_status("AUTO_APPROVED"),
        "QueueForReview": send_to(review_url, "MEDIUM", "MarkPendingReview"),
        "MarkPendingReview": set_status("PENDING_REVIEW", queue="REVIEW"),
        "QueuePriority": send_to(priority_url, "HIGH", "MarkPriorityReview"),
        "MarkPriorityReview": set_status("PRIORITY_REVIEW", queue="PRIORITY"),
        "MarkScoringFailed": set_status("SCORING_FAILED", key_path="$.claim_id",
                                        end=False, next_state="Failed"),
        "Failed": {"Type": "Fail", "Error": "ScoringFailed",
                   "Cause": "The claim could not be scored"},
    },
}

body = json.dumps(definition)
try:
    arn = sfn.create_state_machine(name=NAME, definition=body, roleArn=role,
                                   type="STANDARD")["stateMachineArn"]
    print("Created state machine")
except sfn.exceptions.StateMachineAlreadyExists:
    arn = next(m["stateMachineArn"]
               for m in sfn.list_state_machines(maxResults=100)["stateMachines"]
               if m["name"] == NAME)
    sfn.update_state_machine(stateMachineArn=arn, definition=body, roleArn=role)
    print("Updated existing state machine")

print(arn)