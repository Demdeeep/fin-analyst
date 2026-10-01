"""Tests for the anomaly rules.

Two obligations per rule: it must fire on data that warrants it, and it must
stay silent on data that does not. The second half matters as much as the first
-- a rule that flags everything trains its reader to ignore it, and that is how
a genuine distress signal gets missed.

Rules are tested against purpose-built minimal frames rather than the sample
CSV, so a failure points at the rule rather than at the data. The sample data
is still used, as an integration check that the rules fire where intended.
"""

from __future__ import annotations

import re

import pandas as pd
import pytest

from financial_analyst import config
from financial_analyst.tools import anomalies as an

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
QUIET = {
    "revenue": 1000.0, "revenue_growth": 0.05, "revenue_cagr": 0.05,
    "net_margin": 0.10, "gross_margin": 0.40, "ebitda_margin": 0.15,
    "free_cash_flow": 50.0, "debt_to_equity": 0.5, "interest_coverage": 5.0,
    "net_debt_to_ebitda": 1.0, "working_capital": 100.0, "total_equity": 600.0,
    "first_year": 2020, "last_year": 2024,
}


def metric_row(**overrides) -> dict:
    """One quiet metrics row, with only the fields a rule might read."""
    row = {"ticker": "AAA", "fiscal_year": 2020}
    row.update(QUIET)
    row.update(overrides)
    return row


def context_for(rows, *, source_rows=None, **kwargs) -> an.AnalysisContext:
    metrics = pd.DataFrame(rows)
    source = pd.DataFrame(source_rows if source_rows is not None else rows)
    return an.AnalysisContext(metrics=metrics, source=source, raw=source, **kwargs)


def rules_that_fire(ctx, rule) -> int:
    function, _, _ = an.RULES[rule]
    return len(function(ctx))


# ---------------------------------------------------------------------------
# Every rule must fire on data that warrants it
# ---------------------------------------------------------------------------
FIRE_CASES = {
    "missing_fiscal_year": dict(
        rows=[metric_row(fiscal_year=2020), metric_row(fiscal_year=2022)],
    ),
    "identity_mismatch": dict(
        rows=[metric_row()],
        source_rows=[metric_row(total_assets=1000.0, total_liabilities=400.0,
                                total_equity=500.0)],
    ),
    "negative_equity": dict(rows=[metric_row(total_equity=-20.0)]),
    "imputed_column": dict(
        rows=[metric_row()],
        source_rows=[metric_row(ebitda=None)],
        kwargs={"imputed_columns": ("ebitda",)},
    ),
    "loader_warning": dict(
        rows=[metric_row()],
        kwargs={"loader_warnings": ("AAA:2020: something looked odd",)},
    ),
    "revenue_decline": dict(rows=[metric_row(revenue_growth=-0.118)]),
    "margin_compression": dict(
        rows=[
            metric_row(fiscal_year=2020, net_margin=0.10, gross_margin=0.40),
            metric_row(fiscal_year=2021, net_margin=0.014, gross_margin=0.40),
        ],
    ),
    "negative_cagr": dict(rows=[metric_row(revenue_cagr=-0.09)]),
    "fcf_negative": dict(
        rows=[
            metric_row(fiscal_year=2020, free_cash_flow=-5.0),
            metric_row(fiscal_year=2021, free_cash_flow=-23.0),
        ],
    ),
    "ebitda_margin_collapse": dict(
        rows=[
            metric_row(fiscal_year=2020, ebitda_margin=0.10),
            metric_row(fiscal_year=2021, ebitda_margin=0.004),
        ],
    ),
    "high_debt_to_equity": dict(rows=[metric_row(debt_to_equity=3.22)]),
    "low_interest_coverage": dict(rows=[metric_row(interest_coverage=0.36)]),
    "negative_working_capital": dict(rows=[metric_row(working_capital=-49.0)]),
    "high_net_debt_to_ebitda": dict(rows=[metric_row(net_debt_to_ebitda=25.56)]),
}


@pytest.mark.parametrize("rule", sorted(FIRE_CASES))
def test_rule_fires_on_data_that_warrants_it(rule):
    case = FIRE_CASES[rule]
    kwargs = case.get("kwargs", {})
    ctx = context_for(case["rows"], source_rows=case.get("source_rows"), **kwargs)
    assert rules_that_fire(ctx, rule) > 0, f"{rule} did not fire"


def test_every_registered_rule_has_a_fire_case():
    """A new rule cannot be added without a test that exercises it."""
    assert set(FIRE_CASES) == set(an.RULES)


# ---------------------------------------------------------------------------
# ... and must stay silent otherwise
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("rule", sorted(an.RULES))
def test_rule_is_silent_on_quiet_data(rule):
    ctx = context_for([metric_row()])
    assert rules_that_fire(ctx, rule) == 0, f"{rule} fired on a quiet row"


def test_healthy_two_year_company_raises_nothing_at_all(healthy_context):
    """The broad negative case: a well-run company produces no output at all."""
    assert an.run_anomalies(healthy_context) == []


def test_thresholds_are_inclusive_at_the_boundary():
    """Exactly at the threshold is not "above" it."""
    ctx = context_for([metric_row(
        debt_to_equity=config.DEBT_TO_EQUITY_THRESHOLD,
        interest_coverage=config.INTEREST_COVERAGE_THRESHOLD,
        net_debt_to_ebitda=config.NET_DEBT_TO_EBITDA_THRESHOLD,
    )])
    assert an.run_anomalies(ctx) == []


def test_thresholds_fire_just_past_the_boundary():
    epsilon = 0.001
    ctx = context_for([metric_row(
        debt_to_equity=config.DEBT_TO_EQUITY_THRESHOLD + epsilon,
        interest_coverage=config.INTEREST_COVERAGE_THRESHOLD - epsilon,
        net_debt_to_ebitda=config.NET_DEBT_TO_EBITDA_THRESHOLD + epsilon,
    )])
    fired = {flag.rule for flag in an.run_anomalies(ctx)}
    assert fired == {
        "high_debt_to_equity", "low_interest_coverage", "high_net_debt_to_ebitda"
    }


def test_margin_compression_respects_the_configured_threshold():
    just_under = config.MARGIN_COMPRESSION_THRESHOLD_PP - 0.1
    rows = [
        metric_row(fiscal_year=2020, net_margin=0.30),
        metric_row(fiscal_year=2021, net_margin=0.30 - just_under / 100),
    ]
    assert an.margin_compression(context_for(rows)) == []

    over = config.MARGIN_COMPRESSION_THRESHOLD_PP + 0.1
    rows = [
        metric_row(fiscal_year=2020, net_margin=0.30),
        metric_row(fiscal_year=2021, net_margin=0.30 - over / 100),
    ]
    assert len(an.margin_compression(context_for(rows))) == 1


def test_one_negative_fcf_year_is_not_a_trend():
    """`fcf_negative` means a run of them, not a single bad year."""
    rows = [
        metric_row(fiscal_year=2020, free_cash_flow=-5.0),
        metric_row(fiscal_year=2021, free_cash_flow=50.0),
    ]
    assert an.fcf_negative(context_for(rows)) == []


def test_negative_fcf_streak_flags_every_year_from_the_second():
    rows = [
        metric_row(fiscal_year=2020, free_cash_flow=-5.0),
        metric_row(fiscal_year=2021, free_cash_flow=-23.0),
        metric_row(fiscal_year=2022, free_cash_flow=-47.0),
        metric_row(fiscal_year=2023, free_cash_flow=50.0),   # streak resets
        metric_row(fiscal_year=2024, free_cash_flow=-10.0),  # first of a new run
    ]
    years = [flag.fiscal_year for flag in an.fcf_negative(context_for(rows))]
    assert years == [2021, 2022]


def test_ordinals_read_like_prose():
    """Twelve consecutive negative years exercise 2nd, 3rd, 4th, 11th and 12th."""
    rows = [
        metric_row(fiscal_year=2020 + offset, free_cash_flow=-float(offset + 1))
        for offset in range(12)
    ]
    messages = " ".join(flag.message for flag in an.fcf_negative(context_for(rows)))
    for ordinal in ("2nd", "3rd", "4th", "11th", "12th"):
        assert ordinal in messages, f"missing {ordinal}"
    # Whole-word: "1th" is a substring of "11th", which is correct output.
    assert re.search(r"\b1th\b", messages) is None
    assert re.search(r"\b2th\b", messages) is None


def test_undefined_metrics_do_not_trigger_threshold_rules():
    """A missing ratio is not a breach."""
    ctx = context_for([metric_row(
        debt_to_equity=None, interest_coverage=None, net_debt_to_ebitda=None,
    )])
    fired = {flag.rule for flag in an.run_anomalies(ctx)}
    assert fired.isdisjoint({
        "high_debt_to_equity", "low_interest_coverage", "high_net_debt_to_ebitda"
    })


def test_flags_are_sorted_most_severe_first(flags):
    rank = {name: index for index, name in enumerate(an.SEVERITY_ORDER)}
    severities = [flag.severity for flag in flags]
    assert severities == sorted(severities, key=lambda value: rank[value])


def test_flag_run_is_deterministic(context):
    first = [flag.to_dict() for flag in an.run_anomalies(context)]
    second = [flag.to_dict() for flag in an.run_anomalies(context)]
    assert first == second


def test_messages_carry_no_template_placeholders(flags):
    for flag in flags:
        assert "{" not in flag.message and "}" not in flag.message
        assert flag.message.strip()
        assert flag.rule in an.RULES
        assert flag.severity in config.SEVERITIES


def test_sample_data_fires_the_rules_it_was_built_to_trigger(flags):
    """The integration counterpart to the per-rule unit tests."""
    fired = {flag.rule for flag in flags}
    assert {"missing_fiscal_year", "negative_equity", "revenue_decline",
            "margin_compression", "fcf_negative", "ebitda_margin_collapse",
            "high_debt_to_equity", "low_interest_coverage",
            "negative_working_capital", "high_net_debt_to_ebitda",
            "imputed_column"} <= fired


def test_clean_sample_data_produces_no_identity_flags(flags):
    """The sample file is internally consistent, so it must not claim otherwise."""
    assert [flag for flag in flags if flag.rule == "identity_mismatch"] == []
    assert [flag for flag in flags if flag.rule == "loader_warning"] == []


# ---------------------------------------------------------------------------
# Rule catalogue and output frame
# ---------------------------------------------------------------------------
def test_rule_catalogue_shape():
    described = an.describe_rules()
    assert len(described) == len(an.RULES)
    assert [entry["rule"] for entry in described] == list(an.rule_names())
    for entry in described:
        assert entry["severity"] in config.SEVERITIES
        assert entry["looks_for"]


def test_flags_to_frame_has_a_fixed_shape_when_empty():
    frame = an.flags_to_frame([])
    assert list(frame.columns) == [
        "ticker", "fiscal_year", "rule", "severity", "message", "value"
    ]
    assert len(frame) == 0


def test_flags_to_frame_keeps_years_integral(flags):
    """Whole-company flags have no year; that must not render as 2020.0."""
    frame = an.flags_to_frame(flags)
    assert str(frame["fiscal_year"].dtype) == "Int64"
    assert frame["fiscal_year"].isna().any()
    for year in frame["fiscal_year"].dropna().tolist():
        assert float(year).is_integer()


def test_flag_to_dict_round_trips(flags):
    for flag in flags[:5]:
        assert an.Flag(**flag.to_dict()) == flag


