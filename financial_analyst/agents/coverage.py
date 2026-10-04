"""Flag coverage: did the analysis engage with what Stage 1 actually raised?

This is the deterministic *screen*. It exists to answer one question cheaply --
"which flags show no trace at all?" -- and deliberately **never** concludes that
a flag was ignored. That direction of error is the dangerous one: an analysis
that paraphrases without using my keywords looks identical to one that never
thought about it, and a screen that quietly ruled on that would produce false
confidence.

So the screen returns one of three states per flag:

``ADDRESSED``
    The rule id, or the flag's own value, appears in the narrative. Stops here.

``LOOKS_UNADDRESSED``
    No trace, but the flag has a distinctive signal that a real treatment would
    almost certainly have surfaced. Goes to the model.

``UNCLEAR``
    The flag has no distinctive signal to look for -- ``imputed_column`` has no
    number at all. Always goes to the model, because silence proves nothing.

Model cost is therefore proportional to the actual problem: a company with no
flags costs nothing, and one whose flags are all cited costs nothing either.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

from .flags import FlagRef

ADDRESSED = "addressed"
LOOKS_UNADDRESSED = "looks_unaddressed"
UNCLEAR = "unclear"


@dataclass(frozen=True)
class CoverageIssue:
    """One flag's coverage status, with what decided it."""

    flag: FlagRef
    status: str
    detail: str = ""
    cited: tuple[str, ...] = ()

    @property
    def addressed(self) -> bool:
        return self.status == ADDRESSED

    @property
    def needs_review(self) -> bool:
        return self.status != ADDRESSED

    def __str__(self) -> str:
        return (
            f"[{self.status}] {self.flag.rule} {self.flag.year_label}: {self.detail}"
        )


def _mentions(narrative: str, token: str) -> bool:
    """Does the narrative mention this token?

    Two boundary rules, because one is not enough:

    * A rule id needs a strict alphanumeric boundary -- ``roe`` must not match
      inside ``gross``.
    * A value needs only a *numeric* boundary -- ``0.36`` must still match inside
      ``0.36x``, or every figure written with a unit suffix would look absent.
    """
    if not token:
        return False
    numeric = any(character.isdigit() for character in token)
    tail = r"(?![0-9])" if numeric else r"(?![A-Za-z0-9])"
    head = r"(?<![A-Za-z0-9.])" if numeric else r"(?<![A-Za-z0-9])"
    return re.search(head + re.escape(token) + tail, narrative, re.IGNORECASE) is not None


def screen_flag(narrative: str, flag: FlagRef) -> CoverageIssue:
    """Classify one flag's coverage against the narrative."""
    cited = tuple(token for token in flag.signals() if _mentions(narrative, token))
    if cited:
        return CoverageIssue(
            flag=flag, status=ADDRESSED, detail=f"cited {', '.join(cited)}", cited=cited
        )
    if flag.value is None:
        # Nothing distinctive to search for, so absence proves nothing.
        return CoverageIssue(
            flag=flag,
            status=UNCLEAR,
            detail="no distinctive signal for this flag, so cannot be screened",
        )
    return CoverageIssue(
        flag=flag,
        status=LOOKS_UNADDRESSED,
        detail=f"no mention of {flag.rule} or its value {flag.value:g}",
    )


def screen_flags(
    narrative: str, flags: Sequence[FlagRef]
) -> tuple[CoverageIssue, ...]:
    """Screen every flag, in the order Stage 1 wrote them."""
    for flag in flags:
        if not isinstance(flag, FlagRef):
            raise TypeError(
                f"flag coverage needs FlagRef records, got {type(flag).__name__}; "
                "use agents.flags.load_flag_refs rather than rendered text"
            )
    return tuple(screen_flag(narrative, flag) for flag in flags)


def summarise(issues: Sequence[CoverageIssue]) -> dict[str, int]:
    """Counts for the report, so coverage is auditable rather than inferred."""
    counts = {ADDRESSED: 0, LOOKS_UNADDRESSED: 0, UNCLEAR: 0}
    for issue in issues:
        counts[issue.status] += 1
    return counts


def require_addressed(issues: Sequence[CoverageIssue]) -> tuple[CoverageIssue, ...]:
    """The must-address flags the screen could not find any trace of.

    Info flags are excluded outright, and advisory severities are recorded but
    not demanded -- otherwise the check fires on essentially every analysis and
    teaches its reader to ignore it.
    """
    return tuple(
        issue for issue in issues if issue.needs_review and issue.flag.must_address
    )


def advisory_only(issues: Sequence[CoverageIssue]) -> tuple[CoverageIssue, ...]:
    """Unaddressed warnings: reported, not fatal."""
    return tuple(
        issue for issue in issues if issue.needs_review and issue.flag.advisory
    )