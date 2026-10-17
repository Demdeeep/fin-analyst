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
from .citations import CitationIssue, check_filing_citations
from .critic import CriticAgent, CriticVerdict, OK, REJECT, REVISE
from ..filings import EvidenceChunk
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
    filing_sources: tuple[dict, ...] = ()
    citation_issues: tuple[CitationIssue, ...] = ()

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
            "filing_sources": list(self.filing_sources),
            "citation_issues": [str(issue) for issue in self.citation_issues],
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
        self,
        ticker: str,
        flags: Sequence = (),
        filing_evidence: Sequence[EvidenceChunk] = (),
        filing_query: Optional[str] = None,
    ) -> Stage2Result:
        """Analyse one company and review the result.

        The loop is bounded by ``max_revisions``, and gated on
        ``retryable``: an unaddressed flag is exactly what a retry can fix,
        while a metric Stage 1 declined to compute being given a number is not
        repairable by rewording and is not worth a second attempt.
        """
        analyst_result = self.analyst.analyse(
            ticker,
            flags=flags,
            filing_evidence=filing_evidence,
            filing_query=filing_query,
        )
        verdict = self.critic.review(
            ticker, analyst_result.narrative, flags=flags
        )
        revisions = 0

        while verdict.verdict == REVISE and verdict.retryable and revisions < self.max_revisions:
            unresolved = [
                f"{item.flag.rule} {item.flag.year_label}"
                for item in verdict.coverage
                if item.needs_review and item.flag.must_address
            ]
            revision_note = (
                "(previous attempt was rejected: "
                f"{verdict.verdict} -- {verdict.notes or 'unsupported claim'})"
            )
            if unresolved:
                revision_note += (
                    " Must address these flags by rule id in the revision: "
                    + ", ".join(unresolved)
                    + ". If one cannot be resolved from Stage 1's values, say that plainly rather than omitting it."
                )
            analyst_result = self.analyst.analyse(
                ticker,
                flags=list(flags) + [revision_note],
                filing_evidence=filing_evidence,
                filing_query=filing_query,
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
            filing_sources=analyst_result.filing_sources,
            citation_issues=tuple(check_filing_citations(
                analyst_result.narrative,
                (source["source_id"] for source in analyst_result.filing_sources),
            )),
        )
