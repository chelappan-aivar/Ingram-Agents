import os
import sys
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT))

# No AWS in unit tests: storage off, a fixed region, no real table.
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.pop("DATA_BUCKET", None)

SAMPLE_SOW = """# About Ingram Micro
Ingram Micro Holding Corporation is a leading global distributor.
# Statement of Work
This Statement of Work (SOW) is entered into by and between Ingram Micro and Acme Corp.
# Scope of Work
## In Scope
- Amazon Bedrock (Claude) agents for claims triage
- Amazon S3 document store
- **AWS Lambda** integrations
## Out of Scope
- Data migration
# Architecture
![Architecture Diagram](arch:1)
# Pricing
One-time delivery (fixed price): USD 50,000.
The recurring AWS run-rate is not restated here. Refer to the cost estimate below, which is the
authoritative source for the monthly and annual AWS run-rate:
AWS Pricing Calculator estimate: https://calculator.aws/#/estimate?id=sample123
# Roles And Responsibilities
| Item | Responsible | Accountable |
| --- | --- | --- |
| Project Kick-off | Ingram Micro | Ingram Micro |
# Schedule I: Estimated Project Schedule
The engagement runs for 8 weeks.
"""

# 1x1 transparent PNG
PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
