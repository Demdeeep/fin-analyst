"""Claim verification: every figure an agent writes must trace to Stage 1.

Stage 1 was forbidden from drawing conclusions, so its output could be checked by
reading the code. Stage 2 exists to draw conclusions, which means its output can
no longer be verified that way -- and prose is exactly where a language model
quietly invents a plausible number.

This module is the answer, and it is deliberately mechanical rather than clever.
It extracts every unit-bearing figure from the agent's prose and requires each one
to reconcile with something Stage 1 actually computed. Two failure modes are
caught:

**Unverifiable figure.** "Revenue grew 12%" when the computed CAGR is 9.9%. A
model that hallucinates a number is the most damaging thing it can do in a
financial context, and it is trivially detectable without understanding the
sentence around it.

**Numerified undefined.** An agent writing "return on equity is 4.2%" for a
company with negative equity. Stage 1 returned ``None`` precisely because that
number has no meaning there, and an agent must not fill the hole with a plausible
figure. This is Stage 1's core invariant, re-applied to the layer where holding it
is hardest.

The tolerance is derived from the *displayed* precision, so "9.9%" reconciles with
0.0989 but "12.0%" does not, and rounding is never mistaken for fabrication.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .tools import MetricStore

# Only figures carrying a unit count as claims. Bare integers are ignored: they
# are far more often years, headcounts or list numbering than measurements, and
# a verifier that cries wolf gets ignored.
PERCENT = re.compile(r"(?<![\w.])(?P<sign>[+-]?)(?P<value>\d+(?:\.\d+)?)\s*%")
POINTS = re.compile(r"(?<![\w.])(?P<sign>[+-]?)(?P<value>\d+(?:\.\d+)?)\s*pp\b")
MULTIPLE = re.compile(r"(?<![\w.])(?P<sign>[+-]?)(?P<value>\d+(?:\.\d+)?)x\b")
MONEY = re.compile(r"\$(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*M\b")

#: Phrases that indicate a metric is being discussed, for the undefined check.
_METRIC_WORDS = {
    "return on equity": "return_on_equity",
    "roe": "return_on_equity",
    "return on assets": "return_on_assets",
    "roa": "return_on_assets",
    "gross margin": "gross_margin",
    "net margin": "net_margin",
    "operating margin": "operating_margin",
    "price/earnings": "price_to_earnings",
    "p/e": "price_to_earnings",
    "price to earnings": "price_to_earnings",
    "net debt/ebitda": "net_debt_to_ebitda",
    "debt to equity": "debt_to_equity",
    "debt/equity": "debt_to_equity",
    "debt to assets": "debt_to_assets",
    "debt/assets": "debt_to_assets",
    "current ratio": "current_ratio",
    "interest coverage": "interest_coverage",
}


@dataclass(frozen=True)
class Claim:
    """One figure taken out of the agent's prose."""

    text: str
    value: float
    kind: str          # percent | points | multiple | money
    ticker: str = ""

    def __str__(self) -> str:
        return f"{self.value:g} ({self.kind})"


@dataclass(frozen=True)
class ClaimIssue:
    """Something in the prose that Stage 1's numbers do not support."""

    kind: str          # unverifiable_figure | numerified_undefined
    detail: str
    claim: Optional[Claim] = None

    def __str__(self) -> str:
        return f"[{self.kind}] {self.detail}"


def extract_claims(text: str, ticker: str = "") -> list[Claim]:
    """Every unit-bearing figure in ``text``, normalised to Stage 1's units.

    Percentages and percentage points become decimal fractions, matching how
    Stage 1 stores them; multiples stay as they are; money is already in the
    millions Stage 1 uses.
    """
    claims: list[Claim] = []

    for match in PERCENT.finditer(text):
        sign = -1.0 if match.group("sign") == "-" else 1.0
        claims.append(Claim(
            text=match.group(0).strip(),
            value=sign * float(match.group("value")) / 100.0,
            kind="percent",
            ticker=ticker,
        ))

    for match in POINTS.finditer(text):
        sign = -1.0 if match.group("sign") == "-" else 1.0
        claims.append(Claim(
            text=match.group(0).strip(),
            value=sign * float(match.group("value")) / 100.0,
            kind="points",
            ticker=ticker,
        ))

    for match in MULTIPLE.finditer(text):
        sign = -1.0 if match.group("sign") == "-" else 1.0
        claims.append(Claim(
            text=match.group(0).strip(), value=sign * float(match.group("value")),
            kind="multiple", ticker=ticker,
        ))

    for match in MONEY.finditer(text):
        claims.append(Claim(
            text=match.group(0).strip(),
            value=float(match.group("value").replace(",", "")),
            kind="money",
            ticker=ticker,
        ))

    return claims


def _tolerance(claim: Claim, raw_text: str) -> float:
    """Half of the last displayed digit, in the claim's own unit.

    A figure written to one decimal place can legitimately be off by up to 0.05
    in that unit. Anything beyond that is a different number, not a rounded one.
    """
    body = raw_text.rstrip("%").rstrip("M").strip().rstrip("x").rstrip("pp").strip()
    body = body.lstrip("+-$")
    digits = len(body.split(".")[1]) if "." in body else 0
    step = 0.5 * (10 ** -digits)
    return step / 100.0 if claim.kind in {"percent", "points"} else step


def check_figures(
    narrative: str,
    store: MetricStore,
    ticker: str,
    extra_values: Optional[tuple[float, ...]] = None,
) -> list[ClaimIssue]:
    """Every figure in the narrative must reconcile with a Stage 1 value.

    ``extra_values`` carries figures Stage 1 did not compute but that are still
    legitimate -- peer medians and gaps-to-median from
    :mod:`financial_analyst.agents.peers`, for instance. Without it a comparison
    would be unverifiable by construction, since "6.5pp above the median" is a
    real derived number that appears in no company's metrics row.
    """
    known = list(store.all_numeric_values(ticker))
    known.extend(value for value in (extra_values or ()) if isinstance(value, (int, float)))
    if not known:
        return [ClaimIssue(
            kind="unverifiable_figure",
            detail=(
                f"no Stage 1 metrics available for {ticker!r}, so no figure in the "
                "narrative can be checked"
            ),
        )]

    issues: list[ClaimIssue] = []
    for claim in extract_claims(narrative, ticker):
        tolerance = _tolerance(claim, claim.text)
        if any(abs(claim.value - value) <= tolerance for value in known):
            continue
        nearest = min(known, key=lambda value: abs(value - claim.value))
        issues.append(ClaimIssue(
            kind="unverifiable_figure",
            detail=(
                f"{claim.text!r} does not match any value Stage 1 computed for "
                f"{ticker}; nearest was {nearest:.6g}"
            ),
            claim=claim,
        ))
    return issues


def check_undefined_not_numerified(
    narrative: str, store: MetricStore, ticker: str, year: Optional[str] = None
) -> list[ClaimIssue]:
    """A metric Stage 1 declined to compute must not be given a number.

    Checked per sentence: if a sentence mentions a metric that is ``None`` for
    this company *and* carries a figure, that figure is almost certainly an
    invention filling the hole.
    """
    year = year or store.latest_year(ticker)
    if year is None:
        return []
    undefined = set(store.undefined_metrics(ticker, year))
    if not undefined:
        return []

    issues: list[ClaimIssue] = []
    for sentence in re.split(r"(?<=[.!?])\s+", narrative or ""):
        lowered = sentence.lower()
        mentioned = {
            metric for phrase, metric in _METRIC_WORDS.items()
            if phrase in lowered and metric in undefined
        }
        if not mentioned:
            continue
        figures = extract_claims(sentence, ticker)
        if not figures:
            continue
        issues.append(ClaimIssue(
            kind="numerified_undefined",
            detail=(
                f"sentence mentions {', '.join(sorted(mentioned))}, which Stage 1 "
                f"left undefined for {ticker} FY{year}, yet asserts "
                f"{', '.join(sorted(f.text for f in figures))}"
            ),
            claim=figures[0],
        ))
    return issues


def verify(
    narrative: str,
    store: MetricStore,
    ticker: str,
    year: Optional[str] = None,
    extra_values: Optional[tuple[float, ...]] = None,
) -> list[ClaimIssue]:
    """Run every check. An empty list means the narrative is defensible.

    Set ``extra_values`` when the narrative quotes derived figures -- peer
    medians, gaps to median -- that Stage 1 did not compute for this company.
    """
    return (
        check_figures(narrative, store, ticker, extra_values=extra_values)
        + check_undefined_not_numerified(narrative, store, ticker, year)
    )
