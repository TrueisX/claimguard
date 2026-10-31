
from boto3.dynamodb.conditions import Key


def query_all(table, index, key, value):
    kwargs = {"IndexName": index, "KeyConditionExpression": Key(key).eq(value)}
    items = []
    while True:
        resp = table.query(**kwargs)
        items += resp["Items"]
        if "LastEvaluatedKey" not in resp:
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def fetch_history(table, provider_id, patient_id):
    """Returns (provider_history, patient_history)."""
    return (query_all(table, "provider-index", "provider_id", provider_id),
            query_all(table, "patient-index", "patient_id", patient_id))