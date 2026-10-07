"""Tests for the generated narrative.

Generated prose fails quietly, so these tests attack it from three sides: the
report must match a reviewed golden file, it must contain no unsubstituted
template holes, and it must not contain a conclusion. That last one is enforced
mechanically -- Stage 1 states values, and judgement is Stage 2's job, because
a Stage 1 that pre-judges biases every agent that reads its output.
"""

from __future__ import annotations

import re

import pytest

from financial_analyst import config
from financial_analyst.tools import anomalies as an
from financial_analyst.reporting import templates as tpl
from financial_analyst.reporting.summary import _StrictContext, _flag_icon, _num, render_report

FIXTURE = "tests/fixtures/expected_summary.md"

#: Words that would turn a measurement into a verdict. Matched case-insensitively
#: as whole words, so "rising" does not trip "rise".
#:
#: "value" is deliberately absent: "enterprise value" and "market value" are
#: technical terms, so banning the bare word would be noise. The words that
#: *are* verdicts -- overvalued, undervalued -- are listed individually.
BANNED_WORDS = (
    "healthy", "unhealthy", "concerning", "alarming", "strong", "weak",
    "outperform", "underperform", "excellent", "poor", "good", "bad",
    "impressive", "disappointing", "risky", "recommend", "attractive",
    "overvalued", "undervalued", "buy", "sell",
)


def contains_word(text: str, word: str) -> bool:
    """Whole-word, case-insensitive match.

    A plain ``in`` check is not good enough here: ``"nan" in "financial"`` is
    True, which made the report look like it were leaking NaN.
    """
    return re.search(rf"\b{re.escape(word)}\b", text.lower()) is not None


@pytest.fixture(scope="module")
def report(metrics, flags):
    return render_report(metrics, flags, source_name="company_financials.csv")


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------
def test_report_has_a_section_per_company(report, dataset):
    for ticker, name in zip(dataset.tickers, ["Acme Corp", "Globex Industries",
                                              "Initech Systems", "Vector Retail Group"]):
        assert f"## {name} ({ticker})" in report
    assert report.startswith("# Stage 1 financial analysis")
    assert report.endswith("\n")


def test_report_states_the_thresholds_it_used(report):
    for threshold in (config.DEBT_TO_EQUITY_THRESHOLD, config.INTEREST_COVERAGE_THRESHOLD,
                      config.NET_DEBT_TO_EBITDA_THRESHOLD):
        assert str(threshold) in report


def test_report_lists_every_flag(report, flags):
    for flag in flags:
        assert flag.rule in report


def test_report_mentions_the_companies_without_flags_honestly(report):
    """Absence of flags must not be dressed up as approval."""
    assert "No flags:" in report
    assert "none of the 14 configured rules matched" in report


# ---------------------------------------------------------------------------
# No holes, no junk
# ---------------------------------------------------------------------------
def test_no_template_placeholder_survives(report):
    assert "{" not in report and "}" not in report


def test_missing_template_key_raises_rather_than_rendering_a_gap():
    with pytest.raises(KeyError):
        tpl.COMPANY_LEVERAGE.format_map(_StrictContext(ticker="AAA"))


def test_a_stray_brace_in_a_template_is_caught(monkeypatch, metrics, flags):
    """The final safety net: nothing un-substituted may reach the reader."""
    monkeypatch.setattr(tpl, "REPORT_TITLE", "# Stage 1 {leftover")
    with pytest.raises(ValueError, match="unsubstituted"):
        render_report(metrics, flags, source_name="company_financials.csv")


def test_no_python_repr_leaks_into_the_prose(report):
    """A missing metric must render as ``n/a``, never as Python's ``None``.

    Only the capitalised form is checked for ``None``: lowercase "none" is
    ordinary English here ("none of the 14 rules matched").
    """
    assert "None" not in report
    for junk in ("nan", "NaN", "NaT", "inf", "Infinity"):
        assert not contains_word(report, junk), f"{junk!r} leaked into the report"


def test_summary_num_helper_refuses_non_numeric_values():
    assert _num(None) is None
    assert _num("abc") is None


def test_flag_icon_falls_back_to_info_for_unknown_severities():
    weird = [an.Flag(ticker="AAA", rule="odd", severity="mystery", message="?")]
    assert _flag_icon(weird) == "🔵"


def test_undefined_values_render_as_na(report):
    """INIT has no P/E and no ROE for FY2022; the report must say so plainly."""
    assert "n/a" in report


def test_report_renders_declined_surfaced_metrics_as_na():
    """A surfaced metric that is undefined must render as ``n/a`` in prose.

    This pins the contract at the report layer: once Stage 1 declines a metric,
    the narrative must not leak ``None`` or invent a stand-in number.
    """
    import pandas as pd

    metrics = pd.DataFrame([
        {
            "ticker": "TEST",
            "company_name": "Test Co",
            "sector": "Tech",
            "fiscal_year": 2024,
            "first_year": 2024,
            "last_year": 2024,
            "years_covered": 1,
            "revenue": 1000.0,
            "revenue_cagr": None,
            "net_margin": 0.10,
            "gross_margin": 0.40,
            "operating_margin": 0.15,
            "ebitda_margin": 0.20,
            "return_on_equity": None,
            "free_cash_flow": 50.0,
            "debt_to_assets": 0.30,
            "debt_to_equity": None,
            "interest_coverage": None,
            "net_debt_to_ebitda": None,
            "current_ratio": 2.0,
            "working_capital": 100.0,
        }
    ])

    rendered = render_report(metrics, [], source_name="one.csv")

    assert "ROE n/a" in rendered
    assert "debt/equity n/a" in rendered
    assert "interest coverage n/a" in rendered
    assert "net debt/EBITDA n/a" in rendered
    assert "None" not in rendered


# ---------------------------------------------------------------------------
# No conclusions
# ---------------------------------------------------------------------------
def test_report_contains_no_verdicts(report):
    for word in BANNED_WORDS:
        assert not contains_word(report, word), f"report asserts {word!r}"


def test_templates_contain_no_verdicts():
    """The rule is enforced at the source, not just on today's output."""
    for name in dir(tpl):
        if name.isupper() and isinstance(getattr(tpl, name), str):
            for word in BANNED_WORDS:
                assert not contains_word(getattr(tpl, name), word), f"{name}: {word!r}"


# ---------------------------------------------------------------------------
# Comparative claims need a comparison
# ---------------------------------------------------------------------------
def test_peer_sentence_is_omitted_for_a_single_company(metrics):
    single = metrics[metrics["ticker"] == "ACME"]
    report = render_report(single, [], source_name="one.csv")
    assert "in the sample" not in report
    assert "## Acme Corp (ACME)" in report


def test_peer_sentence_appears_with_several_companies(report):
    assert "in the sample" in report


# ---------------------------------------------------------------------------
# Golden file
# ---------------------------------------------------------------------------
def test_report_matches_the_reviewed_golden_file(report):
    """Catches any unintended change to numbers, wording or ordering."""
    with open(FIXTURE) as handle:
        expected = handle.read()
    if report != expected:
        import difflib
        diff = "\n".join(
            difflib.unified_diff(
                expected.splitlines(), report.splitlines(),
                "golden", "generated", lineterm="", n=1,
            )
        )
        pytest.fail(f"report drifted from the golden file:\n{diff}")


def test_report_is_byte_for_byte_reproducible(dataset, metrics, flags):
    first = render_report(metrics, flags, source_name="company_financials.csv")
    second = render_report(metrics, flags, source_name="company_financials.csv")
    assert first == second


def test_report_with_no_flags_says_so(metrics):
    report = render_report(metrics, [], source_name="clean.csv")
    assert "No rule returned a flag for any company." in report
