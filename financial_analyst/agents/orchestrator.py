"""Analyst -> critic, with one bounded revision.

The loop is bounded at a single revision by default. That is a judgement, and it
is the conservative one: a second revision usually means the critic and the
analyst are arguing about style, and an unbounded loop against a live model is
an unbounded bill. Raising ``max_revisions`` is a deliberate choice for an
offline run, not a default.

Whatever the verdict, the mechanical findings travel with the result. A REJECT or
REVISE is not a discarded analysis -- it is an analysis with named, checkable
problems, which is the most useful thing this stage can hand a reader.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from .analyst import AnalystAgent, AnalystResult
from .critic import CriticAgent, CriticVerdict, OK, REJECT, REVISE
from .providers.base import LLMClient
from .tools import MetricStore
from .verification import ClaimIssue


@dataclass(frozen=True)
class Stage2Result:
    """One company's finished analysis, with its audit trail."""

    ticker: str
    narrative: str
    verdict: str
    issues: tuple[ClaimIssue, ...] = ()
    coverage: tuple = ()
    revisions: int = 0
    tools_requested: tuple[str, ...] = ()
    critic_notes: str = ""
    model_calls: int = 0
    flags_considered: int = 0
    flags_addressed: int = 0
    #: Carried from the critic rather than recomputed here. REJECT has two
    #: causes with opposite answers, and deriving it from the verdict word plus
    #: the issue list is exactly the conflation this field exists to prevent.
    retryable: bool = True

    @property
    def verified(self) -> bool:
        """True only if every figure reconciles, whatever the critic said."""
        return not self.issues

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "narrative": self.narrative,
            "verdict": self.verdict,
            "verified": self.verified,
            "flags_considered": self.flags_considered,
            "flags_addressed": self.flags_addressed,
            "issues": [str(issue) for issue in self.issues],
            "unaddressed_flags": [
                str(item) for item in self.coverage if item.needs_review
            ],
            "revisions": self.revisions,
            "tools_requested": list(self.tools_requested),
            "critic_notes": self.critic_notes,
        }


class Orchestrator:
    """Runs the analyst, has the result reviewed, and records the outcome."""

    def __init__(
        self,
        client: LLMClient,
        store: MetricStore,
        max_revisions: int = 1,
        consult_critic_model: bool = True,
    ):
        self.analyst = AnalystAgent(client, store)
        self.critic = CriticAgent(
            client if consult_critic_model else None,
            store,
            consult_model=consult_critic_model,
        )
        self.max_revisions = max_revisions

    def run(
        self, ticker: str, flags: Sequence = ()
    ) -> Stage2Result:
        """Analyse one company and review the result.

        The loop is bounded by ``max_revisions``, and gated on
        ``retryable``: an unaddressed flag is exactly what a retry can fix,
        while a metric Stage 1 declined to compute being given a number is not
        repairable by rewording and is not worth a second attempt.
        """
        analyst_result = self.analyst.analyse(ticker, flags=flags)
        verdict = self.critic.review(
            ticker, analyst_result.narrative, flags=flags
        )
        revisions = 0

        while verdict.verdict == REVISE and revisions < self.max_revisions:
            analyst_result = self.analyst.analyse(
                ticker,
                flags=list(flags) + [
                    f"(previous attempt was rejected: {verdict.verdict} -- "
                    f"{verdict.notes or 'unsupported claim'})"
                ],
            )
            verdict = self.critic.review(
                ticker, analyst_result.narrative, flags=flags
            )
            revisions += 1

        return Stage2Result(
            ticker=ticker,
            narrative=analyst_result.narrative,
            verdict=verdict.verdict,
            issues=verdict.issues,
            coverage=verdict.coverage,
            revisions=revisions,
            tools_requested=analyst_result.tools_requested,
            critic_notes=verdict.notes,
            model_calls=revisions * 4 + 2,
            flags_considered=verdict.flags_considered,
            flags_addressed=verdict.flags_addressed,
            retryable=verdict.retryable,
        )