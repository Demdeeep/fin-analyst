"""Stage 4: cross-company comparison.

    outputs/stage1_metrics.json ─┐
    outputs/stage1_flags.csv    ─┴─► comparator ─► outputs/stage4_peer_comparison.{json,md}

    python -m financial_analyst.stages.stage4_peer_comparison
    python -m financial_analyst.stages.stage4_peer_comparison --scope sector
    python -m financial_analyst.stages.stage4_peer_comparison --provider openai

The peer arithmetic is done in :mod:`financial_analyst.agents.peers`; the model
writes prose about it. Figures in that prose are verified against Stage 1's
numbers *and* the peer table, so a hallucinated gap is caught the same way a
hallucinated margin is.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from .. import config
from ..agents import peers
from ..agents.comparator import DEFAULT_METRICS, ComparatorAgent
from ..agents.flags import load_flag_refs
from ..agents.providers import build_client, describe
from ..agents.tools import MetricStore
from ..agents.verification import verify

OUTPUT_JSON = "stage4_peer_comparison.json"
OUTPUT_MD = "stage4_peer_comparison.md"


def peer_values(result) -> tuple[float, ...]:
    """Every derived figure the model is entitled to quote.

    Values, medians and gaps. Ranks are integers and are not quoted as
    measurements, so they are excluded -- otherwise "rank 1" would be accepted
    as a figure.
    """
    values: list[float] = []
    for row in result.rows:
        if not row.computable:
            continue
        for candidate in (row.value, row.peer_median, row.gap):
            if candidate is not None:
                values.append(float(candidate))
    return tuple(values)


def render_report(results, provider: str, scope: str) -> str:
    comparable = sum(1 for result in results if result.any_comparable)
    lines = [
        "# Stage 4 peer comparison",
        "",
        f"Provider: `{provider}`. Scope: `{scope}`. "
        f"{len(results)} companies, {comparable} with at least one comparable metric.",
        "",
        f"Groups smaller than {config.MIN_PEERS_FOR_COMPARISON} companies are declined: "
        "a median of two companies is not a median.",
        "",
    ]
    for result in results:
        lines += [
            f"## {result.ticker}",
            "",
            f"Peer group: {result.group or 'none'} · "
            f"{len(result.computable)} compared, {len(result.declined)} declined",
            "",
            result.narrative,
            "",
        ]
        table = peers.render_table(result.rows)
        lines += ["<details><summary>Peer table</summary>", "", table, "", "</details>", ""]
    lines += [
        "---",
        "",
        "Figures are computed in `agents/peers.py` and verified against Stage 1's "
        "output. A declined row is a deliberate refusal, not missing data.",
        "",
    ]
    return "\n".join(lines)


def run_stage4(
    metrics_path: Optional[Path] = None,
    flags_path: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    provider: str = "stub",
    model: Optional[str] = None,
    scope: str = config.PEER_SCOPE_SECTOR,
    tickers: Optional[list[str]] = None,
    write: bool = True,
) -> list:
    """Compare every company against its peers, or just ``tickers``."""
    from .. import config as cfg

    metrics_path = Path(metrics_path or cfg.OUTPUT_METRICS_JSON)
    flags_path = Path(flags_path or cfg.OUTPUT_FLAGS_CSV)
    if not metrics_path.exists():
        raise FileNotFoundError(
            f"{metrics_path} not found -- run Stage 1 first "
            f"(python -m financial_analyst.stages.stage1_static_csv)"
        )

    store = MetricStore.from_json(metrics_path)
    if store.empty:
        raise ValueError(f"{metrics_path} contains no companies")

    flags_by_ticker = load_flag_refs(flags_path)
    comparator = ComparatorAgent(
        build_client(provider, model=model), store, metrics=DEFAULT_METRICS, scope=scope
    )

    results = []
    for ticker in (tickers or list(store.tickers)):
        result = comparator.compare(ticker, flags=flags_by_ticker.get(ticker, ()))
        issues = verify(
            result.narrative, store, ticker, extra_values=peer_values(result)
        )
        results.append((result, issues))

    if write:
        destination = Path(output_dir) if output_dir else cfg.OUTPUT_DIR
        destination.mkdir(parents=True, exist_ok=True)
        (destination / OUTPUT_JSON).write_text(json.dumps({
            "provider": provider,
            "scope": scope,
            "min_peers": cfg.MIN_PEERS_FOR_COMPARISON,
            "companies": [
                {**result.to_dict(), "issues": [str(i) for i in issues]}
                for result, issues in results
            ],
        }, indent=2) + "\n")
        (destination / OUTPUT_MD).write_text(
            render_report([r for r, _ in results], provider, scope)
        )
    return results


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Stage 4 peer comparison")
    parser.add_argument("--provider", default="stub",
                        choices=("stub", "openai", "anthropic", "auto"))
    parser.add_argument("--model", default=None,
                        help="override the model id (default gpt-4o-mini / "
                             "claude-haiku-4-5-20251001 by provider)")
    parser.add_argument("--scope", default=config.PEER_SCOPE_SECTOR,
                        choices=(config.PEER_SCOPE_SECTOR, config.PEER_SCOPE_SAMPLE))
    parser.add_argument("--ticker", action="append", dest="tickers")
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--dotenv", default=None,
                        help="path to a .env file holding the selected provider's API key")
    arguments = parser.parse_args(argv)

    # Announce the provider before spending anything, so `--provider auto` with
    # no key is visibly not a live run.
    if arguments.dotenv is not None:
        from ..agents.providers.base import load_dotenv

        if not load_dotenv(arguments.dotenv):
            print(f"warning: {arguments.dotenv} not found", file=sys.stderr)
    print(f"provider: {describe(arguments.provider, model=arguments.model)}", file=sys.stderr)

    results = run_stage4(
        provider=arguments.provider,
        model=arguments.model,
        scope=arguments.scope,
        tickers=arguments.tickers,
        write=not arguments.no_write,
    )
    for result, issues in results:
        print(
            f"{result.ticker:6} {len(result.computable)} compared, "
            f"{len(result.declined)} declined, {len(issues)} issue(s)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
