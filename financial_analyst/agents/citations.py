"""Mechanical checks for Stage 3 filing references in Stage 2 prose."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

_REFERENCE = re.compile(r"\[FILING\s+(\d+)\]", re.IGNORECASE)
# These phrases make a sentence explicitly about filing disclosures. This is a
# conservative structural check, not a claim that arbitrary prose can be
# semantically grounded by a regular expression.
_FILING_LANGUAGE = re.compile(
    r"\b(?:filing|10-k|10-k|annual report|risk factor|disclosed|disclosure|"
    r"management stated|management said|the company stated|the company said|"
    r"according to (?:the )?(?:filing|annual report))\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CitationIssue:
    kind: str  # unknown_filing_reference | missing_filing_reference
    detail: str

    def __str__(self) -> str:
        return f"[{self.kind}] {self.detail}"


def check_filing_citations(
    narrative: str, source_ids: Iterable[str]
) -> list[CitationIssue]:
    """Check that references resolve and explicit filing claims are cited."""
    available = {str(source_id).upper() for source_id in source_ids}
    issues: list[CitationIssue] = []
    for match in _REFERENCE.finditer(narrative or ""):
        source_id = f"FILING {match.group(1)}"
        if source_id not in available:
            issues.append(CitationIssue(
                "unknown_filing_reference",
                f"{source_id} is cited but was not among the retrieved filing sources",
            ))

    for sentence in re.split(r"(?<=[.!?])\s+", narrative or ""):
        if _FILING_LANGUAGE.search(sentence) and not _REFERENCE.search(sentence):
            issues.append(CitationIssue(
                "missing_filing_reference",
                f"filing-related statement has no [FILING n] citation: {sentence.strip()}",
            ))
    return issues
