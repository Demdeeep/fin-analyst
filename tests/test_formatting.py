"""Tests for the prose formatters.

The important behaviour is the negative one: a missing value must render as
``n/a``. Every ``n/a`` case below corresponds to a real situation in the sample
data (INIT's negative equity, GLOB's missing EBITDA, a company with no peers),
and the failure this prevents is a report that prints "ROE: nan%" and looks
like a finding.
"""

from __future__ import annotations

import math

import pytest

from financial_analyst.utils import formatting as fmt

MISSING = [None, float("nan"), float("inf"), float("-inf"), "", "n/a", object()]


# ---------------------------------------------------------------------------
# Percentages
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "value, expected",
    [
        (0.1534, "15.3%"),
        (0.15345, "15.3%"),
        (0.0, "0.0%"),
        (1.0, "100.0%"),
        (-0.0358, "-3.6%"),
        (0.12345, "12.3%"),
    ],
)
def test_pct(value, expected):
    assert fmt.pct(value) == expected


@pytest.mark.parametrize("value", MISSING)
def test_pct_missing_is_na_never_nan(value):
    result = fmt.pct(value)
    assert result == "n/a"
    assert "nan" not in result.lower()
    assert "none" not in result.lower()


def test_pct_respects_precision():
    assert fmt.pct(0.123456, digits=2) == "12.35%"
    assert fmt.pct(0.123456, digits=0) == "12%"


# ---------------------------------------------------------------------------
# Percentage points
# ---------------------------------------------------------------------------
def test_pp_is_signed():
    assert fmt.pp(0.0281) == "+2.8pp"
    assert fmt.pp(-0.061) == "-6.1pp"
    assert fmt.pp(0.0) == "+0.0pp"
    assert fmt.pp(None) == "n/a"


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "value, expected",
    [
        (1750, "$1,750M"),
        (-20, "-$20M"),
        (0, "$0M"),
        (1234.5, "$1,234M"),   # round-half-to-even, as Python's format does
        (None, "n/a"),
    ],
)
def test_money(value, expected):
    assert fmt.money(value) == expected


# ---------------------------------------------------------------------------
# Multiples, plain numbers, spans
# ---------------------------------------------------------------------------
def test_multiple():
    assert fmt.multiple(3.6) == "3.6x"
    assert fmt.multiple(-0.84) == "-0.8x"
    assert fmt.multiple(2.473, digits=2) == "2.47x"
    assert fmt.multiple(None) == "n/a"


def test_number():
    assert fmt.number(5.0) == "5.0"
    assert fmt.number(1_234.567, digits=2) == "1,234.57"
    assert fmt.number(None) == "n/a"


def test_span():
    assert fmt.span(2020, 2024) == "FY2020–FY2024"
    assert fmt.span(2020, 2020) == "FY2020"
    assert fmt.span(None, 2024) == "n/a"


# ---------------------------------------------------------------------------
# Direction
# ---------------------------------------------------------------------------
def test_direction():
    assert fmt.direction(0.12, 0.10) == "rose"
    assert fmt.direction(0.08, 0.10) == "fell"
    assert fmt.direction(0.10, 0.10) == "was unchanged"


def test_direction_ignores_rounding_noise():
    """A 0.04pp wobble is not a trend."""
    assert fmt.direction(0.1002, 0.1000) == "was unchanged"
    assert fmt.direction(0.1006, 0.1000) == "rose"


def test_direction_with_a_missing_input():
    assert fmt.direction(None, 0.10) == "n/a"
    assert fmt.direction(0.10, None) == "n/a"


# ---------------------------------------------------------------------------
# Peer ranking
# ---------------------------------------------------------------------------
def test_peer_rank_is_strictly_comparative():
    peers = [0.36, 0.315, 0.40, 0.44]
    assert fmt.peer_rank(0.44, peers) == "the highest of 4 companies"
    assert fmt.peer_rank(0.315, peers) == "the 4th highest of 4 companies"
    assert fmt.peer_rank(0.40, peers) == "the 2nd highest of 4 companies"


def test_peer_rank_with_one_company_says_so():
    assert fmt.peer_rank(0.44, [0.44]) == "the only company in the sample"


def test_peer_rank_returns_none_when_it_cannot_rank():
    # Nothing to compare against: the caller omits the sentence entirely rather
    # than inventing a rank.
    assert fmt.peer_rank(0.44, []) is None
    assert fmt.peer_rank(None, [0.36, 0.44]) is None
    assert fmt.peer_rank(0.5, [0.36, 0.44]) is None
