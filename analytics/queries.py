"""Named Athena queries. Fixed text only: nothing a user types is ever put into SQL."""

HISTORY = {
    "fraud_by_type": (
        "Where the fraud money is, by fraud pattern",
        """SELECT fraud_type, count(*) AS claims, round(sum(amount), 0) AS total_amount,
                  round(avg(amount), 0) AS avg_amount
           FROM history_labeled WHERE is_fraud = 1
           GROUP BY fraud_type ORDER BY total_amount DESC"""),
    "fraud_by_month": (
        "Fraud rate by month",
        """SELECT substr(claim_date, 1, 7) AS month, count(*) AS claims, sum(is_fraud) AS fraud,
                  round(100.0 * sum(is_fraud) / count(*), 2) AS fraud_pct
           FROM history_labeled GROUP BY substr(claim_date, 1, 7) ORDER BY month"""),
    "riskiest_hospitals": (
        "Ten hospitals with the highest share of fraud (at least 20 claims)",
        """SELECT provider_id, count(*) AS claims, sum(is_fraud) AS fraud,
                  round(100.0 * sum(is_fraud) / count(*), 1) AS fraud_pct,
                  round(sum(CASE WHEN is_fraud = 1 THEN amount ELSE 0 END), 0) AS fraud_amount
           FROM history_labeled GROUP BY provider_id HAVING count(*) >= 20
           ORDER BY fraud_pct DESC, fraud_amount DESC LIMIT 10"""),
    "mismatch_lift": (
        "Fraud rate when the procedure does not fit the diagnosis",
        """SELECT CAST(procedure_mismatch AS integer) AS procedure_mismatch, count(*) AS claims,
                  round(100.0 * avg(is_fraud), 2) AS fraud_pct
           FROM history_labeled GROUP BY CAST(procedure_mismatch AS integer) ORDER BY 1"""),
    "amount_bands": (
        "Fraud rate by how far the bill is above the usual cost",
        """SELECT CASE WHEN amount_ratio < 1.3 THEN '1 normal (under 1.3x)'
                      WHEN amount_ratio < 2 THEN '2 high (1.3x to 2x)'
                      ELSE '3 very high (2x or more)' END AS amount_band,
                  count(*) AS claims, round(100.0 * avg(is_fraud), 2) AS fraud_pct
           FROM history_labeled GROUP BY 1 ORDER BY 1"""),
}

LIVE = {
    "live_by_band": (
        "Live claims by risk band and status",
        """SELECT risk_band, status, count(*) AS claims, round(avg(risk_score), 3) AS avg_score
           FROM live_claims WHERE risk_band IS NOT NULL
           GROUP BY risk_band, status ORDER BY risk_band, status"""),
    "live_reviewer_outcomes": (
        "What each reviewer decided",
        """SELECT reviewer, reviewer_decision, count(*) AS claims, round(sum(amount), 0) AS amount
           FROM live_claims WHERE reviewer_decision IS NOT NULL
           GROUP BY reviewer, reviewer_decision ORDER BY reviewer, reviewer_decision"""),
    "live_by_diagnosis": (
        "Average live risk score by diagnosis",
        """SELECT diagnosis, count(*) AS claims, round(avg(risk_score), 3) AS avg_score
           FROM live_claims WHERE risk_score IS NOT NULL
           GROUP BY diagnosis ORDER BY avg_score DESC"""),
}

ALL = {**HISTORY, **LIVE}

# Column types follow the exported files. history_labeled is a CSV with a header row.
FEATURE_COLUMNS = ["expected_cost", "amount_ratio", "procedure_mismatch", "provider_claims_30d",
                   "patient_claims_30d", "provider_avg_amount", "provider_patient_count",
                   "days_since_previous_claim", "duplicate_candidate",
                   "same_day_provider_claim_count", "same_day_patient_provider_count"]

DDL_DATABASE = "CREATE DATABASE IF NOT EXISTS claimguard"


def ddl_history(bucket):
    features = ",\n  ".join(f"{c} double" for c in FEATURE_COLUMNS)
    return f"""CREATE EXTERNAL TABLE IF NOT EXISTS claimguard.history_labeled (
  claim_id string, provider_id string, patient_id string, claim_date string,
  diagnosis string, procedure_code string, amount double, is_fraud int, fraud_type string,
  {features},
  split string
)
ROW FORMAT DELIMITED FIELDS TERMINATED BY ','
STORED AS TEXTFILE
LOCATION 's3://{bucket}/analytics/history_labeled/'
TBLPROPERTIES ('skip.header.line.count'='1')"""


def ddl_live(bucket):
    return f"""CREATE EXTERNAL TABLE IF NOT EXISTS claimguard.live_claims (
  claim_id string, provider_id string, patient_id string, claim_date string,
  diagnosis string, procedure_code string, amount double, status string,
  risk_score double, risk_band string, received_at string, scored_at string,
  reviewer_decision string, reviewer string, reviewed_at string, reviewer_label int
)
ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
LOCATION 's3://{bucket}/analytics/live_claims/'"""
