"""Tests for cross-company comparison.

The interesting cases here are the refusals. A peer comparison that always
produces a number is worse than no peer comparison: "rank 1 of 2" reads as a
finding and is not one. So most of this file is about when the machinery refuses,
and refuses *visibly*.
"""

from __future__ import annotations

import pytest

from financial_analyst import config
from financial_analyst.agents import peers
from financial_analyst.agents.comparator import ComparatorAgent
from financial_analyst.agents.providers.stub import ScriptedLLM
from financial_analyst.agents.tools import MetricStore
from financial_analyst.agents.verification import verify
from financial_analyst.stages.stage4_peer_comparison import peer_values


def _company(sector, **metrics) -> dict:
    row = {"company_name": f"{sector} Co", "sector": sector,
           "revenue": 1000.0, "gross_margin": 0.4, "debt_to_assets": 0.2}
    row.update(metrics)
    return {"2024": row}


@pytest.fixture
def big_store() -> MetricStore:
    """Six companies, three per sector -- large enough for a real comparison."""
    return MetricStore(
        data={
            "T1": _company("Tech", gross_margin=0.60, debt_to_assets=0.10,
                           return_on_equity=0.30),
            "T2": _company("Tech", gross_margin=0.50, debt_to_assets=0.20,
                           return_on_equity=0.20),
            "T3": _company("Tech", gross_margin=0.40, debt_to_assets=0.30,
                           return_on_equity=0.10),
            "R1": _company("Retail", gross_margin=0.30, debt_to_assets=0.40,
                           return_on_equity=0.05),
            "R2": _company("Retail", gross_margin=0.20, debt_to_assets=0.50,
                           return_on_equity=None),
            "R3": _company("Retail", gross_margin=0.25, debt_to_assets=0.60,
                           return_on_equity=0.15),
        },
        tickers=("R1", "R2", "R3", "T1", "T2", "T3"),
    )


@pytest.fixture
def tiny_store() -> MetricStore:
    """The real sample's shape: no sector reaches three companies."""
    return MetricStore(
        data={
            "A": _company("Tech", gross_margin=0.44),
            "B": _company("Tech", gross_margin=0.40),
            "C": _company("Retail", gross_margin=0.30),
            "D": _company("Industrial", gross_margin=0.31),
        },
        tickers=("A", "B", "C", "D"),
    )


def _with_extra(store: MetricStore, column: str, values: dict) -> MetricStore:
    """Add a column to every company, so it becomes a comparable metric.

    A ticker absent from ``values`` gets ``None`` -- undefined, not zero.
    """
    data = {
        ticker: {year: {**row, column: values.get(ticker)} for year, row in years.items()}
        for ticker, years in store.data.items()
    }
    return MetricStore(data=data, tickers=store.tickers)


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------
def test_a_big_enough_sector_becomes_the_peer_group(big_store):
    group = peers.build_groups(big_store)["T1"]
    assert group.name == "Tech"
    assert group.members == ("T1", "T2", "T3")
    assert group.size == 3


def test_a_small_sector_falls_back_to_the_sample_and_says_so(tiny_store):
    """The fallback must be visible, or "top of 2" reads as "top of the sector"."""
    group = peers.build_groups(tiny_store)["A"]
    assert group.scope == config.PEER_SCOPE_SAMPLE
    assert group.size == 4
    assert "sample" in group.describe()


def test_sample_scope_can_be_forced(big_store):
    groups = peers.build_groups(big_store, scope=config.PEER_SCOPE_SAMPLE)
    assert all(group.size == 6 for group in groups.values())


def test_an_unknown_scope_is_rejected(big_store):
    with pytest.raises(ValueError, match="unknown peer scope"):
        peers.build_groups(big_store, scope="galaxy")


# ---------------------------------------------------------------------------
# The refusal
# ---------------------------------------------------------------------------
def test_comparison_is_declined_below_the_minimum():
    store = MetricStore(
        data={
            "A": _company("Tech", gross_margin=0.44),
            "B": _company("Tech", gross_margin=0.40),
        },
        tickers=("A", "B"),
    )
    result = peers.compare_metric(store, "A", "gross_margin")
    assert not result.computable
    assert result.peer_median is None and result.rank is None
    assert "3 needed" in result.reason
    assert "compan" in result.reason


def test_the_sample_data_compares_only_against_the_whole_sample(tiny_store):
    """Technology has 2, so it falls back to the sample of 4 and does compare."""
    result = peers.compare_metric(tiny_store, "A", "gross_margin")
    assert result.computable
    assert result.group.size == 4
    assert result.group.scope == config.PEER_SCOPE_SAMPLE


def test_an_undefined_metric_declines_rather_than_comparing_zeros(big_store):
    result = peers.compare_metric(big_store, "R2", "return_on_equity")
    assert not result.computable
    assert "did not compute" in result.reason


def test_a_metric_that_is_not_a_stage1_column_is_refused(big_store):
    result = peers.compare_metric(big_store, "T1", "not_a_column")
    assert not result.computable
    assert "not a Stage 1 output column" in result.reason


def test_revenue_cagr_compares_though_cagr_is_not_in_the_registry(big_store):
    """`cagr` is the function; `revenue_cagr` is the column an agent cites."""
    store = _with_extra(big_store, "revenue_cagr", {t: 0.1 * i for i, t in
                                                     enumerate(big_store.tickers, 1)})
    result = peers.compare_metric(store, "T1", "revenue_cagr")
    assert "not a Stage 1 output column" not in result.reason
    assert result.computable


# ---------------------------------------------------------------------------
# Direction-aware ranking
# ---------------------------------------------------------------------------
def test_higher_is_better_ranks_the_highest_first(big_store):
    result = peers.compare_metric(big_store, "T1", "gross_margin")
    assert result.rank == 1                           # 0.60 is the highest margin
    assert result.group.members == ("T1", "T2", "T3")  # sector peers, not all six
    assert result.peer_median == pytest.approx(0.45)  # median of peers 0.50, 0.40
    assert result.gap == pytest.approx(0.15)


def test_lower_is_better_ranks_the_lowest_first(big_store):
    """Rank 1 on leverage means least levered, not most."""
    assert peers.compare_metric(big_store, "T1", "debt_to_assets").rank == 1
    assert peers.compare_metric(big_store, "T3", "debt_to_assets").rank == 3


def test_a_neutral_metric_gets_no_rank(big_store):
    """Being top of a table with no good direction is not an achievement."""
    store = _with_extra(big_store, "days_payables_outstanding",
                        {t: 30.0 + i for i, t in enumerate(big_store.tickers)})
    result = peers.compare_metric(store, "T1", "days_payables_outstanding")
    assert result.computable, "the median is still computable"
    assert result.rank is None, "but nothing here beats anything else"


def test_peers_without_the_metric_are_excluded_not_zeroed(big_store):
    """T1's median must ignore R2's undefined ROE rather than read it as 0."""
    result = peers.compare_metric(big_store, "T1", "return_on_equity")
    assert result.computable
    # Tech peers only: 0.20 and 0.10. R2's undefined ROE is in another sector and
    # would have pulled the median down to 0.10 had it been read as zero.
    assert result.peer_median == pytest.approx(0.15)
    assert result.gap == pytest.approx(0.15)


def test_an_undefined_peer_is_excluded_within_the_same_sector(big_store):
    """R2's ROE is undefined; R1's median must use R3 only, not R2 as zero."""
    result = peers.compare_metric(big_store, "R1", "return_on_equity")
    assert result.computable
    assert result.peer_median == pytest.approx(0.15)  # R3 alone, not (0 + 0.15)/2
    assert result.gap == pytest.approx(-0.10)


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------
def test_the_prompt_carries_the_computed_table(big_store):
    """The model must never be asked to derive a gap itself."""
    client = ScriptedLLM(["T1 leads the group."])
    agent = ComparatorAgent(client, big_store, metrics=("gross_margin",))
    agent.compare("T1")

    prompt = client.calls[0].user
    assert "peer median" in prompt.lower()
    assert "0.6" in prompt and "0.45" in prompt    # value and peer median
    assert "Tech" in prompt and "n=3" in prompt


def test_the_prompt_tells_the_model_a_refusal_is_a_refusal(big_store):
    client = ScriptedLLM(["ok"])
    ComparatorAgent(client, big_store, metrics=("gross_margin",)).compare("T1")
    system = client.calls[0].system
    assert "not comparable" in system
    assert "too few" in system


def test_the_prompt_demands_units_so_figures_stay_checkable(big_store):
    """Bare numbers are not extracted as claims, so an unlabelled gap is unverified."""
    client = ScriptedLLM(["ok"])
    ComparatorAgent(client, big_store, metrics=("gross_margin",)).compare("T1")
    assert "unit" in client.calls[0].system


def test_declined_rows_stay_visible_in_the_prompt(big_store):
    """The refusal must reach the model, not be quietly dropped from the table."""
    store = _with_extra(big_store, "interest_coverage", {"T1": 8.0})
    client = ScriptedLLM(["ok"])
    result = ComparatorAgent(
        client, store, metrics=("gross_margin", "interest_coverage")
    ).compare("T1")

    prompt = client.calls[0].user
    assert "not comparable" in prompt           # T1's interest coverage declined
    assert "no peer" in prompt                  # and it says why
    assert len(result.declined) == 1
    assert result.computable[0].metric == "gross_margin"
    assert result.any_comparable


def test_results_separate_what_compared_from_what_did_not(big_store):
    client = ScriptedLLM(["ok"])
    result = ComparatorAgent(
        client, big_store, metrics=("gross_margin", "not_a_column")
    ).compare("T1")
    assert len(result.computable) == 1
    assert result.declined[0].metric == "not_a_column"
    assert result.to_dict()["declined"][0]["computable"] is False


# ---------------------------------------------------------------------------
# Verifying peer-derived figures
# ---------------------------------------------------------------------------
def test_a_real_peer_gap_is_accepted_as_a_legitimate_figure(big_store):
    """15.0pp is not in T1's metrics, but it is the computed gap to median."""
    result = ComparatorAgent(ScriptedLLM(["ok"]), big_store,
                             metrics=("gross_margin",)).compare("T1")
    narrative = "T1's gross margin is 15.0pp above the peer median of 45.0%."
    assert verify(narrative, big_store, "T1",
                  extra_values=peer_values(result)) == []


def test_a_made_up_peer_median_is_still_flagged(big_store):
    narrative = "T1's gross margin is 15.0pp above the peer median of 0.99%."
    issues = verify(narrative, big_store, "T1", extra_values=(0.6, 0.45, 0.15))
    assert any(issue.kind == "unverifiable_figure" for issue in issues)


def test_a_hallucinated_gap_is_still_caught(big_store):
    result = ComparatorAgent(ScriptedLLM(["ok"]), big_store,
                             metrics=("gross_margin",)).compare("T1")
    narrative = "T1's gross margin is 99.0pp above the peer median."
    issues = verify(narrative, big_store, "T1", extra_values=peer_values(result))
    assert [issue.kind for issue in issues] == ["unverifiable_figure"]


def test_a_bare_decimal_gap_is_not_checked_at_all(big_store):
    """Documented limitation: only unit-bearing figures are extracted as claims.

    This is why the comparator prompt insists on units -- an unlabelled gap
    passes verification precisely because nobody can check it.
    """
    result = ComparatorAgent(ScriptedLLM(["ok"]), big_store,
                             metrics=("gross_margin",)).compare("T1")
    assert verify("T1's margin is 0.99 above the median.", big_store, "T1",
                  extra_values=peer_values(result)) == []


def test_rank_is_not_quoted_as_a_measurement(big_store):
    """Rank 1 must not be accepted as a figure; it is an ordinal, not a number."""
    result = ComparatorAgent(ScriptedLLM(["ok"]), big_store,
                             metrics=("gross_margin",)).compare("T1")
    narrative = "T1 ranks 1 of 3 on gross margin at 60.0%."
    assert verify(narrative, big_store, "T1",
                  extra_values=peer_values(result)) == []


def test_a_gap_drawn_from_a_declined_group_is_not_blessed(big_store):
    """extra_values must only ever carry figures a comparison actually produced."""
    store = MetricStore(
        data={"A": _company("Tech", gross_margin=0.44),
              "B": _company("Tech", gross_margin=0.40)},
        tickers=("A", "B"),
    )
    result = ComparatorAgent(ScriptedLLM(["ok"]), store,
                             metrics=("gross_margin",)).compare("A")
    assert peer_values(result) == ()   # nothing comparable, so nothing quotable
    issues = verify("A's margin is 4.0pp above the median.", store, "A",
                    extra_values=peer_values(result))
    assert [issue.kind for issue in issues] == ["unverifiable_figure"]
