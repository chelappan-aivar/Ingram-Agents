"""Well-Architected review of a SOW draft, run before the architecture diagram is generated.

Ordering matters here. The architect settles the service decisions while writing the SOW, so
reviewing the SOW catches a bad decision before a diagram and a cost estimate are built on it.
Reviewing after the diagram means an extra loop through all three stages.

This review is local rather than the AWS managed Well-Architected Agent because that service's
architecture review takes CDK or Terraform from an S3 URI and cannot read prose. See
sow_generator/schemas/wa.py for the detail and where the managed agent does fit.
"""

from sow_generator.services.wa_review.checks import DECISION_CHECKS, REQUIRED_TOPICS
from sow_generator.services.wa_review.reviewer import (
    WaVerificationOutcome,
    merge_findings,
    review_sow,
    run_wa_review,
)

__all__ = [
    "DECISION_CHECKS",
    "REQUIRED_TOPICS",
    "WaVerificationOutcome",
    "merge_findings",
    "review_sow",
    "run_wa_review",
]
