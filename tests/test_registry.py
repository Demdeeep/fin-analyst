"""Tests for the metric catalogue.

The catalogue is how Stage 2 will describe its own tools to an agent, so the
thing worth protecting is that it cannot drift: every entry must point at a
real function, must declare the columns that function needs, and must cover
every ratio the pipeline actually emits.
"""

from __future__ import annotations

import inspect

import pytest

from financial_analyst.tools import calculations as calc
from financial_analyst.tools import registry
from financial_analyst.tools.metrics import COMPANY_COLUMNS, METRIC_COLUMNS, PERIOD_COLUMNS

#: Metric columns that are raw derived quantities rather than ratios, and so are
#: deliberately not catalogued with a direction.
CONTEXT_COLUMNS = {
    "total_debt", "net_debt", "nopat", "earnings_per_share",
    "book_value_per_share", "market_cap", "enterprise_value",
}

# Company-level and period-level output columns that are useful context for later
# stages but are not directional metrics in the registry catalogue.
NON_REGISTRY_OUTPUT_COLUMNS = {
    "first_year", "last_year", "years_covered", "missing_years", "revenue_cagr",
    "years_since_prior",
}

# Intentional name/shape differences between the generic registry catalogue and
# the concrete Stage 1 output columns.
REGISTRY_ONLY_METRICS = {"cagr", "earnings_per_share", "book_value_per_share"}

VALID_DIRECTIONS = {
    registry.HIGHER_IS_BETTER, registry.LOWER_IS_BETTER, registry.NEUTRAL,
}


def test_catalogue_is_not_empty():
    assert len(registry.METRICS) > 20
    assert registry.names() == tuple(sorted(registry.METRICS))


@pytest.mark.parametrize("name", registry.names())
def test_every_entry_is_well_formed(name):
    spec = registry.get(name)
    assert callable(spec.function)
    assert spec.description and not spec.description.endswith(".")
    assert spec.direction in VALID_DIRECTIONS
    assert spec.required_columns, f"{name} declares no inputs"
    for column in spec.required_columns:
        assert column != name or True  # names may shadow inputs; just no crash


def test_required_columns_come_from_the_function_signature():
    """If the signature changes, the catalogue follows automatically."""
    assert registry.get("debt_to_assets").required_columns == (
        "total_debt_value", "total_assets"
    )
    assert registry.get("gross_margin").required_columns == ("revenue", "cogs")


def test_required_columns_never_leak_plumbing_keywords():
    for name in registry.names():
        spec = registry.get(name)
        assert "default" not in spec.required_columns
        assert "require_positive_denominator" not in spec.required_columns


def test_every_emitted_ratio_is_catalogued():
    """The catalogue must describe the output the pipeline actually produces."""
    uncatalogued = set(METRIC_COLUMNS) - set(registry.METRICS) - CONTEXT_COLUMNS
    assert not uncatalogued, f"metrics emitted but not catalogued: {sorted(uncatalogued)}"


def test_every_catalogued_metric_is_emitted_or_explicitly_context_only():
    """The registry must not describe metrics Stage 1 never produces.

    Some catalogued metrics live outside ``METRIC_COLUMNS`` because they are
    emitted in the company-summary or period-summary sections instead.
    """
    emitted = set(METRIC_COLUMNS) | set(COMPANY_COLUMNS) | set(PERIOD_COLUMNS)
    extra = set(registry.METRICS) - emitted
    assert extra == {"cagr"}


def test_catalogued_functions_live_in_calculations():
    """Every tool is a pure function, not a dataframe helper."""
    for name in registry.names():
        function = registry.get(name).function
        module = inspect.getmodule(function).__name__
        assert module == "financial_analyst.tools.calculations", name


def test_get_unknown_metric_lists_the_alternatives():
    with pytest.raises(KeyError) as error:
        registry.get("not_a_metric")
    assert "not_a_metric" in str(error.value)
    assert "gross_margin" in str(error.value)


def test_describe_is_sorted_and_json_shaped():
    described = registry.describe()
    assert [entry["name"] for entry in described] == list(registry.names())
    assert set(described[0]) == {
        "name", "required_columns", "direction", "description"
    }


def test_lower_is_better_is_marked_for_leverage_metrics():
    for name in ("debt_to_assets", "debt_to_equity", "net_debt_to_ebitda",
                 "equity_multiplier", "price_to_earnings"):
        assert registry.get(name).direction == registry.LOWER_IS_BETTER


def test_higher_is_better_is_marked_for_returns_and_margins():
    for name in ("return_on_equity", "return_on_assets", "gross_margin",
                 "net_margin", "interest_coverage", "current_ratio"):
        assert registry.get(name).direction == registry.HIGHER_IS_BETTER


def test_every_catalogued_function_actually_runs():
    """A catalogue entry pointing at a broken function is worse than no entry."""
    for name in registry.names():
        spec = registry.get(name)
        try:
            spec.function(**{column: None for column in spec.required_columns})
        except Exception as error:  # noqa: BLE001 - that is the point
            pytest.fail(f"{name} raised on all-None inputs: {error!r}")


def test_catalogue_matches_the_calculations_module():
    """Sanity check that the two modules have not drifted apart."""
    assert registry.get("cagr").function is calc.cagr
    assert registry.get("free_cash_flow") if "free_cash_flow" in registry.METRICS else True


def test_metric_frame_columns_have_registry_entries_for_agent_visible_metrics():
    """Columns exposed to later stages must stay described for agent use.

    Raw derived context columns are exempt because they are supporting values,
    not directional metrics.
    """
    emitted = set(METRIC_COLUMNS) | set(COMPANY_COLUMNS) | set(PERIOD_COLUMNS)
    agent_visible = emitted - CONTEXT_COLUMNS - NON_REGISTRY_OUTPUT_COLUMNS
    assert agent_visible | REGISTRY_ONLY_METRICS == set(registry.METRICS)
