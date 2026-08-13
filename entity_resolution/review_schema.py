"""Entity resolution review record schema and validation."""

from enum import Enum
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional
import json

# Core decision types
class Verdict(str, Enum):
    """Reviewer decision on a pair."""
    MERGE = "MERGE"
    REJECT = "REJECT"
    DEFER = "DEFER"
    ALIAS = "ALIAS"              # fka, formerly known as
    VARIANT = "VARIANT"          # different names


# Structured reason codes for decisions
class ReasonCode(str, Enum):
    """Structured reason for rejection or deferral."""
    DIFF_MANAGER = "DIFF_MANAGER"
    DIFF_FUND_VINTAGE = "DIFF_FUND_VINTAGE"
    DIFF_COMPANY = "DIFF_COMPANY"
    GENERIC_WORDS_ONLY = "GENERIC_WORDS_ONLY"
    TYPO_VARIANT = "TYPO_VARIANT"
    NEEDS_GP_CONFIRM = "NEEDS_GP_CONFIRM"
    AMBIGUOUS = "AMBIGUOUS"
    OTHER = "OTHER"


class Confidence(str, Enum):
    """Confidence level in the decision."""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass
class ReviewRecord:
    """Complete review record for an entity pair."""

    # Identity
    pair_id: int
    reviewed_by: str
    reviewed_at: str

    # Original pair information
    score: float
    id_a: str
    id_b: str
    name_a: str                     # Entity A name
    name_b: str                     # Entity B name

    # Core decision
    verdict: Verdict                # MERGE / REJECT / DEFER / PARENT_CHILD / ALIAS / VARIANT
    reason_code: Optional[ReasonCode] = None  # Structured reason code
    evidence: str = ""              # Supporting evidence
    confidence: Confidence = Confidence.HIGH  # Decision confidence level

    # Follow-up actions
    needs_followup: bool = False    # Whether followup is needed (e.g., GP confirmation)
    followup_notes: str = ""        # Notes on followup needed

    def to_dict(self):
        """Convert to dictionary, with enums as strings."""
        d = asdict(self)
        d['verdict'] = self.verdict.value
        if self.reason_code:
            d['reason_code'] = self.reason_code.value
        d['confidence'] = self.confidence.value
        return d

    def to_json(self, compact: bool = True):
        """Serialize to JSON (compact for JSONL by default, pretty for display)."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=None if compact else 2)

    @classmethod
    def from_dict(cls, data: dict) -> 'ReviewRecord':
        """Construct from dictionary."""
        data = dict(data)
        data['verdict'] = Verdict(data['verdict'])
        if data.get('reason_code'):
            data['reason_code'] = ReasonCode(data['reason_code'])
        data['confidence'] = Confidence(data['confidence'])
        return cls(**data)


def create_review_record(
    pair_id: int,
    reviewed_by: str,
    score: float,
    id_a: str,
    id_b: str,
    name_a: str,
    name_b: str,
    verdict: Verdict,
    reason_code: Optional[ReasonCode] = None,
    evidence: str = "",
    confidence: Confidence = Confidence.HIGH,
    needs_followup: bool = False,
    followup_notes: str = "",
) -> ReviewRecord:
    """Factory function with better ergonomics."""
    return ReviewRecord(
        pair_id=pair_id,
        reviewed_by=reviewed_by,
        reviewed_at=datetime.now().isoformat(),
        score=score,
        id_a=id_a,
        id_b=id_b,
        name_a=name_a,
        name_b=name_b,
        verdict=verdict,
        reason_code=reason_code,
        evidence=evidence,
        confidence=confidence,
        needs_followup=needs_followup,
        followup_notes=followup_notes,
    )
