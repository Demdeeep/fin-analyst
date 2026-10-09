"""The critic agent.

Three checks, in increasing order of cost and decreasing order of certainty:

**1. Figures.** :mod:`financial_analyst.agents.verification` compares every
number in the prose against Stage 1's output. Free, deterministic, and catches
the failure that actually matters. Asking a model whether 3.2x matches the
metrics would be both expensive and less reliable than comparing the numbers.

**2. Flag coverage, screened.** :mod:`financial_analyst.agents.coverage` finds
flags with no trace in the narrative. It only *selects candidates*; it never
concludes that a flag was ignored, because absence of a keyword is not proof
and that error would be invisible.

**3. Flag coverage, adjudicated.** The model is asked only about the candidates
from step 2, and only about the ones whose severity means they matter.

Two rules hold throughout:

* **A deterministic finding is never overridden by the model.** If the figures
  do not reconcile, the verdict is not OK whatever the critic says -- a language
  model asked to check arithmetic will often confirm whatever it is shown.
* **An unparseable reply abstains** rather than inventing a revision the analyst
  cannot act on, falling back to the deterministic result with a note saying so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

from . import coverage as cov
from . import prompts
from .coverage import CoverageIssue
from .flags import FlagRef
from .providers.base import LLMClient
from .tools import MetricStore
from .utils_text import company_line
from .verification import ClaimIssue, verify

OK = "OK"
REVISE = "REVISE"
REJECT = "REJECT"
VERDICTS = (OK, REVISE, REJECT)


@dataclass(frozen=True)
class CriticVerdict:
    """The critic's conclusion, with every finding attached and typed."""

    ticker: str
    verdict: str
    issues: tuple[ClaimIssue, ...] = ()
    coverage: tuple[CoverageIssue, ...] = ()
    notes: str = ""
    model_consulted: bool = False
    #: Whether another attempt could plausibly fix this. ``REJECT`` has two
    #: causes with opposite answers: an unaddressed flag is fixed by rewording,
    #: a numerified undefined metric is not fixed by anything.
    retryable: bool = True
    flags_considered: int = 0
    flags_addressed: int = 0

    @property
    def clean(self) -> bool:
        return self.verdict == OK and not self.findings

    @property
    def verified(self) -> bool:
        """Every figure reconciles. Distinct from the verdict: an analysis can be
        figure-clean and still be rejected for ignoring a flag."""
        return not self.issues

    @property
    def findings(self) -> tuple[str, ...]:
        """Every finding as a string, for reports and logs."""
        return tuple(str(item) for item in self.issues) + tuple(
            str(item) for item in self.coverage if item.needs_review
        )

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "verdict": self.verdict,
            "retryable": self.retryable,
            "verified": not self.issues,
            "flags_considered": self.flags_considered,
            "flags_addressed": self.flags_addressed,
            "figure_findings": [str(issue) for issue in self.issues],
            "coverage_findings": [
                str(item) for item in self.coverage if item.needs_review
            ],
            "notes": self.notes,
            "model_consulted": self.model_consulted,
        }


def _render_verification(issues) -> str:
    if not issues:
        return (
            "Every figure in the analysis reconciles with a value Stage 1 "
            "computed for this company, and no undefined metric was given a "
            "number."
        )
    return "\n".join(f"  {issue}" for issue in issues)


def _render_flag_candidates(issues) -> str:
    """The flags that need adjudication, and why each needs it."""
    return "\n".join(
        f"  {issue.flag.render()}\n      screen: {issue.detail}"
        for issue in issues
    )


class CriticAgent:
    """Reviews one narrative against the numbers *and* the flags it was given."""

    def __init__(
        self,
        client: Optional[LLMClient],
        store: MetricStore,
        consult_model: bool = True,
    ):
        # A None client is a supported configuration: the mechanical checks
        # alone are a complete safety net, and are what CI should rely on.
        self.client = client
        self.store = store
        self.consult_model = consult_model and client is not None

    def review(
        self,
        ticker: str,
        narrative: str,
        year: Optional[str] = None,
        flags: Sequence[FlagRef] = (),
    ) -> CriticVerdict:
        issues = tuple(verify(narrative, self.store, ticker, year))
        screened = cov.screen_flags(narrative, flags)
        counts = cov.summarise(screened)

        # An undefined metric given a number is not repairable by rewording, so
        # it returns here rather than falling through to a retryable verdict.
        if any(issue.kind == "numerified_undefined" for issue in issues):
            return CriticVerdict(
                ticker=ticker, verdict=REJECT, issues=issues, coverage=screened,
                notes="a metric Stage 1 declined to compute was given a number",
                retryable=False,
                flags_considered=len(screened),
                flags_addressed=counts[cov.ADDRESSED],
            )

        notes: list[str] = []
        unresolved = list(cov.require_addressed(screened))

        # Ask the model about *only* the candidates whose severity matters, and
        # only when there is at least one. ACME has no flags, so this costs
        # nothing; INIT's twenty-three collapse to its six criticals.
        if unresolved and self.consult_model:
            reply = self.client.complete(
                system=prompts.CRITIC_FLAG_SYSTEM,
                user=prompts.CRITIC_FLAG_USER.format(
                    company_name=company_line(self.store, ticker, year)["company_name"],
                    ticker=ticker,
                    candidates=_render_flag_candidates(unresolved),
                    narrative=narrative,
                ),
            )
            addressed, flag_notes = _parse_flag_review(reply)
            if flag_notes:
                notes.append(flag_notes)
            if addressed is None:
                # Abstention is not acquittal. The screen stands.
                notes.append(
                    "critic gave no usable answer on flag coverage; treating the "
                    "screen as correct"
                )
            else:
                unresolved = [
                    issue for issue in unresolved
                    if f"{issue.flag.rule} {issue.flag.year_label}" not in addressed
                ]

        verdict = _derive_verdict(issues, unresolved)
        summary = _code_checked_summary(issues, unresolved)

        # General judgement on the prose, when the flag round did not already use
        # the model. Gating on `screened` here would silently skip prose review
        # for any company Stage 1 raised nothing against, which is backwards:
        # those are exactly the analyses where an unsupported conclusion is most
        # likely to go unnoticed.
        if self.consult_model and not unresolved:
            reply = self.client.complete(
                system=prompts.CRITIC_SYSTEM,
                user=prompts.CRITIC_USER.format(
                    company_name=company_line(self.store, ticker, year)["company_name"],
                    ticker=ticker,
                    verification_section=_render_verification(issues),
                    coverage_section=_render_coverage_counts(screened),
                    narrative=narrative,
                ),
            )
            parsed, general_notes = _parse_verdict(reply)
            if parsed is not None:
                # The general model reply may add notes and may soften an OK to
                # REVISE, but it must not escalate to REJECT on its own. Only
                # code-checked findings (numerified_undefined or unresolved
                # must-address flags) may do that.
                if parsed == REVISE and verdict == OK:
                    verdict = REVISE
                if general_notes:
                    notes.append(general_notes)
            else:
                notes.append(
                    "critic model returned no usable verdict; mechanically "
                    "verified but not model-reviewed"
                )
        elif not self.consult_model:
            notes.append("mechanical checks only; not model-reviewed")

        if summary:
            notes.insert(0, summary)

        return CriticVerdict(
            ticker=ticker,
            verdict=verdict,
            issues=issues,
            coverage=screened,
            notes="; ".join(note for note in notes if note),
            model_consulted=self.consult_model,
            # Always retryable by this point: the one non-retryable cause
            # returned above. An unaddressed flag is exactly what a retry fixes.
            retryable=True,
            flags_considered=len(screened),
            flags_addressed=counts[cov.ADDRESSED],
        )


def _parse_flag_review(reply: str) -> tuple[Optional[set[str]], str]:
    """Which candidate flags does the critic judge to be genuinely addressed?

    The model lists them as ``ADDRESSED: rule FYyear`` lines. Returns ``None``
    when nothing parseable came back, which the caller treats as *the screen
    stands* rather than as agreement.
    """
    text = (reply or "").strip()
    if not text:
        return None, ""
    addressed = {
        match.group("rest").strip()
        for match in re.finditer(r"^\s*ADDRESSED:\s*(?P<rest>.+)$", text, re.MULTILINE | re.I)
    }
    if not addressed:
        return None, text
    notes = " ".join(
        line.strip() for line in text.splitlines()
        if not line.strip().upper().startswith("ADDRESSED:")
    ).strip()
    return addressed, notes


def _render_coverage_counts(screened) -> str:
    counts = cov.summarise(screened)
    if not screened:
        return "Stage 1 raised no flags for this company."
    return (
        f"Stage 1 raised {len(screened)} flag(s); {counts[cov.ADDRESSED]} were "
        f"referenced in the analysis, {counts[cov.LOOKS_UNADDRESSED] + counts[cov.UNCLEAR]} "
        "were not detectably engaged with."
    )


def _code_checked_summary(issues, unresolved) -> str:
    if any(issue.kind == "numerified_undefined" for issue in issues):
        return "a metric Stage 1 declined to compute was given a number"
    if unresolved:
        return f"{len(unresolved)} must-address flag(s) remain unresolved after screening"
    if issues:
        return f"{len(issues)} figure(s) did not reconcile with Stage 1"
    return ""


def _derive_verdict(issues, unresolved) -> str:
    """Precedence: a hard figure failure, then an unaddressed must-address flag,
    then softer findings."""
    if any(issue.kind == "numerified_undefined" for issue in issues):
        return REJECT
    if unresolved:
        return REJECT
    if issues:
        return REVISE
    return OK


def _parse_verdict(reply: str) -> tuple[Optional[str], str]:
    """Pull the verdict word and reasoning out of the critic's reply.

    Returns ``(None, text)`` when no verdict word is recognised, so the caller
    can distinguish "the critic said REVISE" from "the critic said something
    else entirely" -- the difference between a real finding and a silent one.
    """
    text = (reply or "").strip()
    if not text:
        return None, ""
    # Compare the whole first token, not a prefix: "okay then" starts with "ok"
    # but is not a verdict, and reading it as one would log agreement that the
    # critic never gave. Anything unrecognised abstains, which is the safe
    # direction -- the caller falls back to the mechanical result.
    token = text.split()[0].strip("*_ .:").upper()
    if token in VERDICTS:
        rest = "\n".join(text.splitlines()[1:]).strip()
        return token, rest
    return None, text