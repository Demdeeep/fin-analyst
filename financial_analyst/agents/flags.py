"""Stage 1's flags as structured records.

Kept structured all the way to the critic and rendered to prose only at the
prompt boundary. That boundary matters: if the critic received pre-rendered
strings it would have to string-match on English it did not write, which is a
fuzzy operation on text that changes with every wording tweak.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from .. import config
from ..utils import formatting as fmt


@dataclass(frozen=True)
class FlagRef:
    """One anomaly Stage 1 raised, with enough structure to reason about."""

    ticker: str
    rule: str
    severity: str
    message: str
    fiscal_year: Optional[int] = None
    value: Optional[float] = None

    @property
    def year_label(self) -> str:
        return f"FY{self.fiscal_year}" if self.fiscal_year else "n/a"

    @property
    def must_address(self) -> bool:
        return self.severity in config.MUST_ADDRESS_SEVERITIES

    @property
    def advisory(self) -> bool:
        return self.severity in config.ADVISORY_SEVERITIES

    def render(self) -> str:
        """One line, as the analyst sees it."""
        return f"[{self.severity}] {self.rule} {self.year_label}: {self.message}"

    def signals(self) -> tuple[str, ...]:
        """Tokens that would show this flag was engaged with, cheapest first.

        The rule id is the strong signal, because the analyst prompt asks for it
        explicitly -- that turns "did they address this" from fuzzy text
        matching into a stated protocol. The flag's own value is the fallback for
        an analysis that paraphrases without citing the rule name.

        Deliberately *not* derived from every number in the message: threshold
        text like "below the 1.0x threshold" appears in nearly every flag, so
        matching on it would mark almost anything as addressed.
        """
        tokens = [self.rule, self.rule.replace("_", " ")]
        if self.value is not None:
            tokens.append(f"{abs(self.value):g}")
            if self.value < 0:
                tokens.append(f"{self.value:g}")
        return tuple(tokens)

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "rule": self.rule,
            "severity": self.severity,
            "fiscal_year": self.fiscal_year,
            "message": self.message,
        }


def load_flag_refs(path: str | Path) -> dict[str, tuple[FlagRef, ...]]:
    """Read ``stage1_flags.csv`` into structured records, grouped by ticker.

    Order follows the file, which Stage 1 wrote most-severe-first, so the order
    the analyst sees is the order worth reading in.
    """
    import pandas as pd

    source = Path(path)
    if not source.exists():
        return {}
    frame = pd.read_csv(source)
    grouped: dict[str, list[FlagRef]] = {}
    for row in frame.to_dict("records"):
        year = row.get("fiscal_year")
        raw_value = row.get("value")
        value = None if raw_value is None or pd.isna(raw_value) else float(raw_value)
        flag = FlagRef(
            ticker=str(row["ticker"]),
            rule=str(row["rule"]),
            severity=str(row["severity"]),
            message=str(row["message"]),
            fiscal_year=None if year is None or pd.isna(year) else int(year),
            value=value,
        )
        grouped.setdefault(flag.ticker, []).append(flag)
    return {ticker: tuple(flags) for ticker, flags in grouped.items()}


def render_section(flags: Sequence) -> str:
    """The flags block for a prompt, or an explicit note that none were raised.

    Accepts :class:`FlagRef` records and plain strings together, because the
    orchestrator appends a prose note to the flag list when it retries. Rendering
    by type rather than assuming one shape keeps that path working.

    The wording matters: "no flags" must not read as "no problems". Stage 1's
    report says the same thing, and an agent that reads it as a clean bill of
    health will write one.
    """
    from . import prompts

    if not flags:
        return prompts.ANALYST_FLAGS_NONE
    lines = "\n".join(
        f"  {flag.render() if isinstance(flag, FlagRef) else flag}"
        for flag in flags
    )
    return prompts.ANALYST_FLAGS_PRESENT.format(flag_lines=lines)