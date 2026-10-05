"""Peer-relative comparison, computed deterministically.

Stage 2's rule was that an agent may only cite numbers Stage 1 computed. Peer
comparison breaks that rule -- a gap-to-median is not in any company's metrics --
so the arithmetic moves *here*, into code, and the model writes prose about
figures it did not produce. Same arrangement as the critic: the maths is ours,
the judgement is theirs.

The hard part is not the arithmetic, it is knowing when **not** to do it. A
median of two companies is not a median, and "rank 1 of 2" is not evidence of
anything. So every comparison carries its group size, and refuses outright below
``MIN_PEERS_FOR_COMPARISON`` rather than emitting a number that merely looks
statistical.

Ranking is direction-aware. `debt_to_assets` ranked first means *least* levered,
so a naive descending sort would crown the most indebted company. A metric with
no direction -- days payable, for instance -- gets no rank at all, because being
top of that table is not an achievement.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, replace
from typing import Optional, Sequence

from .. import config
from ..tools import registry
from .tools import CITEABLE_METRICS, MetricStore


@dataclass(frozen=True)
class PeerGroup:
    """The companies a comparison is drawn against, and how that was decided."""

    name: str
    scope: str
    members: tuple[str, ...]

    @property
    def size(self) -> int:
        return len(self.members)

    def describe(self) -> str:
        return f"{self.name} ({self.scope}, {self.size} companies)"


@dataclass(frozen=True)
class PeerComparison:
    """One metric, one company, against its peer group."""

    ticker: str
    metric: str
    group: PeerGroup
    value: Optional[float] = None
    peer_median: Optional[float] = None
    gap: Optional[float] = None
    rank: Optional[int] = None
    computable: bool = False
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "metric": self.metric,
            "scope": self.group.scope,
            "group": self.group.name,
            "group_size": self.group.size,
            "computable": self.computable,
            "value": self.value,
            "peer_median": self.peer_median,
            "gap": self.gap,
            "rank": self.rank,
            "reason": self.reason,
        }


def _sector(store: MetricStore, ticker: str) -> str:
    year = store.latest_year(ticker) or ""
    value = store.value(ticker, year, "sector")
    return str(value) if value else "unclassified"


def build_groups(
    store: MetricStore,
    scope: str = config.PEER_SCOPE_SECTOR,
    min_peers: int = config.MIN_PEERS_FOR_COMPARISON,
) -> dict[str, PeerGroup]:
    """Assign each company a peer group, preferring sector.

    A company whose sector group is too small falls back to the whole sample,
    and the group's ``scope`` records that it did -- so a reader can tell "top of
    two rivals" from "top of forty".
    """
    if scope not in (config.PEER_SCOPE_SECTOR, config.PEER_SCOPE_SAMPLE):
        raise ValueError(f"unknown peer scope {scope!r}")

    by_sector: dict[str, list[str]] = {}
    for ticker in store.tickers:
        by_sector.setdefault(_sector(store, ticker), []).append(ticker)

    everyone = tuple(sorted(store.tickers))
    groups: dict[str, PeerGroup] = {}
    for ticker in store.tickers:
        if scope == config.PEER_SCOPE_SAMPLE:
            groups[ticker] = PeerGroup("All companies", scope, everyone)
            continue
        members = tuple(sorted(by_sector[_sector(store, ticker)]))
        if len(members) >= min_peers:
            groups[ticker] = PeerGroup(_sector(store, ticker), scope, members)
        else:
            groups[ticker] = PeerGroup(
                "All companies", config.PEER_SCOPE_SAMPLE, everyone
            )
    return groups


def compare(
    store: MetricStore,
    ticker: str,
    metric: str,
    group: PeerGroup,
    year: Optional[str] = None,
    min_peers: int = config.MIN_PEERS_FOR_COMPARISON,
) -> PeerComparison:
    """Compare one metric against a peer group, or explain why it cannot be."""
    year = year or store.latest_year(ticker)
    value = store.value(ticker, year, metric) if year else None

    if metric not in CITEABLE_METRICS:
        # Membership is checked against Stage 1's *output columns*, not the
        # function catalogue: `cagr` is the function behind `revenue_cagr`, and
        # testing against the catalogue rejects perfectly comparable metrics.
        return PeerComparison(
            ticker, metric, group, reason=f"{metric} is not a Stage 1 output column"
        )
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return PeerComparison(
            ticker, metric, group,
            reason=f"Stage 1 did not compute {metric} for {ticker} FY{year}",
        )
    if group.size < min_peers:
        return PeerComparison(
            ticker, metric, group, value=float(value),
            reason=(
                f"only {group.size} compan"
                f"{'y' if group.size == 1 else 'ies'} in {group.name}; "
                f"{min_peers} needed for a comparison"
            ),
        )

    # Peers Stage 1 could not compute this metric for are excluded rather than
    # treated as zero -- the same rule that governs everything else.
    peers: list[float] = []
    for member in group.members:
        if member == ticker:
            continue
        peer_value = store.value(member, store.latest_year(member) or "", metric)
        if isinstance(peer_value, (int, float)) and not isinstance(peer_value, bool):
            peers.append(float(peer_value))

    if not peers:
        return PeerComparison(
            ticker, metric, group, value=float(value),
            reason=f"no peer in {group.name} has a computed {metric}",
        )

    median = statistics.median(peers)
    comparison = PeerComparison(
        ticker=ticker, metric=metric, group=group, value=float(value),
        peer_median=median, gap=float(value) - median, computable=True,
    )
    return _with_rank(comparison, float(value), peers)


def _with_rank(
    comparison: PeerComparison, value: float, peers: list[float]
) -> PeerComparison:
    """Rank within the group, direction-aware.

    ``neutral`` metrics get no rank: being top of a table with no good direction
    is not an achievement, and implying otherwise is the kind of quiet editorial
    this project refuses to do.
    """
    try:
        direction = registry.get(comparison.metric).direction
    except KeyError:
        direction = registry.NEUTRAL
    if direction == registry.NEUTRAL:
        return comparison

    pool = peers + [value]
    ordered = sorted(pool, reverse=(direction == registry.HIGHER_IS_BETTER))
    return replace(comparison, rank=ordered.index(value) + 1)


def compare_metric(
    store: MetricStore,
    ticker: str,
    metric: str,
    year: Optional[str] = None,
    scope: str = config.PEER_SCOPE_SECTOR,
    min_peers: int = config.MIN_PEERS_FOR_COMPARISON,
) -> PeerComparison:
    """Convenience wrapper: build the group, then compare."""
    group = build_groups(store, scope=scope, min_peers=min_peers)[ticker]
    return compare(store, ticker, metric, group, year=year, min_peers=min_peers)


def comparisons(
    store: MetricStore,
    metrics: Sequence[str],
    tickers: Optional[Sequence[str]] = None,
    year: Optional[str] = None,
    scope: str = config.PEER_SCOPE_SECTOR,
) -> tuple[PeerComparison, ...]:
    """Every (company, metric) comparison, for one cohort."""
    groups = build_groups(store, scope=scope)
    selected = tuple(tickers) if tickers else store.tickers
    return tuple(
        compare(store, ticker, metric, groups[ticker], year=year)
        for ticker in selected
        for metric in metrics
    )


def render_table(rows: Sequence[PeerComparison]) -> str:
    """A markdown table for the prompt, including the rows that declined."""
    lines = [
        "| company | metric | value | peer median | gap | rank | group |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        if row.computable:
            lines.append(
                f"| {row.ticker} | {row.metric} | {row.value:.4g} | "
                f"{row.peer_median:.4g} | {row.gap:+.4g} | {row.rank or '-'} | "
                f"{row.group.name} (n={row.group.size}) |"
            )
        else:
            lines.append(
                f"| {row.ticker} | {row.metric} | not comparable | - | - | - | "
                f"{row.group.name} (n={row.group.size}): {row.reason} |"
            )
    return "\n".join(lines)
