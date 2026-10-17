"""Northstar local dashboard for the financial analyst pipeline."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from financial_analyst import config
from financial_analyst.agents.providers import build_client
from financial_analyst.documents.pdf import PDFExtractionError, extract_pdf
from financial_analyst.documents.financial_tables import (
    FinancialTableExtractionError,
    STAGE1_CSV_COLUMNS,
    compare_with_stage1,
    extract_financial_tables,
    make_stage1_csv_rows,
)
from financial_analyst.stages.stage1_static_csv import metrics_to_nested_json, run_stage1
from financial_analyst.stages.stage2_agents import run_stage2
from financial_analyst.stages.stage4_peer_comparison import run_stage4
from financial_analyst.tools import anomalies


st.set_page_config(page_title="Northstar | Retro Research Terminal", page_icon="✦", layout="wide")


def load_hosted_secrets() -> None:
    """Expose Streamlit-hosted secrets to provider adapters as environment vars.

    Shell environment variables keep precedence for local runs. Values are
    never rendered or logged; provider clients read them only when called.
    """
    for name in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "FIN_ANALYST_SEC_USER_AGENT",
    ):
        if os.environ.get(name):
            continue
        try:
            value = st.secrets.get(name)
        except StreamlitSecretNotFoundError:
            continue
        if value:
            os.environ[name] = str(value)


load_hosted_secrets()

st.markdown(
    """
    <style>
    [data-testid="stAppViewContainer"] {
        background-color: #0d1311;
        background-image: radial-gradient(#202b25 0.7px, transparent 0.7px);
        background-size: 15px 15px;
    }
    [data-testid="stHeader"] { background: rgba(13,19,17,.94); }
    [data-testid="stSidebar"] { background: #111b17; border-right: 3px solid #b88c42; }
    [data-testid="stSidebar"] * { color: #e9e0c9; }
    .block-container { max-width: 1440px; padding-top: 1.5rem; padding-bottom: 4rem; }
    .northstar-hero {
        background: #14231d; color: #eee4ce; padding: 1.7rem 2rem;
        border: 2px solid #bd9146; border-radius: 3px; margin-bottom: 1.2rem;
        box-shadow: 7px 7px 0 #663a2d;
    }
    .northstar-mark { color: #e0b85c; font: 700 .9rem/1.2 monospace; letter-spacing: .22em; }
    .northstar-hero h1 { color: #f5e8c9; margin: .5rem 0 .35rem; font: 700 2.45rem/1.1 Georgia, serif; }
    .northstar-hero p { color: #c6cbb2; font: .95rem/1.5 monospace; margin: 0; }
    h1, h2, h3, h4 { color: #eee4ce; font-family: Georgia, serif; }
    p, li, label, [data-testid="stMarkdownContainer"] { color: #ddd7c5; }
    a { color: #e0b85c !important; }
    [data-testid="stMetric"] {
        background: #17221d; border: 1px solid #53604a; border-radius: 3px;
        padding: 1rem 1.2rem; box-shadow: 4px 4px 0 #33271e;
    }
    [data-testid="stMetricLabel"] { color: #b4b99f; font: .76rem monospace; text-transform: uppercase; letter-spacing: .07em; }
    [data-testid="stMetricValue"] { color: #e0b85c; font-family: monospace; }
    .stButton > button { background: #1c2923; border: 1px solid #718065; color: #eee4ce; border-radius: 3px; box-shadow: 3px 3px 0 #33271e; font-weight: 700; }
    .stButton > button[kind="primary"] { background: #a44f32; border-color: #d19c52; color: #fff3d6; }
    [data-testid="stTabs"] button { font: 700 .88rem monospace; color: #e2d3ae; }
    [data-testid="stExpander"] { background: #151f1a; border: 1px solid #465347; border-radius: 3px; }
    [data-testid="stFileUploader"] { background: #151f1a; border: 1px dashed #9b804b; border-radius: 3px; }
    [data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea { background: #101915; color: #eee4ce; }
    [data-testid="stSelectbox"] [data-baseweb="select"] { background: #101915; }
    [data-testid="stAlert"] { background: #19231e; }
    hr { border-color: #3b483d; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="northstar-hero">
      <div class="northstar-mark">✦ NORTHSTAR / RESEARCH TERMINAL / VOL. 01</div>
      <h1>Company analysis, with the receipts.</h1>
      <p>Fundamentals · filing notes · peer comparisons &nbsp;|&nbsp; Built for careful reading.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.markdown("## ✦ Northstar")
    st.caption("RETRO RESEARCH TERMINAL")
    st.divider()
    st.subheader("Financial data")
    uploaded = st.file_uploader("Upload a financials CSV", type=["csv"])
    st.caption("Use the required columns in `data/schemas/financials_schema.md`.")

    st.subheader("Analysis settings")
    provider = st.selectbox("Language model", ["stub", "openai", "anthropic"], index=0)
    model = st.text_input("Model override", placeholder="Use provider default")
    use_filings = st.checkbox("Add SEC filing context", value=False)
    filing_query = st.text_input(
        "Filing search question", placeholder="What risks could affect demand?",
        disabled=not use_filings,
    )
    sec_user_agent = st.text_input(
        "SEC contact email", type="password", disabled=not use_filings,
        value=os.environ.get("FIN_ANALYST_SEC_USER_AGENT", ""),
        help="Required by SEC EDGAR; used only for this filing search.",
    )
    st.divider()
    st.caption("Stub mode is free and makes no model calls.")
    if provider != "stub":
        st.warning(
            f"Running Stage 2 or Stage 4 with {provider} sends the selected company metrics to that provider."
        )

dashboard_tab, pdf_tab = st.tabs(["📊  Company dashboard", "📄  PDF review"])

if uploaded is None:
    input_bytes = config.DEFAULT_INPUT_CSV.read_bytes()
    input_name = config.DEFAULT_INPUT_CSV.name
else:
    input_bytes = uploaded.getvalue()
    input_name = uploaded.name

with dashboard_tab:
    if uploaded is None:
        st.info(f"Using the included sample dataset: **{input_name}**. Upload your own CSV in the sidebar to switch datasets.")

    with tempfile.TemporaryDirectory(prefix="northstar-") as temporary:
        workspace = Path(temporary)
        csv_path = workspace / input_name
        csv_path.write_bytes(input_bytes)

        if st.button("Run Stage 1 · Calculate metrics", type="primary"):
            try:
                stage1 = run_stage1(csv_path, write=False)
                st.session_state.stage1 = stage1
                st.session_state.stage1_input = input_bytes
                st.session_state.stage2 = None
                st.session_state.stage4 = None
            except Exception as exc:
                st.error(f"Stage 1 could not process this CSV: {exc}")

        stage1 = st.session_state.get("stage1")
        if stage1 is not None and st.session_state.get("stage1_input") != input_bytes:
            stage1 = None
            st.session_state.stage2 = None
            st.session_state.stage4 = None

        if stage1 is None:
            st.markdown("### Start with a company dataset")
            st.write("Run Stage 1 to calculate financial ratios, validate the input, and identify items that need attention.")
            st.markdown("**Workflow:** Stage 1 fundamentals → Stage 2 analyst review → Stage 3 filing context → Stage 4 peer comparison")
        else:
            st.subheader("Stage 1 · Financial snapshot")
            first, second, third = st.columns(3)
            first.metric("Companies", len(stage1.dataset.tickers))
            second.metric("Company-year records", len(stage1.metrics))
            third.metric("Flags to review", len(stage1.flags))

            with st.expander("View calculated metrics", expanded=True):
                st.dataframe(stage1.metrics, width="stretch", hide_index=True)
            if stage1.flags:
                with st.expander("Review anomaly flags", expanded=True):
                    st.dataframe(anomalies.flags_to_frame(stage1.flags), width="stretch", hide_index=True)
            else:
                st.success("No anomaly flags were found in this dataset.")
            st.download_button(
                "Download metrics CSV", stage1.metrics.to_csv(index=False),
                file_name="company_metrics.csv", mime="text/csv",
            )

            st.divider()
            st.subheader("Stage 2 · Analyst review")
            tickers = st.multiselect(
                "Choose companies", stage1.dataset.tickers,
                default=list(stage1.dataset.tickers),
            )
            if use_filings:
                st.caption("Stage 3 retrieves relevant annual filing excerpts and attaches source references to the Stage 2 review.")
            if st.button("Run analyst review", disabled=not tickers):
                if use_filings and not filing_query.strip():
                    st.error("Enter a filing search question or turn off SEC filing context.")
                elif use_filings and not sec_user_agent.strip():
                    st.error("Enter an identifying SEC contact email for EDGAR requests.")
                else:
                    metrics_path = workspace / "stage1_metrics.json"
                    flags_path = workspace / "stage1_flags.csv"
                    metrics_path.write_text(json.dumps(metrics_to_nested_json(stage1.metrics), indent=2))
                    anomalies.flags_to_frame(stage1.flags).to_csv(flags_path, index=False)
                    try:
                        st.session_state.stage2 = run_stage2(
                            metrics_path=metrics_path, flags_path=flags_path,
                            provider=provider, model=model.strip() or None,
                            filing_query=filing_query.strip() if use_filings else None,
                            sec_user_agent=sec_user_agent.strip() if use_filings else None,
                            tickers=tickers, write=False,
                        )
                        st.session_state.stage2_provider = provider
                    except Exception as exc:
                        st.error(f"Stage 2 could not complete: {exc}")

            for result in (st.session_state.get("stage2") or []):
                verdict_messages = {
                    "OK": ("✅ Review passed", "The figures checked against Stage 1 and the required flags were addressed."),
                    "REVISE": ("🟠 Needs another look", "The review found an issue that may need corrected wording or closer inspection."),
                    "REJECT": ("🔴 Review not accepted", "At least one required item was missed or a claim could not be supported."),
                }
                verdict_title, verdict_help = verdict_messages.get(
                    result.verdict, ("Review status", "See the findings below.")
                )
                with st.container(border=True):
                    st.markdown(f"### {result.ticker} · {verdict_title}")
                    st.caption(verdict_help)
                    summary_a, summary_b, summary_c = st.columns(3)
                    summary_a.metric("Figures", "All checked" if result.verified else f"{len(result.issues)} to review")
                    summary_b.metric("Critical flags addressed", f"{result.flags_addressed} / {result.flags_considered}")
                    summary_c.metric("Revision attempts", result.revisions)

                    st.markdown("#### Analyst summary")
                    st.markdown(result.narrative or "No narrative was returned.")
                    if result.coverage:
                        unresolved = [str(item) for item in result.coverage if item.needs_review]
                        if unresolved:
                            st.markdown("#### Flags still needing attention")
                            st.dataframe(pd.DataFrame([
                                {
                                    "Year": item.flag.year_label,
                                    "Severity": item.flag.severity.title(),
                                    "Check": item.flag.rule.replace("_", " ").capitalize(),
                                    "What Stage 1 found": item.flag.message,
                                    "Review status": item.status.replace("_", " ").capitalize(),
                                }
                                for item in result.coverage if item.needs_review
                            ]), width="stretch", hide_index=True)
                        elif result.flags_considered:
                            st.success("All required flags were addressed in the analyst summary.")
                    if result.issues:
                        st.markdown("#### Figures to check against the source CSV")
                        st.dataframe(pd.DataFrame([
                            {"Finding": issue.kind.replace("_", " ").capitalize(), "Details": issue.detail}
                            for issue in result.issues
                        ]), width="stretch", hide_index=True)
                    elif result.verified:
                        st.success("Every numeric claim checked by the verifier matches Stage 1.")
                    if result.filing_sources:
                        st.markdown("#### Filing sources")
                        for source in result.filing_sources:
                            st.markdown(f"- [{source['source_id']}]({source['source_url']}) — {source['citation']}")
                    if result.citation_issues:
                        with st.expander("Filing citation checks"):
                            for issue in result.citation_issues:
                                st.write(str(issue))
                    if result.critic_notes:
                        with st.expander("Reviewer notes"):
                            st.write(result.critic_notes)

            st.divider()
            st.subheader("Stage 4 · Peer comparison")
            peer_scope = st.selectbox("Peer group", ["sector", "sample"])
            if st.button("Compare peers", disabled=not tickers):
                metrics_path = workspace / "stage1_metrics.json"
                flags_path = workspace / "stage1_flags.csv"
                metrics_path.write_text(json.dumps(metrics_to_nested_json(stage1.metrics), indent=2))
                anomalies.flags_to_frame(stage1.flags).to_csv(flags_path, index=False)
                try:
                    st.session_state.stage4 = run_stage4(
                        metrics_path=metrics_path, flags_path=flags_path,
                        provider=provider, model=model.strip() or None,
                        scope=peer_scope, tickers=tickers, write=False,
                    )
                except Exception as exc:
                    st.error(f"Stage 4 could not complete: {exc}")

            for comparison, issues in (st.session_state.get("stage4") or []):
                with st.expander(f"{comparison.ticker} · {len(comparison.computable)} metrics compared"):
                    st.markdown(comparison.narrative)
                    if comparison.rows:
                        st.dataframe(pd.DataFrame([row.to_dict() for row in comparison.rows]), width="stretch", hide_index=True)
                    if issues:
                        st.error("Figure verification: " + "; ".join(map(str, issues)))

with pdf_tab:
    st.subheader("Review a company PDF")
    st.write("Upload an annual report, investor presentation, or other company document. Northstar extracts selectable text locally and keeps page references for review.")
    pdf_upload = st.file_uploader("Choose a PDF", type=["pdf"], key="company_pdf")
    if pdf_upload is None:
        st.info("PDF review is separate from the CSV-based ratio checks. Start with a text-based PDF; scanned pages need OCR, which is not included yet.")
    else:
        pdf_bytes = pdf_upload.getvalue()
        pdf_digest = hashlib.sha256(pdf_bytes).hexdigest()
        try:
            document = extract_pdf(pdf_bytes)
            metric_a, metric_b = st.columns(2)
            metric_a.metric("Pages", document.page_count)
            metric_b.metric("Extracted text", f"{document.text_characters:,} characters")
            if document.text_characters > 40_000:
                st.warning("The live analysis uses up to the first 40,000 characters. Review the page excerpts below to see the available text.")
            for page in document.pages[:8]:
                if page.text:
                    with st.expander(f"Page {page.number}"):
                        st.text(page.text[:5_000] or "No selectable text on this page.")

            st.markdown("#### Extract financial statement rows")
            st.caption("Local rule-based extraction · no model or API credits · review the cited source row before relying on a value.")
            table_digest_key = f"financial_tables_{pdf_digest}"
            if st.button("Extract financial tables", key="extract_pdf_financial_tables"):
                try:
                    with st.spinner("Searching statement pages and reading table rows locally…"):
                        st.session_state[table_digest_key] = extract_financial_tables(pdf_bytes)
                except FinancialTableExtractionError as exc:
                    st.error(str(exc))

            if table_digest_key in st.session_state:
                extracted_tables = st.session_state[table_digest_key]
                financial_rows = [
                    value.to_dict()
                    for table in extracted_tables
                    for value in table.values
                ]
                if financial_rows:
                    table_metric, value_metric = st.columns(2)
                    table_metric.metric("Statement tables", len(extracted_tables))
                    value_metric.metric("Recognized values", len(financial_rows))
                    if any(row["usd_millions"] is None for row in financial_rows):
                        st.warning("Some table units were not clear. Those rows keep the reported amount and leave USD millions blank; don’t compare them directly with Stage 1.")
                    st.dataframe(pd.DataFrame(financial_rows), width="stretch", hide_index=True)
                    st.download_button(
                        "Download extracted rows for review",
                        pd.DataFrame(financial_rows).to_csv(index=False),
                        file_name="northstar_pdf_extracted_rows_review.csv",
                        mime="text/csv",
                    )
                    st.markdown("#### Prepare a Stage 1 CSV")
                    st.caption("This creates the column format Stage 1 expects. Extracted numbers are copied only when the PDF unit is clear; anything not found stays blank for you to complete and verify.")
                    id_a, id_b = st.columns(2)
                    export_ticker = id_a.text_input("Company ticker", key=f"pdf_csv_ticker_{pdf_digest}")
                    export_company = id_b.text_input("Company name", key=f"pdf_csv_company_{pdf_digest}")
                    if st.button("Prepare Stage 1 CSV", key=f"prepare_stage1_csv_{pdf_digest}"):
                        if not export_ticker.strip() or not export_company.strip():
                            st.error("Enter both the ticker and company name before preparing the CSV.")
                        else:
                            all_values = [value for table in extracted_tables for value in table.values]
                            stage1_rows, export_notes = make_stage1_csv_rows(
                                all_values,
                                ticker=export_ticker,
                                company_name=export_company,
                            )
                            st.session_state[f"stage1_pdf_csv_{pdf_digest}"] = stage1_rows
                            st.session_state[f"stage1_pdf_csv_notes_{pdf_digest}"] = export_notes
                    stage1_csv_key = f"stage1_pdf_csv_{pdf_digest}"
                    if stage1_csv_key in st.session_state:
                        stage1_frame = pd.DataFrame(
                            st.session_state[stage1_csv_key], columns=STAGE1_CSV_COLUMNS
                        )
                        notes = st.session_state.get(f"stage1_pdf_csv_notes_{pdf_digest}", [])
                        if notes:
                            st.warning("Review before using this CSV: " + "; ".join(notes))
                        st.dataframe(stage1_frame, width="stretch", hide_index=True)
                        st.download_button(
                            "Download Stage 1 CSV draft",
                            stage1_frame.to_csv(index=False),
                            file_name="northstar_stage1_financials_draft.csv",
                            mime="text/csv",
                            key=f"download_stage1_csv_{pdf_digest}",
                        )
                        st.info("After downloading, review the source pages, fill required blank figures, then upload this draft in the sidebar and run Stage 1. Stage 1 accepts USD figures only.")
                    stage1_result = st.session_state.get("stage1")
                    if stage1_result is None:
                        st.info("To compare extracted rows with Stage 1, upload the same company's financial CSV in the sidebar and run Stage 1 first.")
                    else:
                        st.markdown("#### Compare with Stage 1")
                        compare_ticker = st.selectbox(
                            "Stage 1 company",
                            stage1_result.dataset.tickers,
                            key=f"pdf_compare_ticker_{pdf_digest}",
                        )
                        comparisons = compare_with_stage1(
                            [value for table in extracted_tables for value in table.values],
                            stage1_result.metrics,
                            compare_ticker,
                        )
                        st.caption("Comparison is by exact fiscal year. Values require a clear PDF unit; differences are review cues, not validation.")
                        if comparisons:
                            st.dataframe(pd.DataFrame(comparisons), width="stretch", hide_index=True)
                    with st.expander("Source table snapshots"):
                        for table in extracted_tables:
                            source_label = "recognized text rows" if table.table == 0 else f"table {table.table}"
                            st.markdown(
                                f"**{table.statement.replace('_', ' ').title()} · PDF page {table.page}, {source_label} · {table.reported_unit}**"
                            )
                            st.dataframe(
                                pd.DataFrame(table.rows), width="stretch", hide_index=True
                            )
                else:
                    st.warning("No recognizable financial statement rows were extracted. The PDF may use a different layout; check the page text above.")

            st.markdown("#### Ask about this document")
            pdf_question = st.text_area(
                "Analysis question", value="Summarize the business, performance drivers, risks, and uncertainties described in this document.",
                height=100, key="pdf_question",
            )
            analysis_digest = hashlib.sha256(
                f"{pdf_digest}|{provider}|{model.strip()}|{pdf_question.strip()}".encode("utf-8")
            ).hexdigest()
            if provider == "stub":
                st.info("Stub mode makes no model calls. Text extraction and page review are available now; a live model is needed to write a document analysis.")
            else:
                consent = st.checkbox(
                    f"Send up to 40,000 characters of extracted PDF text to the selected {provider} API for this analysis.",
                    key="pdf_external_consent",
                )
                if st.button("Analyze PDF", type="primary", disabled=not consent or not pdf_question.strip()):
                    excerpt_parts = []
                    remaining = 40_000
                    for page in document.pages:
                        if not page.text or remaining <= 0:
                            continue
                        excerpt = page.text[:remaining]
                        excerpt_parts.append(f"[p. {page.number}]\n{excerpt}")
                        remaining -= len(excerpt)
                    try:
                        client = build_client(provider, model=model.strip() or None)
                        analysis = client.complete(
                            system=(
                                "You are Northstar's company-document analyst. Use only the supplied PDF text. "
                                "Do not invent facts or treat document claims as independently verified. Cite every "
                                "document-based factual statement with its page marker, such as [p. 4]. If a page "
                                "does not provide the requested information, say so. Separate reported facts from "
                                "your interpretation, and do not present this as investment advice."
                            ),
                            user=(
                                f"Question: {pdf_question.strip()}\n\n"
                                f"Document: {pdf_upload.name}\n\n"
                                "Extracted pages (up to 40,000 characters):\n\n"
                                + "\n\n".join(excerpt_parts)
                            ),
                        )
                        st.session_state.pdf_analysis = analysis
                        st.session_state.pdf_analysis_digest = analysis_digest
                        st.session_state.pdf_analysis_provider = provider
                    except Exception as exc:
                        st.error(f"PDF analysis could not complete: {exc}")
            if (
                st.session_state.get("pdf_analysis")
                and st.session_state.get("pdf_analysis_digest") == analysis_digest
                and st.session_state.get("pdf_analysis_provider") == provider
            ):
                st.markdown("#### Document analysis")
                st.info("This narrative is based on extracted PDF text and page citations; its figures are not checked against the Stage 1 CSV.")
                st.markdown(st.session_state.pdf_analysis)
        except PDFExtractionError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"PDF could not be processed: {exc}")
