"""Deterministic, LLM-free compliance checks of a generated SOW draft (Layer 1).

Only these rules set pass/fail. The optional LLM reviewer contributes advisory issues and
never flips a rule.
"""

from sow_generator.services.draft_rules.checker import (
    ISSUE_CAP,
    check_draft,
    merge_issues,
)
from sow_generator.services.draft_rules.config import RulesConfigError, load_rules

__all__ = ["ISSUE_CAP", "RulesConfigError", "check_draft", "load_rules", "merge_issues"]
