"""Tests for flag coverage: did the analysis engage with what Stage 1 raised?

Structured like ``test_anomalies.py`` -- fire, no-fire, then edges -- because
the failure mode that matters here is a check that fires on everything. An
analysis cannot address 23 flags in five sentences, so a naive per-flag rule
would fire constantly and its reader would learn to ignore it. These tests pin
the severity gating that prevents exactly that.
"""

from __future__ import annotations

import pytest

from financial_analyst import config
from financial_analyst.agents import coverage as cov
from financial_analyst.agents.critic import OK, REJECT, CriticAgent, _parse_flag_review
from financial_analyst.agents.flags import FlagRef
from financial_analyst.agents.providers.stub import ScriptedLLM

from test_agents import synthetic_store  # noqa: F401 - reuse the fixture

CLEAN = "Revenue compounded at 9.9% to $1,750M with gross margin of 44.0%."

CRITICAL = FlagRef(
    ticker="LOSS", rule="low_interest_coverage", severity="critical",
    message="operating income covers interest 0.36x, below the 1.0x threshold",
    fiscal_year=2024, value=0.36,
)
WARNING = FlagRef(
    ticker="LOSS", rule="margin_compression", severity="warning",
    message="net margin fell 9.4pp year over year",
    fiscal_year=2023, value=-9.4,
)
INFO = FlagRef(
    ticker="VCTR", rule="imputed_column", severity="info",
    message="ebitda was blank in the source and has been derived",
    fiscal_year=None, value=None,
)


# ---------------------------------------------------------------------------
# The screen
# ---------------------------------------------------------------------------
def test_citing_the_rule_id_counts_as_addressed():
    narrative = "The low_interest_coverage flag is the headline: coverage is 0.36x."
    issue = cov.screen_flag(narrative, CRITICAL)
    assert issue.status == cov.ADDRESSED
    assert not issue.needs_review


def test_citing_the_rule_name_in_words_counts_too():
    narrative = "The low interest coverage flag is the headline."
    assert cov.screen_flag(narrative, CRITICAL).status == cov.ADDRESSED


def test_citing_the_flags_own_value_counts_as_addressed():
    assert cov.screen_flag("Interest cover of 0.36x is thin.", CRITICAL).status == (
        cov.ADDRESSED
    )


def test_silence_is_only_a_candidate_never_a_conclusion():
    """The screen selects; it must not pronounce. Absence of a keyword is not
    proof of absence of engagement."""
    issue = cov.screen_flag(CLEAN, CRITICAL)
    assert issue.status == cov.LOOKS_UNADDRESSED
    assert issue.needs_review
    assert "no mention" in issue.detail


def test_a_flag_with_no_signal_is_unclear_not_unaddressed():
    """`imputed_column` has no number, so silence proves nothing about it."""
    issue = cov.screen_flag(CLEAN, INFO)
    assert issue.status == cov.UNCLEAR
    assert issue.needs_review


def test_threshold_text_is_never_used_as_a_signal():
    """"below the 1.0x threshold" appears in nearly every flag; matching on it
    would mark almost anything as addressed."""
    assert "1.0" not in CRITICAL.signals()
    assert set(CRITICAL.signals()) == {
        "low_interest_coverage", "low interest coverage", "0.36"
    }


def test_rule_id_matching_respects_word_boundaries():
    assert cov.screen_flag("Gross margin is strong.", CRITICAL).status == (
        cov.LOOKS_UNADDRESSED
    )


def test_no_flags_is_silent_and_free():
    issues = cov.screen_flags(CLEAN, ())
    assert issues == ()
    assert cov.require_addressed(issues) == ()
    assert cov.summarise(issues) == {
        cov.ADDRESSED: 0, cov.LOOKS_UNADDRESSED: 0, cov.UNCLEAR: 0
    }


def test_summarise_counts_each_state():
    issues = cov.screen_flags(
        "The low_interest_coverage flag stands.", (CRITICAL, WARNING, INFO)
    )
    assert cov.summarise(issues) == {
        cov.ADDRESSED: 1, cov.LOOKS_UNADDRESSED: 1, cov.UNCLEAR: 1
    }


# ---------------------------------------------------------------------------
# Severity gating -- what stops the check firing on everything
# ---------------------------------------------------------------------------
def test_info_flags_are_never_required():
    assert cov.require_addressed(cov.screen_flags(CLEAN, (INFO,))) == ()


def test_warnings_are_advisory_not_required():
    issues = cov.screen_flags(CLEAN, (WARNING,))
    assert cov.require_addressed(issues) == ()
    assert cov.advisory_only(issues) == issues


def test_criticals_are_required():
    issues = cov.screen_flags(CLEAN, (CRITICAL,))
    assert cov.require_addressed(issues) == issues
    assert cov.advisory_only(issues) == ()


def test_the_severity_policy_is_configured_not_hardcoded():
    assert config.MUST_ADDRESS_SEVERITIES == ("critical",)
    assert config.ADVISORY_SEVERITIES == ("warning",)
    combined = set(
        config.MUST_ADDRESS_SEVERITIES
        + config.ADVISORY_SEVERITIES
        + config.OPTIONAL_SEVERITIES
    )
    assert combined == set(config.SEVERITIES)


# ---------------------------------------------------------------------------
# The critic, end to end
# ---------------------------------------------------------------------------
def test_ignored_critical_flag_is_rejected(synthetic_store):
    verdict = CriticAgent(None, synthetic_store).review(
        "LOSS", "Revenue was 430.0M with gross margin 40.0%.", flags=(CRITICAL,)
    )
    assert verdict.verdict == REJECT
    assert verdict.flags_considered == 1
    assert verdict.flags_addressed == 0
    assert verdict.retryable, "an unaddressed flag is exactly what a retry fixes"


def test_naming_the_flag_clears_it(synthetic_store):
    narrative = (
        "Revenue was 430.0M. The low_interest_coverage flag is the concern: "
        "coverage is 0.36x."
    )
    verdict = CriticAgent(None, synthetic_store).review(
        "LOSS", narrative, flags=(CRITICAL,)
    )
    assert verdict.verdict == OK
    assert verdict.flags_addressed == 1


def test_ignored_warning_does_not_move_the_verdict(synthetic_store):
    verdict = CriticAgent(None, synthetic_store).review(
        "LOSS", "Revenue was 430.0M.", flags=(WARNING,)
    )
    assert verdict.verdict == OK
    assert verdict.flags_considered == 1


def test_no_flags_means_no_coverage_findings(synthetic_store):
    verdict = CriticAgent(None, synthetic_store).review("TST", CLEAN)
    assert verdict.verdict == OK
    assert verdict.flags_considered == 0
    assert verdict.coverage == ()


def test_a_clean_analysis_that_ignores_everything_is_still_rejected(synthetic_store):
    """No invented numbers is necessary but not sufficient."""
    verdict = CriticAgent(None, synthetic_store).review(
        "LOSS", "Revenue was 430.0M and margins held at 40.0%.", flags=(CRITICAL,)
    )
    assert verdict.verified, "the figures are clean"
    assert verdict.verdict == REJECT, "but it never mentions the flag"


def test_the_model_can_dismiss_a_candidate(synthetic_store):
    client = ScriptedLLM(
        ["ADDRESSED: low_interest_coverage FY2024\nthe analysis covers it in full"]
    )
    verdict = CriticAgent(client, synthetic_store).review(
        "LOSS", "Revenue was 430.0M.", flags=(CRITICAL,)
    )
    assert verdict.verdict == OK


def test_the_model_cannot_clear_a_figure_finding(synthetic_store):
    bad = "Revenue compounded at 12.0% while gross margin reached 30.0%."
    verdict = CriticAgent(ScriptedLLM(["OK\nfine"]), synthetic_store).review("TST", bad)
    assert verdict.verdict != OK


def test_flag_abstention_is_not_acquittal(synthetic_store):
    """An unusable reply leaves the screen standing."""
    verdict = CriticAgent(ScriptedLLM(["I dunno"]), synthetic_store).review(
        "LOSS", "Revenue was 430.0M.", flags=(CRITICAL,)
    )
    assert verdict.verdict == REJECT
    assert "no usable answer" in verdict.notes


def test_a_hard_figure_failure_outranks_an_unaddressed_flag(synthetic_store):
    """`numerified_undefined` returns before flags are considered at all."""
    verdict = CriticAgent(None, synthetic_store).review(
        "LOSS",
        "It trades at a price to earnings of 4.2x and revenue was 430.0M.",
        flags=(CRITICAL,),
    )
    assert verdict.verdict == REJECT
    assert verdict.retryable is False


def test_screening_is_deterministic_without_a_model(synthetic_store):
    flags = (CRITICAL, WARNING, INFO)
    first = CriticAgent(None, synthetic_store).review(
        "LOSS", "Revenue was 430.0M.", flags=flags
    )
    second = CriticAgent(None, synthetic_store).review(
        "LOSS", "Revenue was 430.0M.", flags=flags
    )
    assert first.to_dict() == second.to_dict()


# ---------------------------------------------------------------------------
# Parsing the model's flag review
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "reply, expected",
    [
        ("ADDRESSED: low_interest_coverage FY2024", {"low_interest_coverage FY2024"}),
        ("ADDRESSED: a_rule FY2024\nADDRESSED: b_rule FY2023",
         {"a_rule FY2024", "b_rule FY2023"}),
        ("addressed: lower_case_rule FY2024", {"lower_case_rule FY2024"}),
        ("NONE", None),
        ("I don't think any were addressed", None),
        ("", None),
    ],
)
def test_flag_review_parsing(reply, expected):
    assert _parse_flag_review(reply)[0] == expected


def test_a_dismissed_flag_must_match_rule_and_year(synthetic_store):
    """A bare rule name is not enough: the same rule fires on several years."""
    client = ScriptedLLM(["ADDRESSED: low_interest_coverage FY2020"])
    verdict = CriticAgent(client, synthetic_store).review(
        "LOSS", "Revenue was 430.0M.", flags=(CRITICAL,)
    )
    assert verdict.verdict == REJECT