"""Small text helpers shared by the agents.

Kept separate from the agents themselves so that prompt *assembly* can be tested
without an LLM client, and so the stage can reuse the same rendering of a
company without importing an agent.
"""

from __future__ import annotations

from typing import Optional, Sequence

from ..utils import formatting as fmt
from . import prompts
from .tools import MetricStore


def company_line(store: MetricStore, ticker: str, year: Optional[str] = None) -> dict:
    """Identity fields for the prompt, with honest fallbacks.

    An agent told "Sector: n/a" can still reason; an agent told "Sector: None"
    may quote the literal string.
    """
    row = store.data.get(ticker, {}).get(
        year or store.latest_year(ticker) or "", {}
    )
    name = row.get("company_name") or ticker
    sector = row.get("sector") or "not stated"
    return {"company_name": str(name), "sector": str(sector)}


def span_of(store: MetricStore, ticker: str) -> str:
    """``FY2020-FY2024``, or a single year, or a note that there is nothing."""
    years = store.years(ticker)
    if not years:
        return "no data"
    if len(years) == 1:
        return f"FY{years[0]} only"
    return f"FY{years[0]}-FY{years[-1]} ({len(years)} years)"


def flags_section(flags: Sequence[str]) -> str:
    """The flags block, or an explicit note that the rules found nothing.

    The wording matters: "no flags" must not read as "no problems". Stage 1's
    report says the same thing, and an agent that reads it as a clean bill of
    health will write a clean bill of health.
    """
    if not flags:
        return prompts.ANALYST_FLAGS_NONE
    lines = "\n".join(f"  {flag}" for flag in flags)
    return prompts.ANALYST_FLAGS_PRESENT.format(flag_lines=lines)
