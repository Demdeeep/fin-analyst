"""Cross-company comparison: one agent, against peers.

Stage 2's analyst answers "how is this company doing". This one answers "how is
it doing *relative to the others*", which needs a different tool: peer medians,
gaps and direction-aware ranks.

Those are all computed in :mod:`financial_analyst.agents.peers`, not by the
model. That is the same bargain as the critic -- the arithmetic is ours, the
judgement is theirs -- and it matters more here, because a model asked to
compute a gap-to-median will produce a plausible one whether or not the peer
group is big enough for a median to mean anything.

So the agent is told, explicitly, that a "not comparable" row is a refusal and
must be reported as such. The failure this guards against is an analyst writing
"ACME ranks first among technology peers" over a group of two.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from . import peers, prompts
from .flags import FlagRef
from .providers.base import LLMClient
from .tools import MetricStore
from .utils_text import company_line, flags_section, span_of

#: Metrics worth comparing by default. Ratios and growth, not absolutes: ranking
#: companies by revenue tells the reader nothing they cannot already see.
DEFAULT_METRICS = (
    "revenue_cagr", "gross_margin", "net_margin", "return_on_equity",
    "debt_to_assets", "interest_coverage",
)


@dataclass(frozen=True)
class ComparisonResult:
    """One company's peer comparison, with the table it was drawn from."""

    ticker: str
    narrative: str
    rows: tuple[peers.PeerComparison, ...]
    group: str = ""

    @property
    def computable(self) -> tuple[peers.PeerComparison, ...]:
        return tuple(row for row in self.rows if row.computable)

    @property
    def declined(self) -> tuple[peers.PeerComparison, ...]:
        return tuple(row for row in self.rows if not row.computable)

    @property
    def any_comparable(self) -> bool:
        return bool(self.computable)

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "group": self.group,
            "narrative": self.narrative,
            "any_comparable": self.any_comparable,
            "compared": len(self.computable),
            "declined": [row.to_dict() for row in self.declined],
            "rows": [row.to_dict() for row in self.rows],
        }


class ComparatorAgent:
    """Writes a peer comparison from a deterministically computed table."""

    def __init__(
        self,
        client: LLMClient,
        store: MetricStore,
        metrics: Sequence[str] = DEFAULT_METRICS,
    ):
        self.client = client
        self.store = store
        self.metrics = tuple(metrics)

    def compare(
        self,
        ticker: str,
        flags: Sequence[FlagRef] = (),
        year: Optional[str] = None,
    ) -> ComparisonResult:
        year = year or self.store.latest_year(ticker)
        rows = peers.comparisons(self.store, self.metrics, tickers=[ticker], year=year)
        group = rows[0].group if rows else peers.PeerGroup("", "", ())

        narrative = self.client.complete(
            system=prompts.COMPARATOR_SYSTEM,
            user=prompts.COMPARATOR_USER.format(
                company_name=company_line(self.store, ticker, year)["company_name"],
                ticker=ticker,
                sector=company_line(self.store, ticker, year)["sector"],
                span=span_of(self.store, ticker),
                group=group.describe() if group.members else "none",
                peer_table=peers.render_table(rows),
                flags_section=flags_section(flags),
            ),
        )
        return ComparisonResult(
            ticker=ticker,
            narrative=narrative.strip(),
            rows=rows,
            group=group.describe() if group.members else "",
        )
