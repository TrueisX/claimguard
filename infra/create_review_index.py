
import time

import boto3
from boto3.dynamodb.conditions import Attr

REGION, INDEX = "us-east-1", "review-queue-index"
ddb = boto3.client("dynamodb", region_name=REGION)
table = boto3.resource("dynamodb", region_name=REGION).Table("Claims")


def state():
    t = ddb.describe_table(TableName="Claims")["Table"]
    return t["TableStatus"], {g["IndexName"]: g["IndexStatus"]
                              for g in t.get("GlobalSecondaryIndexes", [])}


if INDEX not in state()[1]:
    ddb.update_table(
        TableName="Claims",
        AttributeDefinitions=[
            {"AttributeName": "review_queue", "AttributeType": "S"},
            {"AttributeName": "received_at", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexUpdates=[{"Create": {
            "IndexName": INDEX,
            "KeySchema": [
                {"AttributeName": "review_queue", "KeyType": "HASH"},
                {"AttributeName": "received_at", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
        }}],
    )

print("Waiting for the index (this can take several minutes)", end="", flush=True)
while True:
    table_status, indexes = state()
    if table_status == "ACTIVE" and indexes.get(INDEX) == "ACTIVE":
        break
    print(".", end="", flush=True)
    time.sleep(5)
print(f"\n{INDEX} is ACTIVE")

# Tag claims that were already flagged by earlier tests
QUEUE_FOR = {"PRIORITY_REVIEW": "PRIORITY", "PENDING_REVIEW": "REVIEW"}
kwargs = {"FilterExpression": Attr("status").is_in(list(QUEUE_FOR))
                              & Attr("review_queue").not_exists()}
tagged = 0
while True:
    resp = table.scan(**kwargs)
    for item in resp["Items"]:
        table.update_item(Key={"claim_id": item["claim_id"]},
                          UpdateExpression="SET review_queue = :q",
                          ExpressionAttributeValues={":q": QUEUE_FOR[item["status"]]})
        tagged += 1
    if "LastEvaluatedKey" not in resp:
        break
    kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
print(f"Tagged {tagged} existing flagged claims")