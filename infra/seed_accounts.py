"""Create the ClaimGuardAccounts table and fill it with synthetic accounts.

Everything here is invented demo data: claimants, hospitals, insurer staff,
the admin account, and the fraud-criteria configuration the website shows.
Safe to rerun: same seed, same data, items are overwritten.
"""
import json
import random
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal

import boto3

REGION = "us-east-1"
TABLE = "ClaimGuardAccounts"
rng = random.Random(7)

ddb = boto3.client("dynamodb", region_name=REGION)
table = boto3.resource("dynamodb", region_name=REGION).Table(TABLE)


def dec(obj):
    """DynamoDB wants Decimal, not float."""
    return json.loads(json.dumps(obj), parse_float=Decimal)


def ensure_table():
    try:
        ddb.describe_table(TableName=TABLE)
        print(f"{TABLE} already exists")
    except ddb.exceptions.ResourceNotFoundException:
        ddb.create_table(
            TableName=TABLE,
            AttributeDefinitions=[{"AttributeName": "account_id", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "account_id", "KeyType": "HASH"}],
            BillingMode="PAY_PER_REQUEST",
        )
        print(f"Creating {TABLE} ...")
    ddb.get_waiter("table_exists").wait(TableName=TABLE)
    print(f"{TABLE} is ACTIVE")


FIRST = ["Aarav", "Vivaan", "Aditya", "Arjun", "Ishaan", "Rohan", "Kabir", "Dev",
         "Neel", "Yash", "Ananya", "Diya", "Isha", "Kavya", "Meera", "Naina",
         "Priya", "Riya", "Saanvi", "Tara", "Anika", "Sneha", "Pooja", "Nisha",
         "Aditi", "Rahul", "Karan", "Vikram", "Sanjay", "Lakshmi"]
LAST = ["Sharma", "Patel", "Iyer", "Nair", "Reddy", "Gupta", "Mehta", "Joshi",
        "Kulkarni", "Desai", "Shah", "Verma", "Rao", "Banerjee", "Chatterjee",
        "Singh", "Kapoor", "Menon", "Pillai", "Bhatt", "Naidu", "Thakur",
        "Jain", "Agarwal", "Chopra"]
CITIES = [("Mumbai", "Maharashtra"), ("Pune", "Maharashtra"),
          ("Ahmedabad", "Gujarat"), ("Surat", "Gujarat"),
          ("Bengaluru", "Karnataka"), ("Hyderabad", "Telangana"),
          ("Chennai", "Tamil Nadu"), ("Delhi", "Delhi"),
          ("Kolkata", "West Bengal"), ("Jaipur", "Rajasthan"),
          ("Lucknow", "Uttar Pradesh"), ("Bhopal", "Madhya Pradesh")]

PLANS = {"Silver": (300000, 9500), "Gold": (500000, 14500),
         "Platinum": (1000000, 26000)}          # sum insured, yearly premium (Rs)

HOSPITAL_PREFIX = ["Sunrise", "Lifeline", "Apex", "CityCare", "Medicover",
                   "Sanjeevani", "Arogya", "Shree", "Metro", "Jeevan", "Unity",
                   "Harmony", "Noble", "Vedanta", "Green Cross", "Rainbow",
                   "Amrit", "Navjeevan", "Prime", "Aastha"]
# label, bed range, chance of NABH accreditation
FACILITY_TYPES = [("Multi-specialty Hospital", (100, 600), 0.70),
                  ("Super-speciality Hospital", (200, 900), 0.85),
                  ("General Hospital", (60, 300), 0.40),
                  ("Nursing Home", (15, 60), 0.15),
                  ("Diagnostic Centre", (0, 0), 0.30),
                  ("Clinic", (2, 20), 0.05)]


def claimants():
    out = []
    for i in range(200):
        pid = f"PT{i:05d}"
        first, last = rng.choice(FIRST), rng.choice(LAST)
        city, state = rng.choice(CITIES)
        plan = rng.choices(list(PLANS), weights=[5, 3, 2])[0]
        sum_insured, premium = PLANS[plan]
        cover_from = date(2023, 4, 1) + timedelta(days=rng.randint(0, 940))
        out.append({
            "account_id": pid, "kind": "CLAIMANT", "patient_id": pid,
            "full_name": f"{first} {last}", "city": city, "state": state,
            "phone": f"+91 9{rng.randint(10, 99)}XXXXX{rng.randint(10, 99)}",
            "email": f"{first}.{last}{i}@example.com".lower(),
            "policy_number": f"CG-POL-{i:05d}", "plan": plan,
            "sum_insured": sum_insured, "annual_premium": premium,
            "cover_from": cover_from.isoformat(), "renewal_due": "2027-03-31",
            "policy_status": "Active",
            "family_members": rng.choice([1, 2, 3, 4]),
            "kyc_status": rng.choices(["Verified", "Pending"], weights=[9, 1])[0],
        })
    return out


def hospitals():
    out = []
    for n in range(300):
        pid = f"P{n:04d}"
        label, (lo, hi), nabh_chance = rng.choices(
            FACILITY_TYPES, weights=[3, 1, 2, 2, 1, 3])[0]
        city, state = rng.choice(CITIES)
        out.append({
            "account_id": pid, "kind": "HOSPITAL", "provider_id": pid,
            "name": f"{rng.choice(HOSPITAL_PREFIX)} {label}, {city}",
            "facility_type": label, "city": city, "state": state,
            "beds": rng.randint(lo, hi),
            "nabh_accredited": rng.random() < nabh_chance,
            "network_status": "In-network" if rng.random() < 0.8 else "Out-of-network",
            "registered_since": rng.randint(2000, 2022),
            "license_no": f"LIC-{rng.randint(10000, 99999)}",
        })
    return out


INSURERS = [
    ("INS-001", "Meera Iyer", "Claims Officer", "Mumbai", 50000, 2019),
    ("INS-002", "Rohan Desai", "Senior Claims Investigator", "Pune", 500000, 2016),
    ("INS-003", "Kavita Nair", "Claims Manager", "Bengaluru", 2500000, 2013),
    ("INS-004", "Arjun Malhotra", "Special Investigations Unit Lead", "Delhi", 5000000, 2011),
]


def staff():
    out = []
    for eid, name, title, region, limit, joined in INSURERS:
        out.append({
            "account_id": eid, "kind": "INSURER", "employee_id": eid,
            "full_name": name, "job_title": title, "region": region,
            "department": "Claims Adjudication" if "Claims" in title else "Fraud Investigation",
            "approval_limit": limit, "joined_year": joined,
            "email": f"{name.split()[0].lower()}.{name.split()[1].lower()}@claimguard.example",
            "cases_reviewed": rng.randint(800, 6000),
            "avg_decision_hours": round(rng.uniform(2.5, 14), 1),
        })
    return out


ADMIN = {
    "account_id": "ADM-001", "kind": "ADMIN", "full_name": "Platform Admin",
    "job_title": "Platform Owner (Builder)",
    "email": "admin@claimguard.example",
    "permissions": ["view_all_claims", "view_model_metrics", "edit_thresholds",
                    "manage_accounts", "retrain_model", "view_audit_log"],
}

# ---- the criteria the website shows ------------------------------------
# The thresholds below are starting values I chose, not tuned numbers.
# The admin console will let you change them.
CRITERIA = [
    {"id": "F1", "verdict": "FRAUD", "source": "model", "implemented": True,
     "feature": "procedure_mismatch", "threshold": 1,
     "title": "Procedure doesn't fit the diagnosis",
     "rule": "The billed procedure is not on the clinically sensible list for the diagnosis",
     "example": "Fever billed as heart surgery (SURG06)"},
    {"id": "F2", "verdict": "FRAUD", "source": "model", "implemented": True,
     "feature": "amount_ratio", "threshold": 1.5,
     "title": "Amount far above the typical price",
     "rule": "Billed amount is at least 1.5x the typical price for that procedure",
     "example": "Rs 3,10,000 billed for a procedure that typically costs Rs 1,50,000"},
    {"id": "F3", "verdict": "FRAUD", "source": "model", "implemented": True,
     "feature": "duplicate_candidate", "threshold": 1,
     "title": "Duplicate submission",
     "rule": "Same patient, provider, diagnosis, procedure and amount within 3 days",
     "example": "The same X-ray claim submitted twice on the same day"},
    {"id": "F4", "verdict": "FRAUD", "source": "model", "implemented": True,
     "feature": "days_since_previous_claim", "threshold": 3,
     "title": "Repeat visit within days",
     "rule": "The same patient is billed by the same provider again within 3 days",
     "example": "Two consultations billed on consecutive days"},
    {"id": "F5", "verdict": "FRAUD", "source": "model", "implemented": True,
     "feature": "same_day_patient_provider_count", "threshold": 1,
     "title": "Split billing on one day",
     "rule": "The patient was already billed by this provider earlier the same day (unbundling)",
     "example": "One visit split into 4 small claims"},
    {"id": "F6", "verdict": "FRAUD", "source": "model", "implemented": True,
     "feature": "provider_claims_30d", "threshold": 35,
     "title": "Provider billing burst",
     "rule": "An unusually high number of claims from one provider in 30 days (phantom billing signal)",
     "example": "A clinic billing 40 one-time patients in three weeks"},
    {"id": "F7", "verdict": "FRAUD", "source": "model", "implemented": True,
     "feature": "provider_patient_count", "threshold": 2,
     "title": "Same patient-provider pair keeps repeating",
     "rule": "The patient has billed this provider 2 or more times before (ring signal)",
     "example": "A small group of patients and providers billing each other"},
    {"id": "F8", "verdict": "FRAUD", "source": "business rule", "implemented": False,
     "feature": "policy_status", "threshold": 0,
     "title": "Policy expired or sum insured exceeded",
     "rule": "Claim date is outside the policy period, or total claims exceed the sum insured",
     "example": "A claim dated after the policy lapsed"},
    {"id": "F9", "verdict": "FRAUD", "source": "business rule", "implemented": False,
     "feature": "network_status", "threshold": 50000,
     "title": "Out-of-network hospital with a high amount",
     "rule": "Hospital is out-of-network and the claim is above Rs 50,000",
     "example": "A Rs 2,00,000 claim from an unlisted nursing home"},
    {"id": "L1", "verdict": "LEGIT", "source": "model", "implemented": True,
     "feature": "procedure_mismatch", "threshold": 0,
     "title": "Procedure fits the diagnosis",
     "rule": "The billed procedure is on the clinically sensible list for the diagnosis",
     "example": "Fever with a consultation (CONS01)"},
    {"id": "L2", "verdict": "LEGIT", "source": "model", "implemented": True,
     "feature": "amount_ratio", "threshold": 1.2,
     "title": "Amount close to the typical price",
     "rule": "Billed amount is between 0.8x and 1.2x the typical price",
     "example": "Rs 820 billed for a Rs 800 consultation"},
    {"id": "L3", "verdict": "LEGIT", "source": "model", "implemented": True,
     "feature": "duplicate_candidate", "threshold": 0,
     "title": "No duplicate found",
     "rule": "No identical claim from the same patient and provider in the last 3 days",
     "example": "A first-time claim for this procedure"},
    {"id": "L4", "verdict": "LEGIT", "source": "model", "implemented": True,
     "feature": "days_since_previous_claim", "threshold": 30,
     "title": "Visits are well spaced",
     "rule": "No earlier visit to this provider in the last 30 days, or none at all",
     "example": "First visit to this hospital"},
    {"id": "L5", "verdict": "LEGIT", "source": "model", "implemented": True,
     "feature": "provider_claims_30d", "threshold": 20,
     "title": "Normal provider volume",
     "rule": "The provider billed 20 or fewer claims in the last 30 days",
     "example": "A clinic with a steady week-by-week pattern"},
    {"id": "L6", "verdict": "LEGIT", "source": "business rule", "implemented": False,
     "feature": "policy_status", "threshold": 0,
     "title": "Policy active and within the sum insured",
     "rule": "Claim date falls inside the policy period and cover remains",
     "example": "Gold plan, Rs 5,00,000 cover, Rs 40,000 used"},
    {"id": "L7", "verdict": "LEGIT", "source": "business rule", "implemented": False,
     "feature": "network_status", "threshold": 0,
     "title": "In-network, accredited hospital",
     "rule": "Hospital is in the insurer's network and NABH accredited",
     "example": "A listed multi-specialty hospital"},
]

ROUTING = {
    "low_max": 0.3, "high_min": 0.7,
    "bands": [
        {"band": "LOW", "range": "0.00 to 0.30", "action": "Auto-approve"},
        {"band": "MEDIUM", "range": "0.30 to 0.70", "action": "Manual verification by a reviewer"},
        {"band": "HIGH", "range": "0.70 to 1.00", "action": "Priority review"},
    ],
    "manual_verification_triggers": [
        {"trigger": "Fraud score between 0.30 and 0.70", "implemented": True},
        {"trigger": "Claim amount above the reviewer's approval limit", "implemented": False},
        {"trigger": "Claimant KYC is still Pending", "implemented": False},
        {"trigger": "Out-of-network hospital and amount above Rs 50,000", "implemented": False},
    ],
}


def main():
    ensure_table()
    items = (claimants() + hospitals() + staff() + [ADMIN]
             + [{"account_id": "CONFIG#criteria", "kind": "CONFIG", "criteria": CRITERIA},
                {"account_id": "CONFIG#routing", "kind": "CONFIG", **ROUTING}])
    with table.batch_writer() as b:
        for it in items:
            b.put_item(Item=dec(it))
    print(f"Wrote {len(items)} items")

    counts, kwargs = Counter(), {"ProjectionExpression": "#k",
                                 "ExpressionAttributeNames": {"#k": "kind"}}
    while True:
        resp = table.scan(**kwargs)
        counts.update(i["kind"] for i in resp["Items"])
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    print("In the table now:", dict(counts))


main()