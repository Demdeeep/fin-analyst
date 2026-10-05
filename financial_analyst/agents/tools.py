"""Agent-callable access to Stage 1's output.

An agent may only *cite* numbers that Stage 1 already computed; it may not
recompute them. That is enforced here rather than requested in a prompt: the tool
layer reads ``stage1_metrics.json`` and hands back stored values, so there is no
path by which an agent's arithmetic becomes a number in the output.

That is what makes "Stage 1's artifacts are a stable contract" more than a
slogan, and it means the agents need no pandas and no knowledge of the schema.

The tool-call protocol is a plain text convention rather than native SDK function
calling, so it works identically for every provider and is trivially assertable:

    TOOL: gross_margin, debt_to_assets, revenue_cagr
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from ..tools import registry
from ..tools.metrics import COMPANY_COLUMNS, METRIC_COLUMNS, REPORTED_COLUMNS
from ..utils import formatting as fmt

#: What an agent may cite: every column Stage 1 emits. This is deliberately
#: *not* :data:`financial_analyst.tools.registry.METRICS`, which catalogues
#: calculation *functions*. Those are different things -- ``cagr`` is the function
#: behind the ``revenue_cagr`` column, and ``free_cash_flow`` is a carried-through
#: figure with no ratio behind it -- and conflating them made perfectly good
#: citations look like hallucinations.
CITEABLE_METRICS: frozenset[str] = frozenset(
    METRIC_COLUMNS + REPORTED_COLUMNS + COMPANY_COLUMNS
)

#: How an agent asks for tools. Deliberately *not* anchored to the start of the
#: line: models routinely write "I'll need these. TOOL: a, b" rather than
#: putting the request on its own line, and rejecting that would silently drop
#: every metric the agent asked for. It still stops at the end of the line, so it
#: cannot swallow the lines that follow, and it uses horizontal whitespace only
#: because ``\s`` would match the newline.
TOOL_REQUEST = re.compile(
    r"TOOL:[ \t]*(?P<names>[A-Za-z0-9_, \t]+?)[ \t]*$", re.MULTILINE
)


@dataclass(frozen=True)
class MetricStore:
    """Stage 1's metrics, indexed for lookup.

    Values stay in Stage 1's units: ratios as decimal fractions, money in USD
    millions. Only the report layer formats, and the agents see the same
    conventions the report uses.
    """

    data: dict[str, dict[str, dict[str, object]]]
    tickers: tuple[str, ...]

    @classmethod
    def from_json(cls, path: str | Path) -> "MetricStore":
        import json

        payload = json.loads(Path(path).read_text())
        data = {
            str(ticker): {str(year): dict(metrics) for year, metrics in years.items()}
            for ticker, years in payload.items()
        }
        return cls(data=data, tickers=tuple(sorted(data)))

    @property
    def empty(self) -> bool:
        return not self.data

    def years(self, ticker: str) -> tuple[str, ...]:
        return tuple(sorted(self.data.get(ticker, {})))

    def latest_year(self, ticker: str) -> Optional[str]:
        years = self.years(ticker)
        return years[-1] if years else None

    def value(self, ticker: str, year: str, metric: str) -> object:
        """The stored value, or ``None`` when undefined or absent.

        ``None`` is preserved rather than coerced: it means "Stage 1 could not
        compute this", and an agent must be told exactly that.
        """
        return self.data.get(ticker, {}).get(year, {}).get(metric)

    def defined_metrics(self, ticker: str, year: str) -> tuple[str, ...]:
        row = self.data.get(ticker, {}).get(year, {})
        return tuple(
            name for name in row
            if row[name] is not None and isinstance(row[name], (int, float))
        )

    def undefined_metrics(self, ticker: str, year: str) -> tuple[str, ...]:
        """Metrics Stage 1 deliberately declined to compute for this year."""
        row = self.data.get(ticker, {}).get(year, {})
        return tuple(name for name in row if row[name] is None)

    def all_numeric_values(self, ticker: str) -> tuple[float, ...]:
        """Every number Stage 1 computed for this company, any year."""
        values: list[float] = []
        for row in self.data.get(ticker, {}).values():
            for value in row.values():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    values.append(float(value))
        return tuple(values)


@dataclass(frozen=True)
class ToolCatalogue:
    """What an agent may ask for, and what those things mean."""

    entries: tuple[dict, ...] = ()

    @classmethod
    def build(cls) -> "ToolCatalogue":
        return cls(entries=tuple(registry.describe()))

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(entry["name"] for entry in self.entries)

    def render(self) -> str:
        """A compact catalogue for the system prompt."""
        lines = ["| metric | meaning | direction |", "| --- | --- | --- |"]
        for entry in self.entries:
            lines.append(
                f"| `{entry['name']}` | {entry['description']} | {entry['direction']} |"
            )
        return "\n".join(lines)


@dataclass(frozen=True)
class ToolObservation:
    """What the agent receives after asking for tools."""

    requested: tuple[str, ...]
    values: dict[str, str]
    undefined: tuple[str, ...]
    unknown: tuple[str, ...]

    def render(self) -> str:
        """What turn two actually sees.

        An unknown name must not suppress the metrics that *did* resolve. The
        model is not required to get the catalogue right on the first try, and
        discarding seven good values because one name was wrong hands turn two
        nothing at all -- which it then quite reasonably reports as "I have no
        metrics to analyse", with no figure in the sentence for verification to
        catch. The correction is shown alongside the values, not instead of them.
        """
        if not self.values and not self.undefined and self.unknown:
            return (
                "Unknown metrics: " + ", ".join(self.unknown)
                + ". Choose only from the catalogue."
            )

        lines: list[str] = []
        if self.unknown:
            lines += [
                "Not in the catalogue, and therefore not supplied: "
                + ", ".join(self.unknown),
                "",
            ]
        if self.values:
            lines.append("Metric values, most recent reported year:")
            lines.append("")
            lines += [f"  {name}: {value}" for name, value in self.values.items()]
        if self.undefined:
            lines += [
                "",
                "Undefined -- Stage 1 could not compute these. Do not estimate "
                "them and do not substitute another metric:",
                "  " + ", ".join(self.undefined),
            ]
        return "\n".join(lines)


def parse_tool_request(text: str) -> tuple[str, ...]:
    """Pull metric names out of a ``TOOL:`` line.

    An empty tuple is a valid answer: the agent may simply write from the flag
    summary it was given.
    """
    match = TOOL_REQUEST.search(text or "")
    if not match:
        return ()
    names: list[str] = []
    for part in match.group("names").split(","):
        name = part.strip()
        if name and name not in names:
            names.append(name)
    return tuple(names)


def execute(
    request: Iterable[str], store: MetricStore, ticker: str, year: Optional[str] = None
) -> ToolObservation:
    """Look the requested metrics up for ``ticker``.

    Unknown names are reported rather than raised: a model that hallucinates a
    metric name is a finding about the model, not a reason to abort the run.
    """
    year = year or store.latest_year(ticker)
    values: dict[str, str] = {}
    undefined: list[str] = []
    unknown: list[str] = []

    for name in request:
        if name not in CITEABLE_METRICS:
            unknown.append(name)
            continue
        if year is None:
            undefined.append(name)
            continue
        raw = store.value(ticker, year, name)
        if raw is None:
            undefined.append(name)
        elif isinstance(raw, (int, float)) and not isinstance(raw, bool):
            values[name] = _render(name, float(raw))
        else:
            values[name] = str(raw)

    return ToolObservation(
        requested=tuple(request),
        values=values,
        undefined=tuple(undefined),
        unknown=tuple(unknown),
    )


#: Rendering families. Percentage is the *fallback* on purpose: almost every
#: Stage 1 metric column is a decimal fraction, so defaulting to anything else
#: silently prints `0.1358` where the reader expects `13.6%`.
_MONEY = {
    "revenue", "cogs", "gross_profit", "operating_income", "net_income", "ebitda",
    "free_cash_flow", "cash_from_operations", "capex", "total_debt", "net_debt",
    "working_capital", "total_assets", "total_liabilities", "total_equity",
    "cash_and_equivalents", "inventory", "accounts_receivable", "accounts_payable",
    "total_current_assets", "total_current_liabilities", "short_term_debt",
    "long_term_debt", "nopat", "market_cap", "enterprise_value",
}
_UNITS = {"earnings_per_share", "book_value_per_share"}
#: Ratios quoted as multiples rather than percentages.
_MULTIPLES = {
    "debt_to_equity", "interest_coverage", "net_debt_to_ebitda", "current_ratio",
    "quick_ratio", "cash_ratio", "ev_to_ebitda", "price_to_earnings",
    "price_to_book", "cash_conversion_cycle", "inventory_turnover",
    "receivables_turnover", "payables_turnover", "asset_turnover",
    "equity_multiplier",
}
#: Day counts, quoted plainly.
_DAYS = {
    "days_inventory_outstanding", "days_sales_outstanding",
    "days_payables_outstanding",
}


def _render(name: str, value: float) -> str:
    if name in _DAYS:
        return f"{value:,.0f} days"
    if name in _MULTIPLES:
        return fmt.multiple(value)
    if name in _MONEY:
        return fmt.money(value)
    if name in _UNITS:
        return fmt.number(value, digits=2)
    return fmt.pct(value)

