"""The analyst agent.

A two-turn loop, which is the minimum that is genuinely agentic while staying
fully deterministic to test:

1. The model is shown the company's identity and Stage 1's flags, and asked which
   metrics it needs. It answers with a ``TOOL:`` line.
2. The orchestrator executes those lookups -- it does not ask the model what the
   numbers are -- and feeds the results back.
3. The model writes the analysis.

The important property is that the model never produces a number. It *requests*
numbers by name and the tool layer supplies them from Stage 1's artifacts, so
there is no arithmetic path from the model into the output. That is a code
guarantee, not a prompt request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from . import prompts
from .providers.base import LLMClient
from .tools import MetricStore, ToolCatalogue, ToolObservation, execute, parse_tool_request
from .utils_text import company_line, flags_section, span_of


@dataclass(frozen=True)
class AnalystResult:
    """What the analyst produced, and what it asked for to get there."""

    ticker: str
    narrative: str
    observation: ToolObservation
    first_turn: str = ""
    turns: int = 2

    @property
    def tools_requested(self) -> tuple[str, ...]:
        return self.observation.requested


class AnalystAgent:
    """Writes one company's analysis from Stage 1's artifacts."""

    def __init__(
        self,
        client: LLMClient,
        store: MetricStore,
        catalogue: Optional[ToolCatalogue] = None,
    ):
        self.client = client
        self.store = store
        self.catalogue = catalogue or ToolCatalogue.build()

    def _system(self) -> str:
        return prompts.ANALYST_SYSTEM + "\n\nAvailable metrics:\n" + self.catalogue.render()

    def analyse(
        self,
        ticker: str,
        flags: Sequence[str] = (),
        year: Optional[str] = None,
    ) -> AnalystResult:
        """Analyse one company.

        ``flags`` are pre-rendered human-readable lines, because the prompt is
        built here and the flag formatting belongs to Stage 1's vocabulary, not
        to this module.
        """
        if self.store.empty:
            raise ValueError("cannot analyse: Stage 1 metrics are empty")

        year = year or self.store.latest_year(ticker)
        line = company_line(self.store, ticker, year)

        first_turn = self.client.complete(
            system=self._system(),
            user=prompts.ANALYST_TURN_ONE.format(
                company_name=line["company_name"],
                ticker=ticker,
                sector=line["sector"],
                span=span_of(self.store, ticker),
                flags_section=flags_section(flags),
            ),
        )

        observation = execute(
            parse_tool_request(first_turn), self.store, ticker, year
        )

        narrative = self.client.complete(
            system=self._system(),
            user=prompts.ANALYST_TURN_TWO.format(
                company_name=line["company_name"],
                ticker=ticker,
                span=span_of(self.store, ticker),
                flags_section=flags_section(flags),
                observation=observation.render(),
            ),
        )

        return AnalystResult(
            ticker=ticker,
            narrative=narrative.strip(),
            observation=observation,
            first_turn=first_turn,
        )
