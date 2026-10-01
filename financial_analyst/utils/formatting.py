"""Rendering helpers for the Stage 1 narrative.

One rule governs this module: **a missing value renders as ``"n/a"`` and never
as anything else.** Not ``"nan"``, not ``"None"``, not a silent blank. A report
that prints "return on equity: nan%" is worse than one that prints "n/a",
because the first looks like a finding.

Ratios arrive as decimal fractions (``0.153``) and are formatted only here, at
the edge, so the data layer keeps exactly one convention.
"""

from __future__ import annotations

from typing import Optional

NOT_AVAILABLE = "n/a"


def _value(value: object) -> Optional[float]:
    """``float`` or ``None`` for anything missing or non-finite."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def pct(value: object, digits: int = 1) -> str:
    """A decimal fraction as a percentage string.

    >>> pct(0.1534)
    '15.3%'
    >>> pct(None)
    'n/a'
    >>> pct(float("nan"))
    'n/a'
    """
    number = _value(value)
    if number is None:
        return NOT_AVAILABLE
    return f"{number * 100:.{digits}f}%"


def pp(value: object, digits: int = 1) -> str:
    """A change between two decimal fractions, in percentage points, signed.

    >>> pp(0.0281)
    '+2.8pp'
    >>> pp(-0.061)
    '-6.1pp'
    """
    number = _value(value)
    if number is None:
        return NOT_AVAILABLE
    return f"{number * 100:+.{digits}f}pp"


def money(value: object, digits: int = 0) -> str:
    """A USD-millions figure.

    >>> money(1750)
    '$1,750M'
    >>> money(-20)
    '-$20M'
    >>> money(None)
    'n/a'
    """
    number = _value(value)
    if number is None:
        return NOT_AVAILABLE
    sign = "-" if number < 0 else ""
    return f"{sign}${abs(number):,.{digits}f}M"


def multiple(value: object, digits: int = 1) -> str:
    """A leverage/coverage ratio.

    >>> multiple(3.6)
    '3.6x'
    """
    number = _value(value)
    if number is None:
        return NOT_AVAILABLE
    return f"{number:.{digits}f}x"


def number(value: object, digits: int = 1) -> str:
    """A plain number, for share counts and days."""
    result = _value(value)
    if result is None:
        return NOT_AVAILABLE
    return f"{result:,.{digits}f}"


def span(first_year: object, last_year: object) -> str:
    """``FY2020–FY2024``, collapsing to a single year when there is only one.

    >>> span(2020, 2024)
    'FY2020–FY2024'
    >>> span(2020, 2020)
    'FY2020'
    """
    first, last = _value(first_year), _value(last_year)
    if first is None or last is None:
        return NOT_AVAILABLE
    first_year_i, last_year_i = int(first), int(last)
    if first_year_i == last_year_i:
        return f"FY{first_year_i}"
    return f"FY{first_year_i}–FY{last_year_i}"


def direction(current: object, prior: object, tolerance: float = 0.0005) -> str:
    """``"rose"`` / ``"fell"`` / ``"was unchanged"`` between two ratios.

    ``tolerance`` absorbs rounding noise so that a 0.04pp wobble is not reported
    as a change.

    >>> direction(0.12, 0.10)
    'rose'
    >>> direction(0.10, 0.10)
    'was unchanged'
    """
    now, before = _value(current), _value(prior)
    if now is None or before is None:
        return NOT_AVAILABLE
    change = now - before
    if abs(change) <= tolerance:
        return "was unchanged"
    return "rose" if change > 0 else "fell"


def peer_rank(value: object, peers: list, higher_is_better: bool = True) -> Optional[str]:
    """A strictly comparative rank sentence, or ``None`` if unrankable.

    With a single company there is nothing to compare against, and inventing a
    ranking would be noise -- so the caller omits the sentence entirely.

    >>> peer_rank(0.44, [0.36, 0.31, 0.44])
    'the highest of 4 companies'
    >>> peer_rank(0.30, [0.30], higher_is_better=True)
    'the only company in the sample'
    """
    number_value = _value(value)
    candidates = [_value(item) for item in peers]
    candidates = [item for item in candidates if item is not None]
    if number_value is None or not candidates:
        return None
    if len(candidates) == 1:
        return "the only company in the sample"
    ordered = sorted(candidates, reverse=higher_is_better)
    if number_value not in ordered:
        return None
    rank = ordered.index(number_value) + 1
    extreme = "highest" if higher_is_better else "lowest"
    if rank == 1:
        return f"the {extreme} of {len(ordered)} companies"
    suffix = {2: "2nd", 3: "3rd"}.get(rank, f"{rank}th")
    return f"the {suffix} highest of {len(ordered)} companies" if higher_is_better else (
        f"the {suffix} lowest of {len(ordered)} companies"
    )
