"""Small read-only client for public SEC EDGAR company filings.

SEC endpoints are public and do not require an API key. Automated requests do
require an identifying User-Agent; callers must provide one rather than the
package inventing a contact address.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class FilingRetrievalError(RuntimeError):
    """A filing could not be found or retrieved from EDGAR."""


@dataclass(frozen=True)
class FilingMetadata:
    ticker: str
    company_name: str
    cik: str
    form: str
    filing_date: str
    report_date: str
    accession_number: str
    primary_document: str
    url: str


class SECFilingClient:
    """Fetch a company's latest annual filing and its primary document."""

    TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
    SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
    ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
    ANNUAL_FORMS = frozenset({"10-K", "20-F", "40-F"})

    def __init__(
        self,
        user_agent: Optional[str] = None,
        *,
        opener: Callable = urlopen,
        timeout: float = 25.0,
    ) -> None:
        self.user_agent = (user_agent or os.environ.get("FIN_ANALYST_SEC_USER_AGENT", "")).strip()
        if not self.user_agent:
            raise FilingRetrievalError(
                "Set FIN_ANALYST_SEC_USER_AGENT to an identifying name and contact "
                "email before downloading SEC filings."
            )
        self._opener = opener
        self.timeout = timeout

    def _get_bytes(self, url: str) -> bytes:
        request = Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
            },
        )
        try:
            response = self._opener(request, timeout=self.timeout)
            with response:
                return response.read()
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            status = getattr(error, "code", None)
            detail = f" (HTTP {status})" if status else ""
            raise FilingRetrievalError(
                f"SEC request failed{detail}; retry later and check the SEC fair-access policy."
            ) from error

    def _get_json(self, url: str) -> dict:
        try:
            return json.loads(self._get_bytes(url))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise FilingRetrievalError("SEC returned invalid JSON.") from error

    def find_company(self, ticker: str) -> tuple[str, str]:
        """Return ``(zero-padded CIK, company name)`` for an exact ticker."""
        normalized = ticker.strip().upper()
        if not normalized:
            raise FilingRetrievalError("Ticker cannot be blank.")
        payload = self._get_json(self.TICKERS_URL)
        entries = payload.values() if isinstance(payload, dict) else ()
        for entry in entries:
            if str(entry.get("ticker", "")).upper() == normalized:
                return f"{int(entry['cik_str']):010d}", str(entry["title"])
        raise FilingRetrievalError(f"Ticker {normalized} was not found in the SEC company list.")

    def latest_annual_filing(self, ticker: str) -> FilingMetadata:
        """Find the latest 10-K, 20-F or 40-F and construct its SEC source URL."""
        normalized = ticker.strip().upper()
        cik, company_name = self.find_company(normalized)
        submissions = self._get_json(self.SUBMISSIONS_URL.format(cik=cik))
        recent = submissions.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        selected_index = next(
            (i for i, form in enumerate(forms) if form in self.ANNUAL_FORMS), None
        )
        if selected_index is None:
            raise FilingRetrievalError(f"No recent annual filing found for {normalized}.")

        def field(name: str) -> str:
            values = recent.get(name, [])
            return str(values[selected_index]) if selected_index < len(values) else ""

        accession = field("accessionNumber")
        document = field("primaryDocument")
        if not accession or not document:
            raise FilingRetrievalError(
                f"The SEC filing record for {normalized} is missing its accession or document."
            )
        if not re.fullmatch(r"[A-Za-z0-9._-]+", document):
            raise FilingRetrievalError("The SEC returned an invalid primary document name.")
        numeric_cik = str(int(cik))
        url = self.ARCHIVES_URL.format(
            cik=numeric_cik,
            accession=accession.replace("-", ""),
            document=document,
        )
        return FilingMetadata(
            ticker=normalized,
            company_name=company_name,
            cik=cik,
            form=field("form"),
            filing_date=field("filingDate"),
            report_date=field("reportDate"),
            accession_number=accession,
            primary_document=document,
            url=url,
        )

    def filing_html(self, filing: FilingMetadata) -> str:
        """Download a filing document as UTF-8 HTML."""
        try:
            return self._get_bytes(filing.url).decode("utf-8", errors="replace")
        except FilingRetrievalError:
            raise
