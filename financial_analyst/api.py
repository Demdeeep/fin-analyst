"""HTTP API for the Netlify Northstar interface.

The API deliberately delegates to the existing Python pipeline. It does not
persist uploads or write analysis artifacts to the repository.
"""

from __future__ import annotations

import json
import os
import hmac
import tempfile
from pathlib import Path
from typing import Optional

import pandas as pd
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from .agents.providers import build_client
from .documents.financial_tables import (
    FinancialTableExtractionError,
    extract_financial_tables,
    make_stage1_csv_rows,
)
from .documents.pdf import PDFExtractionError, extract_pdf
from .stages.stage1_static_csv import metrics_to_nested_json, run_stage1
from .stages.stage2_agents import run_stage2
from .stages.stage4_peer_comparison import run_stage4
from .tools import anomalies

API_VERSION = "1.0"
MAX_CSV_BYTES = 10_000_000
MAX_PDF_BYTES = 25_000_000

app = FastAPI(
    title="Northstar Analysis API",
    version=API_VERSION,
    description="Python API that runs Northstar's existing financial analysis pipeline.",
)

_origins = [
    value.strip()
    for value in os.getenv("NORTHSTAR_CORS_ORIGINS", "").split(",")
    if value.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_origin_regex=r"^https://[a-zA-Z0-9-]+\.netlify\.app$" if not _origins else None,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Northstar-Token"],
)


def _authorize(x_northstar_token: Optional[str] = Header(default=None)) -> None:
    """Optionally protect analysis endpoints with a shared preview token.

    The token is not a replacement for private-app authentication. It prevents
    casual third-party calls to a test API that has live model keys configured.
    """
    expected = os.getenv("NORTHSTAR_API_TOKEN", "")
    if expected and not hmac.compare_digest(x_northstar_token or "", expected):
        raise HTTPException(status_code=401, detail="Enter the API access token in Connection settings.")


def _require_live_access(provider: str) -> None:
    if provider != "stub" and not os.getenv("NORTHSTAR_API_TOKEN"):
        raise HTTPException(
            status_code=503,
            detail="Live providers are disabled until the backend has NORTHSTAR_API_TOKEN configured.",
        )


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "northstar-python", "version": API_VERSION}


async def _read_upload(upload: UploadFile, *, limit: int) -> bytes:
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status_code=413, detail=f"File must be {limit // 1_000_000} MB or smaller.")
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    return data


def _metrics_records(frame: pd.DataFrame) -> list[dict]:
    # Pandas' JSON encoder maps NaN/NaT to JSON null and handles numpy scalars.
    return json.loads(frame.to_json(orient="records", date_format="iso"))


def _stage1_from_bytes(data: bytes, filename: str):
    if len(data) > MAX_CSV_BYTES:
        raise HTTPException(status_code=413, detail="CSV files must be 10 MB or smaller.")
    with tempfile.TemporaryDirectory(prefix="northstar-api-") as directory:
        source = Path(directory) / (Path(filename).name or "upload.csv")
        source.write_bytes(data)
        try:
            result = run_stage1(source, write=False)
        except Exception as error:
            # Validation text is useful to fix the uploaded file. Avoid returning
            # stack traces or local filesystem details to the browser.
            raise HTTPException(status_code=422, detail=str(error)) from error
        return result


def _write_stage_artifacts(stage1, directory: Path) -> tuple[Path, Path]:
    metrics_path = directory / "stage1_metrics.json"
    flags_path = directory / "stage1_flags.csv"
    metrics_path.write_text(json.dumps(metrics_to_nested_json(stage1.metrics), allow_nan=False))
    anomalies.flags_to_frame(stage1.flags).to_csv(flags_path, index=False)
    return metrics_path, flags_path


@app.post("/api/analyze/csv")
async def analyze_csv(file: UploadFile = File(...), _: None = Depends(_authorize)) -> dict:
    data = await _read_upload(file, limit=MAX_CSV_BYTES)
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=415, detail="Upload a CSV file.")
    result = _stage1_from_bytes(data, file.filename or "upload.csv")
    return {
        "filename": Path(file.filename or "upload.csv").name,
        "tickers": list(result.dataset.tickers),
        "company_count": len(result.dataset.tickers),
        "record_count": len(result.metrics),
        "metrics": _metrics_records(result.metrics),
        "flags": [flag.to_dict() for flag in result.flags],
        "warnings": list(result.dataset.warnings),
        "report": result.report,
        "metrics_csv": result.metrics.to_csv(index=False),
    }


@app.post("/api/analyze/agents")
async def analyze_agents(
    file: UploadFile = File(...),
    tickers_json: str = Form("[]"),
    provider: str = Form("stub"),
    model: Optional[str] = Form(None),
    filing_query: Optional[str] = Form(None),
    sec_user_agent: Optional[str] = Form(None),
) -> dict:
    data = await _read_upload(file, limit=MAX_CSV_BYTES)
    try:
        tickers = json.loads(tickers_json)
        if not isinstance(tickers, list) or any(not isinstance(item, str) for item in tickers):
            raise ValueError
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Selected tickers must be a JSON array of strings.") from error
    if provider not in {"stub", "openai", "anthropic"}:
        raise HTTPException(status_code=400, detail="Choose stub, openai, or anthropic.")
    _require_live_access(provider)
    stage1 = _stage1_from_bytes(data, file.filename or "upload.csv")
    unknown = sorted(set(tickers) - set(stage1.dataset.tickers))
    if unknown:
        raise HTTPException(status_code=422, detail=f"Ticker(s) not found in CSV: {', '.join(unknown)}")
    with tempfile.TemporaryDirectory(prefix="northstar-api-") as directory:
        metrics_path, flags_path = _write_stage_artifacts(stage1, Path(directory))
        try:
            results = run_stage2(
                metrics_path=metrics_path,
                flags_path=flags_path,
                provider=provider,
                model=model or None,
                filing_query=filing_query.strip() or None if filing_query else None,
                sec_user_agent=sec_user_agent or None,
                tickers=tickers or None,
                write=False,
            )
        except Exception as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    return {"provider": provider, "results": [result.to_dict() for result in results]}


@app.post("/api/analyze/peers")
async def analyze_peers(
    file: UploadFile = File(...),
    tickers_json: str = Form("[]"),
    provider: str = Form("stub"),
    model: Optional[str] = Form(None),
    scope: str = Form("sector"),
    _: None = Depends(_authorize),
) -> dict:
    data = await _read_upload(file, limit=MAX_CSV_BYTES)
    try:
        tickers = json.loads(tickers_json)
        if not isinstance(tickers, list) or any(not isinstance(item, str) for item in tickers):
            raise ValueError
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Selected tickers must be a JSON array of strings.") from error
    if provider not in {"stub", "openai", "anthropic"}:
        raise HTTPException(status_code=400, detail="Choose stub, openai, or anthropic.")
    _require_live_access(provider)
    stage1 = _stage1_from_bytes(data, file.filename or "upload.csv")
    with tempfile.TemporaryDirectory(prefix="northstar-api-") as directory:
        metrics_path, flags_path = _write_stage_artifacts(stage1, Path(directory))
        try:
            results = run_stage4(
                metrics_path=metrics_path,
                flags_path=flags_path,
                provider=provider,
                model=model or None,
                scope=scope,
                tickers=tickers or None,
                write=False,
            )
        except Exception as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "provider": provider,
        "results": [
            {**result.to_dict(), "issues": [str(issue) for issue in issues]}
            for result, issues in results
        ],
    }


@app.post("/api/extract/pdf")
async def extract_pdf_api(file: UploadFile = File(...), _: None = Depends(_authorize)) -> dict:
    data = await _read_upload(file, limit=MAX_PDF_BYTES)
    try:
        document = extract_pdf(data)
    except PDFExtractionError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    try:
        tables = extract_financial_tables(data)
        tables_payload = [
            {
                "page": table.page,
                "table": table.table,
                "statement": table.statement,
                "reported_unit": table.reported_unit,
                "rows": [list(row) for row in table.rows],
                "values": [value.to_dict() for value in table.values],
            }
            for table in tables
        ]
        table_error = None
    except FinancialTableExtractionError as error:
        tables_payload = []
        table_error = str(error)
    return {
        "filename": Path(file.filename or "document.pdf").name,
        "page_count": document.page_count,
        "text_characters": document.text_characters,
        "pages": [{"number": page.number, "text": page.text} for page in document.pages],
        "tables": tables_payload,
        "table_error": table_error,
    }


@app.post("/api/extract/pdf/stage1-csv")
async def make_pdf_stage1_csv(
    file: UploadFile = File(...),
    ticker: str = Form(...),
    company_name: str = Form(...),
    _: None = Depends(_authorize),
) -> dict:
    data = await _read_upload(file, limit=MAX_PDF_BYTES)
    if not ticker.strip() or not company_name.strip():
        raise HTTPException(status_code=400, detail="Enter a ticker and company name.")
    try:
        tables = extract_financial_tables(data)
    except FinancialTableExtractionError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    values = [value for table in tables for value in table.values]
    rows, notes = make_stage1_csv_rows(values, ticker=ticker, company_name=company_name)
    return {"columns": list(rows[0]) if rows else [], "rows": rows, "notes": notes}


@app.post("/api/analyze/pdf")
async def analyze_pdf_api(
    file: UploadFile = File(...),
    question: str = Form(...),
    provider: str = Form(...),
    model: Optional[str] = Form(None),
    consent: bool = Form(False),
    _: None = Depends(_authorize),
) -> dict:
    if not consent:
        raise HTTPException(status_code=400, detail="Explicit consent is required before sending PDF text to a model provider.")
    if provider not in {"openai", "anthropic"}:
        raise HTTPException(status_code=400, detail="Choose OpenAI or Anthropic for PDF narrative analysis.")
    _require_live_access(provider)
    if not question.strip():
        raise HTTPException(status_code=400, detail="Enter a question about the document.")
    data = await _read_upload(file, limit=MAX_PDF_BYTES)
    try:
        document = extract_pdf(data)
    except PDFExtractionError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    excerpts = []
    remaining = 40_000
    for page in document.pages:
        if not page.text or remaining <= 0:
            continue
        excerpt = page.text[:remaining]
        excerpts.append(f"[p. {page.number}]\n{excerpt}")
        remaining -= len(excerpt)
    try:
        client = build_client(provider, model=model or None)
        narrative = client.complete(
            system=(
                "You are Northstar's company-document analyst. Use only the supplied PDF text. "
                "Do not invent facts or treat document claims as independently verified. Cite every "
                "document-based factual statement with its page marker, such as [p. 4]. If a page "
                "does not provide the requested information, say so. Separate reported facts from "
                "your interpretation, and do not present this as investment advice."
            ),
            user=(f"Question: {question.strip()}\n\nDocument: {file.filename or 'document.pdf'}\n\n"
                  "Extracted pages (up to 40,000 characters):\n\n" + "\n\n".join(excerpts)),
        )
    except Exception as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"provider": provider, "narrative": narrative, "pages_used": len(excerpts), "characters_sent": 40_000 - remaining}
