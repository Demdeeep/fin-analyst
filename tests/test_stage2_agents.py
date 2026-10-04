"""Tests for the agents and the Stage 2 entry point.

The point of these is that the whole pipeline -- prompts, tool loop, critic,
orchestration -- executes end to end with **no API key and no network**. Only
prose quality changes when a key is added; nothing structural does.

The tests that matter most are about *authority*: a hallucinated figure must be
caught, a model saying OK must not clear a failed mechanical check, and an
unparseable critic reply must not manufacture a revision.
"""

from __future__ import annotations

import json

import pytest

from financial_analyst.agents.analyst import AnalystAgent
from financial_analyst.agents.critic import (
    OK,
    REJECT,
    REVISE,
    CriticAgent,
    _parse_verdict,
)
from financial_analyst.agents.orchestrator import Orchestrator
from financial_analyst.agents.providers.stub import ExplodingLLM, ScriptedLLM
from financial_analyst.agents.tools import MetricStore
from financial_analyst.stages import stage2_agents as stage2

from test_agents import synthetic_store  # noqa: F401 - reuse the fixture

GOOD = "Revenue compounded at 9.9% to $1,750M with gross margin of 44.0%."
BAD_NUMBER = "Revenue compounded at 12.0% while gross margin reached 30.0%."
NUMERIFIED = "It trades at a price to earnings of 4.2x."


def _agent(replies, store):
    return AnalystAgent(ScriptedLLM(list(replies)), store)


# ---------------------------------------------------------------------------
# Analyst
# ---------------------------------------------------------------------------
def test_analyst_runs_two_turns_and_captures_the_narrative(synthetic_store):
    agent = _agent(
        ["I need growth. TOOL: revenue_cagr, gross_margin", GOOD], synthetic_store
    )
    result = agent.analyse("TST")
    assert result.turns == 2
    assert result.narrative == GOOD
    assert result.tools_requested == ("revenue_cagr", "gross_margin")
    assert set(result.observation.values) == {"revenue_cagr", "gross_margin"}


def test_analyst_prompt_carries_the_catalogue_and_the_flags(synthetic_store):
    client = ScriptedLLM(["TOOL: gross_margin", GOOD])
    AnalystAgent(client, synthetic_store).analyse(
        "TST", flags=["[critical] low_interest_coverage FY2022: coverage 0.36x"]
    )
    system = client.calls[0].system
    first_user = client.calls[0].user
    second_user = client.calls[1].user
    # The catalogue belongs in the system prompt, not only the user turn.
    assert "`gross_margin`" in system and "higher_is_better" in system
    assert "low_interest_coverage" in first_user
    assert "44.0%" in second_user          # the observation reached the model


def test_analyst_reports_a_hallucinated_tool_name(synthetic_store):
    """A model inventing a metric is told so, rather than trusted."""
    agent = _agent(["TOOL: net_income_profit_ratio", GOOD], synthetic_store)
    observation = agent.analyse("TST").observation
    assert observation.unknown == ("net_income_profit_ratio",)
    assert "Unknown metrics" in observation.render()


def test_analyst_refuses_an_empty_store():
    empty = MetricStore(data={}, tickers=())
    with pytest.raises(ValueError, match="Stage 1 metrics are empty"):
        _agent(["TOOL: gross_margin", GOOD], empty).analyse("TST")


# ---------------------------------------------------------------------------
# Critic
# ---------------------------------------------------------------------------
def test_clean_narrative_passes(synthetic_store):
    verdict = CriticAgent(ScriptedLLM(["OK\nlooks fine"]), synthetic_store).review(
        "TST", GOOD
    )
    assert verdict.verdict == OK
    assert verdict.issues == ()
    assert verdict.clean


def test_hallucinated_figure_is_revise_even_if_the_model_says_ok(synthetic_store):
    """The model cannot overrule arithmetic."""
    verdict = CriticAgent(ScriptedLLM(["OK\nall good"]), synthetic_store).review(
        "TST", BAD_NUMBER
    )
    assert verdict.verdict == REVISE
    assert {issue.kind for issue in verdict.issues} == {"unverifiable_figure"}


def test_numerified_undefined_is_rejected_outright(synthetic_store):
    verdict = CriticAgent(ScriptedLLM(["OK\nfine"]), synthetic_store).review(
        "LOSS", NUMERIFIED
    )
    assert verdict.verdict == REJECT
    assert "declined to compute" in verdict.notes


def test_unparseable_reply_does_not_manufacture_a_revision(synthetic_store):
    """A silent critic is not a critic saying "revise"."""
    verdict = CriticAgent(ScriptedLLM(["hmm, hard to say"]), synthetic_store).review(
        "TST", GOOD
    )
    assert verdict.verdict == OK
    assert "not model-reviewed" in verdict.notes


def test_critic_can_run_mechanically_with_no_client(synthetic_store):
    """CI should be able to rely on the check alone, with no model at all."""
    verdict = CriticAgent(None, synthetic_store).review("TST", GOOD)
    assert verdict.verdict == OK
    assert verdict.model_consulted is False
    assert CriticAgent(None, synthetic_store).review("TST", BAD_NUMBER).verdict == REVISE


@pytest.mark.parametrize(
    "reply, expected",
    [
        ("OK\nfine", OK),
        ("**REVISE**\nunsupported", REVISE),
        ("reject\nbuilt on nothing", REJECT),
        ("okay then", None),
        ("", None),
    ],
)
def test_verdict_parsing(reply, expected):
    assert _parse_verdict(reply)[0] == expected


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def test_orchestrator_passes_clean_narratives_through(synthetic_store):
    client = ScriptedLLM(["TOOL: revenue_cagr", GOOD, "OK\nfine"])
    result = Orchestrator(client, synthetic_store).run("TST")
    assert result.verdict == OK
    assert result.revisions == 0
    assert result.verified


def test_orchestrator_revises_once_then_stops(synthetic_store):
    """The loop is bounded: a second bad attempt is returned, not retried forever."""
    client = ScriptedLLM(
        ["TOOL: gross_margin", BAD_NUMBER, "OK\nok",       # attempt 1 -> revise
         "TOOL: gross_margin", GOOD, "OK\nfixed"]          # attempt 2
    )
    result = Orchestrator(client, synthetic_store, max_revisions=1).run("TST")
    assert result.revisions == 1
    assert result.narrative == GOOD
    assert result.verdict == OK


def test_orchestrator_respects_a_zero_revision_budget(synthetic_store):
    client = ScriptedLLM(["TOOL: gross_margin", BAD_NUMBER, "OK\nok"])
    result = Orchestrator(client, synthetic_store, max_revisions=0).run("TST")
    assert result.revisions == 0
    assert result.verdict == REVISE


def test_a_hard_failure_is_not_retried(synthetic_store):
    """A numerified-undefined finding cannot be fixed by rewording."""
    client = ScriptedLLM(["TOOL: price_to_earnings", NUMERIFIED, "OK\nfine"])
    result = Orchestrator(client, synthetic_store, max_revisions=3).run("LOSS")
    assert result.verdict == REJECT
    assert result.revisions == 0
    assert not result.verified


def test_verified_is_about_figures_not_about_the_verdict(synthetic_store):
    """A REVISE can still be figure-clean, and an OK cannot make it dirty."""
    client = ScriptedLLM(["TOOL: gross_margin", GOOD, "REVISE\ntoo thin"])
    result = Orchestrator(client, synthetic_store, max_revisions=0).run("TST")
    assert result.verdict == REVISE
    assert result.verified


def test_provider_failure_propagates(synthetic_store):
    with pytest.raises(Exception):
        Orchestrator(ExplodingLLM("provider down"), synthetic_store).run("TST")


# ---------------------------------------------------------------------------
# Stage entry point
# ---------------------------------------------------------------------------
@pytest.fixture
def staged(tmp_path):
    """A minimal Stage 1 artifact pair, so Stage 2 needs no real pipeline run."""
    metrics = tmp_path / "stage1_metrics.json"
    metrics.write_text(json.dumps({
        "TST": {"2024": {"company_name": "Test Co", "sector": "Industrials",
                         "revenue": 1750.0, "revenue_cagr": 0.099,
                         "gross_margin": 0.44, "return_on_equity": 0.091}},
        "LOSS": {"2024": {"company_name": "Loss Co", "sector": "Technology",
                          "revenue": 430.0, "revenue_cagr": 0.094,
                          "gross_margin": 0.40, "return_on_equity": None}},
    }))
    flags = tmp_path / "stage1_flags.csv"
    flags.write_text(
        "ticker,fiscal_year,rule,severity,message,value\n"
        "LOSS,2024,low_interest_coverage,critical,coverage 0.36x,0.36\n"
    )
    return metrics, flags


def test_stage2_runs_end_to_end_without_a_key(staged, tmp_path):
    metrics, flags = staged
    out = tmp_path / "out"
    results = stage2.run_stage2(
        metrics_path=metrics, flags_path=flags, output_dir=out, write=True
    )
    assert [result.ticker for result in results] == ["LOSS", "TST"]
    assert (out / "stage2_analysis.json").exists()
    assert "# Stage 2 agent analysis" in (out / "stage2_analysis.md").read_text()


def test_stage2_json_is_valid_and_carries_verdicts(staged, tmp_path):
    metrics, flags = staged
    out = tmp_path / "out"
    stage2.run_stage2(metrics_path=metrics, flags_path=flags, output_dir=out)
    payload = json.loads((out / "stage2_analysis.json").read_text())
    assert payload["provider"] == "stub"
    assert {entry["ticker"] for entry in payload["companies"]} == {"LOSS", "TST"}
    assert all("verified" in entry for entry in payload["companies"])


def test_stage2_passes_flags_into_the_prompt(staged):
    metrics, flags = staged
    client = ScriptedLLM(["TOOL: gross_margin", GOOD, "OK\nfine"])
    store = MetricStore.from_json(metrics)
    lines = stage2.load_flags(flags)
    assert lines["LOSS"][0].startswith("[critical] low_interest_coverage")
    Orchestrator(client, store).run("LOSS", flags=lines["LOSS"])
    assert "low_interest_coverage" in client.calls[0].user


def test_stage2_can_limit_to_one_ticker(staged, tmp_path):
    metrics, flags = staged
    results = stage2.run_stage2(
        metrics_path=metrics, flags_path=flags, output_dir=tmp_path,
        tickers=["TST"], write=False,
    )
    assert [result.ticker for result in results] == ["TST"]


def test_stage2_refuses_to_run_before_stage_1(tmp_path):
    with pytest.raises(FileNotFoundError, match="run Stage 1 first"):
        stage2.run_stage2(
            metrics_path=tmp_path / "absent.json",
            flags_path=tmp_path / "absent.csv",
            write=False,
        )


def test_flags_absent_is_not_fatal(tmp_path):
    """No flags file means no flags, not a crash."""
    assert stage2.load_flags(tmp_path / "nope.csv") == {}