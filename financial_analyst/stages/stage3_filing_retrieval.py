"""Stage 3: retrieve source passages from a company's latest annual SEC filing.

Example::

    FIN_ANALYST_SEC_USER_AGENT="My Name contact@example.com" \\
        python -m financial_analyst.stages.stage3_filing_retrieval \\
        --ticker AAPL --query "services revenue growth"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from .. import config
from ..filings import (
    EvidenceChunk,
    FilingMetadata,
    FilingRetrievalError,
    SECFilingClient,
    search_filing,
)

OUTPUT_JSON = "stage3_filing_retrieval.json"
OUTPUT_MD = "stage3_filing_retrieval.md"


def render_report(ticker: str, query: str, filing, evidence) -> str:
    lines = [
        f"# Filing evidence: {ticker}",
        "",
        f"Query: {query}",
        "",
        f"Source filing: Form {filing.form}, filed {filing.filing_date}, "
        f"reporting period {filing.report_date or 'not stated'}.",
        f"Filing: {filing.url}",
        "",
        "Retrieved passages are source material, not verified conclusions. "
        "Check each passage in the filing before relying on it.",
        "",
    ]
    if not evidence:
        lines.extend(["No passages matched the query terms.", ""])
    for item in evidence:
        lines.extend([
            f"## Result {item.paragraph} · relevance {item.score:.3f}",
            "",
            item.text,
            "",
            f"Source: {item.citation}",
            "",
        ])
    return "\n".join(lines)


def run_stage3(
    ticker: str,
    query: str,
    *,
    top_k: int = 5,
    user_agent: Optional[str] = None,
    output_dir: Optional[Path] = None,
    write: bool = True,
    client: Optional[SECFilingClient] = None,
) -> tuple[FilingMetadata, list[EvidenceChunk]]:
    """Retrieve ranked evidence passages from the latest annual filing."""
    client = client or SECFilingClient(user_agent=user_agent)
    filing = client.latest_annual_filing(ticker)
    html = client.filing_html(filing)
    evidence = search_filing(filing, html, query, top_k=top_k)

    if write:
        destination = Path(output_dir) if output_dir else config.OUTPUT_DIR
        destination.mkdir(parents=True, exist_ok=True)
        payload = {
            "ticker": filing.ticker,
            "query": query,
            "filing": filing.__dict__,
            "results": [item.to_dict() for item in evidence],
        }
        (destination / OUTPUT_JSON).write_text(json.dumps(payload, indent=2) + "\n")
        (destination / OUTPUT_MD).write_text(render_report(ticker, query, filing, evidence))
    return filing, evidence


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Stage 3 SEC filing evidence retrieval")
    parser.add_argument("--ticker", required=True, help="company ticker symbol")
    parser.add_argument("--query", required=True, help="topics or terms to find in the filing")
    parser.add_argument("--top-k", type=int, default=5, help="maximum passages to return")
    parser.add_argument(
        "--user-agent", default=None,
        help="identifying SEC request user agent; otherwise use FIN_ANALYST_SEC_USER_AGENT",
    )
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--no-write", action="store_true", help="print results without saving files")
    arguments = parser.parse_args(argv)

    try:
        filing, evidence = run_stage3(
            arguments.ticker,
            arguments.query,
            top_k=arguments.top_k,
            user_agent=arguments.user_agent,
            output_dir=Path(arguments.output_dir) if arguments.output_dir else None,
            write=not arguments.no_write,
        )
    except FilingRetrievalError as error:
        print(f"stage 3 error: {error}", file=sys.stderr)
        return 2

    print(
        f"source: {filing.company_name} {filing.form}, filed {filing.filing_date} "
        f"({filing.accession_number})"
    )
    if not evidence:
        print("No passages matched the query terms.")
    for item in evidence:
        print(f"\n[{item.score:.3f}] {item.text}\n{item.citation}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
