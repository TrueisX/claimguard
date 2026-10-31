
import time

import boto3

ddb = boto3.client("dynamodb", region_name="us-east-1")
TABLE = "Claims"


def describe():
    return ddb.describe_table(TableName=TABLE)["Table"]


def wait_until_active(index):
    while True:
        t = describe()
        states = {g["IndexName"]: g["IndexStatus"]
                  for g in t.get("GlobalSecondaryIndexes", [])}
        if t["TableStatus"] == "ACTIVE" and states.get(index) == "ACTIVE":
            return
        print(f"  waiting for {index} ...")
        time.sleep(5)


def add_index(index, key):
    have = {g["IndexName"] for g in describe().get("GlobalSecondaryIndexes", [])}
    if index in have:
        print(f"{index} already exists")
        return
    ddb.update_table(
        TableName=TABLE,
        AttributeDefinitions=[
            {"AttributeName": key, "AttributeType": "S"},
            {"AttributeName": "claim_date", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexUpdates=[{"Create": {
            "IndexName": index,
            "KeySchema": [
                {"AttributeName": key, "KeyType": "HASH"},
                {"AttributeName": "claim_date", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
        }}],
    )
    wait_until_active(index)
    print(f"{index} is ACTIVE")


add_index("provider-index", "provider_id")
add_index("patient-index", "patient_id")