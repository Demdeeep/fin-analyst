"""Offline tests for SEC lookup and Stage 3 evidence retrieval."""

import json
from io import BytesIO

import pytest

from financial_analyst.filings import (
    FilingMetadata,
    FilingRetrievalError,
    SECFilingClient,
    search_filing,
)
from financial_analyst.stages.stage3_filing_retrieval import run_stage3


def _filing(**overrides):
    values = {
        "ticker": "TST",
        "company_name": "Test Corp",
        "cik": "0000000123",
        "form": "10-K",
        "filing_date": "2025-02-10",
        "report_date": "2024-12-31",
        "accession_number": "0000000123-25-000001",
        "primary_document": "annual.htm",
        "url": "https://www.sec.gov/Archives/test/annual.htm",
    }
    values.update(overrides)
    return FilingMetadata(**values)


def test_sec_client_requires_identifying_user_agent_before_request(monkeypatch):
    monkeypatch.delenv("FIN_ANALYST_SEC_USER_AGENT", raising=False)
    called = False

    def opener(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("should not make request")

    with pytest.raises(FilingRetrievalError, match="FIN_ANALYST_SEC_USER_AGENT"):
        SECFilingClient(opener=opener)
    assert not called


def test_sec_client_finds_exact_ticker_and_builds_latest_annual_url():
    ticker_payload = {"0": {"ticker": "TST", "cik_str": 123, "title": "Test Corp"}}
    submissions = {
        "filings": {"recent": {
            "form": ["10-K", "8-K", "20-F"],
            "filingDate": ["2025-02-10", "2025-03-01", "2024-03-12"],
            "reportDate": ["2024-12-31", "2025-02-28", "2023-12-31"],
            "accessionNumber": ["0000000123-25-000001", "0000000123-25-000002", "0000000123-24-000001"],
            "primaryDocument": ["annual.htm", "current.htm", "foreign.htm"],
        }}
    }
    requested = []

    def opener(request, timeout):
        requested.append((request.full_url, request.get_header("User-agent")))
        body = ticker_payload if "company_tickers" in request.full_url else submissions
        return BytesIO(json.dumps(body).encode())

    client = SECFilingClient("Fin Analyst contact@example.com", opener=opener)
    filing = client.latest_annual_filing("tst")

    assert filing.ticker == "TST"
    assert filing.cik == "0000000123"
    assert filing.form == "10-K"
    assert filing.accession_number == "0000000123-25-000001"
    assert filing.url.endswith("/123/000000012325000001/annual.htm")
    assert len(requested) == 2
    assert all(agent == "Fin Analyst contact@example.com" for _, agent in requested)


def test_sec_client_rejects_unknown_ticker():
    client = SECFilingClient(
        "Fin Analyst contact@example.com",
        opener=lambda request, timeout: BytesIO(b'{"0":{"ticker":"ABC","cik_str":1,"title":"ABC"}}'),
    )
    with pytest.raises(FilingRetrievalError, match="not found"):
        client.find_company("XYZ")


def test_search_filing_ranks_relevant_passages_and_attaches_citation():
    filing = _filing()
    html = """
    <html><head><title>Annual filing</title><script>revenue growth</script></head>
    <body>
      <h2>Business overview</h2>
      <p>Our products serve customers in several markets.</p>
      <p>Revenue from cloud services grew 18 percent, driven by higher customer demand.</p>
      <p>We face competition and changing regulations in global markets.</p>
    </body></html>
    """

    results = search_filing(
        filing, html, "cloud services revenue growth markets", top_k=2
    )

    assert len(results) == 2
    assert "cloud services grew" in results[0].text
    assert results[0].score > results[1].score
    assert results[0].source_url == filing.url
    assert "accession 0000000123-25-000001" in results[0].citation
    assert "revenue growth" not in " ".join(item.text for item in results)


def test_search_filing_returns_empty_for_no_matching_terms():
    assert search_filing(_filing(), "<p>Markets changed during the year.</p>", "liquidity waterfall") == []


def test_run_stage3_writes_cited_json_and_markdown(tmp_path):
    class FakeSECClient:
        def latest_annual_filing(self, ticker):
            assert ticker == "TST"
            return _filing()

        def filing_html(self, filing):
            return "<p>Cloud services revenue grew during the year.</p>"

    filing, evidence = run_stage3(
        "TST", "cloud services revenue", client=FakeSECClient(), output_dir=tmp_path
    )

    payload = json.loads((tmp_path / "stage3_filing_retrieval.json").read_text())
    report = (tmp_path / "stage3_filing_retrieval.md").read_text()
    assert filing.accession_number == payload["filing"]["accession_number"]
    assert payload["results"][0]["source_url"] == filing.url
    assert evidence[0].citation in report
    assert "Cloud services revenue grew" in report


@pytest.mark.parametrize(
    ("query", "top_k", "max_chars", "message"),
    [
        ("   ", 5, 1400, "Query cannot be blank"),
        ("revenue", 0, 1400, "top_k must be at least 1"),
        ("revenue", 1, 20, "max_chars must be at least 100"),
    ],
)
def test_search_filing_rejects_invalid_options(query, top_k, max_chars, message):
    with pytest.raises(FilingRetrievalError, match=message):
        search_filing(_filing(), "<p>Revenue increased.</p>", query, top_k=top_k, max_chars=max_chars)
