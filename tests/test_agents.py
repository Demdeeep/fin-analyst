"""Tests for the Stage 2 foundations: providers, tool access and verification.

Stage 2's safety story rests on three things, each tested here:

1. The provider seam is one method, and a missing SDK or key degrades to a clear
   error rather than an import crash.
2. An agent can only cite values Stage 1 computed -- enforced in code, not asked
   for in a prompt.
3. Every figure in an agent's prose reconciles with Stage 1's numbers, and a
   metric Stage 1 declined to compute cannot be quietly filled in.

No API key is needed or used.
"""

from __future__ import annotations

import pytest

from types import SimpleNamespace

from financial_analyst.agents import verification
from financial_analyst.agents.providers import build_client, describe
from financial_analyst.agents.providers.base import CompletionError, ProviderConfig
from financial_analyst.agents.providers.stub import (
    AlwaysSays,
    ExplodingLLM,
    ScriptedLLM,
)
from financial_analyst.agents.tools import (
    CITEABLE_METRICS,
    MetricStore,
    ToolCatalogue,
    execute,
    parse_tool_request,
)
from financial_analyst.tools.metrics import METRIC_COLUMNS


@pytest.fixture(scope="module")
def store() -> MetricStore:
    return MetricStore.from_json("outputs/stage1_metrics.json")


@pytest.fixture(scope="module")
def synthetic_store() -> MetricStore:
    """A tiny store, so tests do not depend on the sample data's values.

    Figures are ACME's, so hand-written prose can be checked against them:
    revenue 1,750; CAGR 0.099; gross margin 0.44; ROE 0.091. LOSS stands in for a
    loss-maker, where ROE and P/E are undefined.
    """
    return MetricStore(
        data={
            "TST": {
                "2024": {
                    "revenue": 1750.0, "revenue_cagr": 0.099, "gross_margin": 0.44,
                    "net_margin": 0.108, "return_on_equity": 0.091,
                    "interest_coverage": 8.5, "debt_to_assets": 0.136,
                    "free_cash_flow": 170.0, "price_to_earnings": 41.48,
                }
            },
            "LOSS": {
                "2024": {
                    "revenue": 430.0, "revenue_cagr": 0.094, "gross_margin": 0.40,
                    "net_margin": -0.036, "return_on_equity": None,
                    "interest_coverage": 0.36, "debt_to_assets": 0.419,
                    "free_cash_flow": 0.0, "price_to_earnings": None,
                }
            },
        },
        tickers=("LOSS", "TST"),
    )


# ---------------------------------------------------------------------------
# Provider seam
# ---------------------------------------------------------------------------
def test_scripted_client_replays_and_records():
    client = ScriptedLLM(["first", "second"])
    assert client.complete(system="s", user="u") == "first"
    assert client.complete(system="s", user="u2") == "second"
    assert client.call_count == 2
    assert client.last_user() == "u2"
    assert client.remaining == 0


def test_scripted_client_falls_back_rather_than_raising():
    """A miscounted script should look obviously wrong, not error like a bug."""
    assert "no scripted reply" in ScriptedLLM([]).complete(system="s", user="u")


def test_always_says_and_exploding():
    client = AlwaysSays("same")
    assert client.complete(system="s", user="u") == "same"
    assert client.complete(system="s", user="u") == "same"
    with pytest.raises(CompletionError):
        ExplodingLLM("down").complete(system="s", user="u")


def test_provider_config_never_exposes_the_key(monkeypatch):
    monkeypatch.setenv("TEST_KEY", "  sk-secret  ")
    config = ProviderConfig(api_key_env="TEST_KEY")
    assert config.api_key == "sk-secret"
    assert config.is_configured
    # A blank key counts as absent rather than as an empty credential.
    monkeypatch.setenv("TEST_KEY", "   ")
    assert not config.is_configured


def _make_response(content, finish_reason="stop"):
    """A stand-in for the SDK's response object.

    Only the attributes the adapter touches, so a truncation test does not
    depend on the SDK's shape.
    """
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    usage = SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18)
    return SimpleNamespace(choices=[choice], usage=usage)


def test_a_truncated_reply_is_recorded_not_raised(monkeypatch):
    """Truncation reads as malformed prose, so it must be distinguishable.

    The critic's verdict parser cannot tell a clipped reply from a genuinely
    unparseable one. Raising here would abort the run; recording it lets the
    orchestrator finish and report that this reply is not trustworthy.
    """
    from financial_analyst.agents.providers.openai_client import OpenAIClient

    client = OpenAIClient(config=ProviderConfig())
    client._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: _make_response("The analysis is cut", "length")
            )
        )
    )
    assert client.complete(system="s", user="u") == "The analysis is cut"
    assert client.truncated == ["The analysis is cut"]
    assert client.last_finish_reason == "length"


def test_a_complete_reply_records_no_truncation(monkeypatch):
    from financial_analyst.agents.providers.openai_client import OpenAIClient

    client = OpenAIClient(config=ProviderConfig())
    client._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: _make_response("All done.", "stop")
            )
        )
    )
    assert client.complete(system="s", user="u") == "All done."
    assert client.truncated == []
    # Recorded on every call, not only truncated ones, so a run's log can say
    # *why* a reply ended rather than only that something was wrong.
    assert client.last_finish_reason == "stop"


def test_a_response_without_a_finish_reason_is_tolerated():
    """The field is the SDK's to change; a missing optional one must not crash.

    Confirmed against the live SDK (3.24.0), which does return it -- so this is
    tolerance for a shape change, not a workaround for an observed absence.
    """
    from financial_analyst.agents.providers.openai_client import OpenAIClient

    client = OpenAIClient(config=ProviderConfig())
    bare = SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content="hi"), finish_reason=None)
        ]
    )
    client._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kwargs: bare)
        )
    )
    assert client.complete(system="s", user="u") == "hi"
    assert client.last_finish_reason is None
    assert client.truncated == []


def test_usage_is_captured_when_the_sdk_returns_it(monkeypatch):
    from financial_analyst.agents.providers.openai_client import OpenAIClient

    client = OpenAIClient(config=ProviderConfig())
    monkeypatch.setattr(client, "_sdk", lambda: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_: _make_response("hi")))
    ))

    assert client.complete(system="s", user="u") == "hi"
    assert client.last_usage == {
        "input_tokens": 11,
        "output_tokens": 7,
        "total_tokens": 18,
    }


def test_missing_usage_is_tolerated(monkeypatch):
    from financial_analyst.agents.providers.openai_client import OpenAIClient

    client = OpenAIClient(config=ProviderConfig())
    monkeypatch.setattr(client, "_sdk", lambda: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_: SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="hi"), finish_reason="stop")]
        )))
    ))

    assert client.complete(system="s", user="u") == "hi"
    assert client.last_usage is None


def test_an_empty_reply_carries_its_finish_reason():
    """An empty completion with no reason is a different failure to diagnose."""
    from financial_analyst.agents.providers.openai_client import OpenAIClient

    client = OpenAIClient(config=ProviderConfig())
    client._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: _make_response(None, "content_filter")
            )
        )
    )
    with pytest.raises(CompletionError) as error:
        client.complete(system="s", user="u")
    assert error.value.finish_reason == "content_filter"
    assert "content_filter" in str(error.value)


def test_completion_error_without_a_finish_reason_still_works():
    """The constructor stayed one-argument compatible."""
    assert CompletionError("boom").finish_reason is None


# ---------------------------------------------------------------------------
# .env loading
# ---------------------------------------------------------------------------
def test_a_dotenv_file_supplies_the_key(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_KEY", raising=False)
    path = tmp_path / ".env"
    path.write_text("# a comment\nexport TEST_KEY='sk-from-file'\n")

    config = ProviderConfig(api_key_env="TEST_KEY", dotenv_path=str(path))
    assert config.api_key == "sk-from-file"
    assert config.is_configured


def test_an_exported_key_is_not_overridden_by_the_file(tmp_path, monkeypatch):
    """A shell export is more deliberate than a file and should win."""
    monkeypatch.setenv("TEST_KEY", "sk-from-shell")
    path = tmp_path / ".env"
    path.write_text("TEST_KEY=sk-from-file\n")

    config = ProviderConfig(api_key_env="TEST_KEY", dotenv_path=str(path))
    assert config.api_key == "sk-from-shell"


def test_a_missing_dotenv_file_is_not_an_error(monkeypatch):
    monkeypatch.delenv("TEST_KEY", raising=False)
    config = ProviderConfig(api_key_env="TEST_KEY",
                            dotenv_path="/nonexistent/path/.env")
    assert not config.is_configured


def test_malformed_dotenv_lines_are_skipped_quietly(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_KEY", raising=False)
    path = tmp_path / ".env"
    path.write_text("not an assignment\n\nTEST_KEY=sk-good\n# trailing note\n")

    config = ProviderConfig(api_key_env="TEST_KEY", dotenv_path=str(path))
    assert config.api_key == "sk-good"


def test_dotenv_is_not_consulted_unless_asked(monkeypatch, tmp_path):
    """Default behaviour is environment-only, so an unexpected file cannot
    silently supply a credential."""
    monkeypatch.delenv("TEST_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("TEST_KEY=sk-sneaky\n")
    assert not ProviderConfig(api_key_env="TEST_KEY").is_configured


def test_describe_makes_a_stub_run_impossible_to_mistake_for_a_live_one(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert "stub" in describe("auto")
    assert "UNAVAILABLE" in describe("openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert "gpt-4o-mini" in describe("openai")
    assert "stub" not in describe("auto")


def test_a_missing_dotenv_file_warns_rather_than_silently_continuing(tmp_path, capsys):
    """An unreadable .env must be visible, not an invisible no-key run.

    Otherwise a typo in the path produces stub output while the operator
    believes they asked for a live one.
    """
    from financial_analyst.stages import stage2_agents

    stage2_agents.main(["--no-write", "--dotenv", str(tmp_path / "absent.env")])
    captured = capsys.readouterr()
    assert "absent.env" in captured.err
    assert "not found" in captured.err


def test_the_cli_announces_which_provider_will_run(capsys):
    """A finished-looking report written by a placeholder is the worst outcome."""
    from financial_analyst.stages import stage2_agents

    stage2_agents.main(["--no-write", "--ticker", "ACME"])
    assert "provider: stub" in capsys.readouterr().err


def test_the_key_never_appears_in_a_config_repr(monkeypatch):
    """A config object is safe to log, so the key must not ride along.

    ``api_key_env`` is the variable *name*, which is safe and useful. The
    credential itself is a property, and dataclass reprs only cover fields.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-supersecret")
    rendered = repr(ProviderConfig())
    assert "sk-supersecret" not in rendered
    assert "OPENAI_API_KEY" in rendered      # the name is fine, and useful


def test_a_missing_key_is_reported_before_a_missing_sdk(monkeypatch):
    """Both problems at once should name the one you can act on first.

    Installing the SDK is advice you cannot follow until you have a key, and it
    hides the fact that one is missing.
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from financial_analyst.agents.providers.openai_client import OpenAIClient

    with pytest.raises(CompletionError, match="OPENAI_API_KEY"):
        OpenAIClient().complete(system="s", user="u")


def test_a_missing_sdk_names_the_install_extra(monkeypatch):
    """With a key present, the SDK is the only thing left to fix."""
    from financial_analyst.agents.providers.openai_client import OpenAIClient

    monkeypatch.setenv("OPENAI_API_KEY", "sk-present")
    try:
        import openai  # noqa: F401
    except ImportError:
        with pytest.raises(CompletionError) as error:
            OpenAIClient().complete(system="s", user="u")
        assert "openai" in str(error.value)
        assert "provider='stub'" in str(error.value)
    else:
        pytest.skip("the openai SDK is installed, so this failure cannot occur")


def test_build_client_falls_back_to_stub_without_a_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert isinstance(build_client("auto"), ScriptedLLM)


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError, match="unknown provider"):
        build_client("nope")


def test_openai_adapter_reports_a_missing_sdk_or_key(monkeypatch):
    """Import-guarded: the module loads with no SDK, and fails clearly.

    Whichever guard trips first depends on whether ``openai`` is installed, so
    the message must name one of the two real causes rather than a stack trace.
    """
    from financial_analyst.agents.providers.openai_client import OpenAIClient

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(CompletionError) as error:
        OpenAIClient().complete(system="s", user="u")
    message = str(error.value)
    assert "openai" in message.lower()
    assert "not set" in message or "not installed" in message
    # The prompt must never appear in an error message.
    assert "system" not in message and "user" not in message


# ---------------------------------------------------------------------------
# Tool access
# ---------------------------------------------------------------------------
def test_tool_request_parsing_stops_at_the_line_end():
    """A ``\\s`` in that pattern swallowed every following line."""
    text = "some prose\nTOOL: revenue_cagr, gross_margin\nmore prose"
    assert parse_tool_request(text) == ("revenue_cagr", "gross_margin")


def test_no_tool_request_is_a_valid_answer():
    assert parse_tool_request("just prose") == ()
    assert parse_tool_request("") == ()


def test_tool_request_deduplicates():
    assert parse_tool_request("TOOL: gross_margin, gross_margin") == ("gross_margin",)


def test_citeable_metrics_are_stage1_columns_not_functions():
    """`cagr` is a function; `revenue_cagr` is the column an agent cites."""
    assert "revenue_cagr" in CITEABLE_METRICS
    assert "free_cash_flow" in CITEABLE_METRICS
    assert set(METRIC_COLUMNS) <= CITEABLE_METRICS


def test_execute_reports_unknown_names_rather_than_raising(synthetic_store):
    observation = execute(("gross_margin", "not_a_metric"), synthetic_store, "TST")
    assert observation.unknown == ("not_a_metric",)
    assert "gross_margin" in observation.values


def test_execute_preserves_undefined_as_undefined(synthetic_store):
    """The core invariant: `None` must reach the agent as undefined, not 0."""
    observation = execute(
        ("return_on_equity", "price_to_earnings"), synthetic_store, "LOSS"
    )
    assert observation.values == {}
    assert set(observation.undefined) == {"return_on_equity", "price_to_earnings"}
    assert "Do not estimate" in observation.render()


def test_execute_renders_each_family_naturally(store):
    rendered = execute(
        ("revenue_cagr", "debt_to_assets", "interest_coverage",
         "earnings_per_share", "revenue"),
        store, "ACME",
    ).values
    assert rendered["revenue_cagr"] == "9.9%"
    assert rendered["debt_to_assets"] == "13.6%"   # not 0.1358
    assert rendered["interest_coverage"].endswith("x")
    assert rendered["earnings_per_share"] == "0.96"
    assert rendered["revenue"] == "$1,750M"


def test_store_reports_what_stage1_declined(synthetic_store):
    assert set(synthetic_store.undefined_metrics("LOSS", "2024")) == {
        "return_on_equity", "price_to_earnings"
    }
    assert synthetic_store.undefined_metrics("TST", "2024") == ()


def test_catalogue_renders_for_the_prompt():
    text = ToolCatalogue.build().render()
    assert "`gross_margin`" in text and "higher_is_better" in text


# ---------------------------------------------------------------------------
# Claim verification -- the component that makes agent prose trustworthy
# ---------------------------------------------------------------------------
def test_honest_prose_passes_verification(synthetic_store):
    prose = (
        "Revenue compounded at 9.9% to $1,750M. Gross margin is 44.0% and "
        "interest coverage 8.5x."
    )
    assert verification.verify(prose, synthetic_store, "TST") == []


def test_a_hallucinated_figure_is_caught(synthetic_store):
    prose = "Revenue compounded at 12.0% while interest coverage was 3.2x."
    kinds = {issue.kind for issue in verification.verify(prose, synthetic_store, "TST")}
    assert "unverifiable_figure" in kinds


def test_rounding_is_not_mistaken_for_fabrication(synthetic_store):
    """0.099 rendered as 9.9% must reconcile; 12.0% must not."""
    assert verification.verify("Growth was 9.9%.", synthetic_store, "TST") == []
    issues = verification.verify("Growth was 12.0%.", synthetic_store, "TST")
    assert len(issues) == 1


def test_an_undefined_metric_cannot_be_numerified(synthetic_store):
    prose = "It trades at a price to earnings of 4.2x."
    kinds = {issue.kind for issue in verification.verify(prose, synthetic_store, "LOSS")}
    assert "numerified_undefined" in kinds


def test_correctly_declining_to_quote_passes(synthetic_store):
    prose = "It has negative net income, so price to earnings is not meaningful."
    assert verification.verify(prose, synthetic_store, "LOSS") == []


def test_bare_integers_are_not_claims(synthetic_store):
    """Years and list numbering would otherwise trigger false accusations."""
    prose = "Over 5 years and 2 segments, the company was reviewed."
    assert verification.extract_claims(prose) == []


def test_verification_against_an_unknown_ticker_says_so(synthetic_store):
    issues = verification.verify("Revenue grew 5.0%.", synthetic_store, "NOPE")
    assert [issue.kind for issue in issues] == ["unverifiable_figure"]
    assert "no Stage 1 metrics" in issues[0].detail


def test_percentage_points_are_normalised_like_stage1(synthetic_store):
    """pp figures become decimal fractions, matching how Stage 1 stores them."""
    claims = verification.extract_claims("Margin rose 2.8pp.")
    assert claims[0].kind == "points"
    assert claims[0].value == pytest.approx(0.028)


def test_negative_percent_and_multiple_keep_their_sign(synthetic_store):
    claims = verification.extract_claims("Net margin was -3.6% and interest coverage was -2.01x.")
    assert [(claim.text, claim.value, claim.kind) for claim in claims] == [
        ("-3.6%", pytest.approx(-0.036), "percent"),
        ("-2.01x", pytest.approx(-2.01), "multiple"),
    ]


def test_real_negative_stage1_values_reconcile(synthetic_store):
    prose = "Net margin was -3.6% and interest coverage was 0.36x."
    assert verification.verify(prose, synthetic_store, "LOSS") == []


def test_metric_and_year_named_value_must_match_that_specific_stage1_cell(store):
    prose = "net_debt_to_ebitda for FY2023 was 3.8x."
    issues = verification.verify(prose, store, "INIT")
    assert any(issue.kind == "unverifiable_figure" for issue in issues)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "verifier does not yet bind figures to a single named metric; "
        "3.8x vs 51.09x passes"
    ),
)
def test_single_metric_sentence_binds_all_figures_to_that_metric(store):
    """One metric named once, far from the figure -- the nearest-before rule
    misses it, so the figure is checked against every INIT value instead."""
    prose = (
        "the high net_debt_to_ebitda flag for FY2023 indicates a concerning "
        "level of debt relative to EBITDA at 3.8x."
    )
    issues = verification.verify(prose, store, "INIT")
    assert any(issue.kind == "unverifiable_figure" for issue in issues)


def test_nearest_preceding_metric_in_same_sentence_is_used(synthetic_store):
    prose = "Gross margin is 44.0% and interest coverage 8.5x."
    assert verification.verify(prose, synthetic_store, "TST") == []


def test_real_negative_values_with_nearest_metric_binding_pass(synthetic_store):
    prose = "Net margin was -3.6% and interest coverage was -0.84x."
    store = synthetic_store
    # Extend the synthetic store with a real negative interest coverage value for this check.
    store.data["LOSS"]["2023"] = dict(store.data["LOSS"]["2024"])
    store.data["LOSS"]["2023"]["interest_coverage"] = -0.84
    assert verification.verify(prose, store, "LOSS", year="2023") == []


def test_claim_with_no_nearby_metric_name_falls_back_to_any_value_check(synthetic_store):
    prose = "The company reported 44.0% in the latest period."
    assert verification.verify(prose, synthetic_store, "TST") == []

