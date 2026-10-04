"""The critic agent.

Two stages, and the order matters:

**Mechanical first.** :mod:`financial_analyst.agents.verification` checks every
figure against Stage 1's numbers before the model is consulted at all. This is
free, deterministic, and catches the failure that actually matters. Asking a
model whether 3.2x matches the metrics would be both expensive and less
reliable than comparing the numbers directly.

**Judgement second.** The model is then asked about the things arithmetic cannot
check: whether a conclusion rests on anything, whether a critical flag was
buried, whether one year was presented as a trend.

A deterministic finding is never overridden by the model. If the figures do not
reconcile, the verdict is not "OK" regardless of what the critic says -- a
language model asked to check arithmetic will often confirm whatever it is
shown.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from . import prompts
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
    """The critic's conclusion, with the mechanical findings attached."""

    ticker: str
    verdict: str
    issues: tuple[ClaimIssue, ...] = ()
    notes: str = ""
    model_consulted: bool = False

    @property
    def clean(self) -> bool:
        return self.verdict == OK and not self.issues

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "verdict": self.verdict,
            "issues": [str(issue) for issue in self.issues],
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


class CriticAgent:
    """Reviews one narrative against the numbers it was allowed to use."""

    def __init__(
        self,
        client: Optional[LLMClient],
        store: MetricStore,
        consult_model: bool = True,
    ):
        # A None client is a supported configuration: the mechanical check alone
        # is a complete safety net, and is what CI should rely on.
        self.client = client
        self.store = store
        self.consult_model = consult_model and client is not None

    def review(
        self, ticker: str, narrative: str, year: Optional[str] = None
    ) -> CriticVerdict:
        issues = tuple(verify(narrative, self.store, ticker, year))

        # Deterministic findings are not negotiable, whatever the model says.
        if any(issue.kind == "numerified_undefined" for issue in issues):
            return CriticVerdict(
                ticker=ticker, verdict=REJECT, issues=issues,
                notes="a metric Stage 1 declined to compute was given a number",
            )

        notes = ""
        verdict = REVISE if issues else OK
        if self.consult_model:
            reply = self.client.complete(
                system=prompts.CRITIC_SYSTEM,
                user=prompts.CRITIC_USER.format(
                    company_name=company_line(self.store, ticker, year)["company_name"],
                    ticker=ticker,
                    verification_section=_render_verification(issues),
                    narrative=narrative,
                ),
            )
            parsed, notes = _parse_verdict(reply)
            if parsed is None:
                # No usable verdict. That is not evidence against the analysis,
                # so fall back to the mechanical result and say so, rather than
                # inventing a revision the analyst cannot act on.
                notes = (
                    "critic model returned no usable verdict; this result is "
                    "mechanically verified but not model-reviewed"
                )
            else:
                verdict = parsed
                # A clean mechanical check cannot be talked out of by the model,
                # but a failed one can be made worse.
                if issues and verdict == OK:
                    verdict = REVISE

        return CriticVerdict(
            ticker=ticker,
            verdict=verdict,
            issues=issues,
            notes=notes,
            model_consulted=self.consult_model,
        )


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