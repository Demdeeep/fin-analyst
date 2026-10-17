(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const state = {
    csvFile: null,
    sourceCsv: "",
    analysis: null,
    selectedTickers: new Set(),
    flagFilter: "all",
    pdfFile: null,
    pdf: null,
    pdfDraft: null,
  };
  const dialog = $("#settingsDialog");
  const apiUrlInput = $("#apiUrl");
  const toast = $("#toast");
  let toastTimer;

  function apiBase() {
    const saved = localStorage.getItem("northstarApiUrl");
    if (saved) return saved.replace(/\/$/, "");
    if (["localhost", "127.0.0.1"].includes(location.hostname)) return "http://127.0.0.1:8000";
    return "";
  }

  function apiToken() {
    return localStorage.getItem("northstarApiToken") || "";
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[char]);
  }

  function showToast(message, kind = "ok") {
    toast.textContent = message;
    toast.className = `toast show${kind === "error" ? " error" : ""}`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toast.className = "toast"; }, 4300);
  }

  function setBusy(button, busy, label) {
    if (!button.dataset.originalLabel) button.dataset.originalLabel = button.innerHTML;
    button.disabled = busy;
    button.innerHTML = busy ? `<span class="loading-spinner"></span> ${escapeHtml(label)}` : button.dataset.originalLabel;
  }

  async function request(path, options = {}) {
    const base = apiBase();
    if (!base) {
      openSettings();
      throw new Error("Connect the Python API URL in Connection settings first.");
    }
    let response;
    try {
      const headers = new Headers(options.headers || {});
      if (apiToken()) headers.set("X-Northstar-Token", apiToken());
      response = await fetch(`${base}${path}`, { ...options, headers });
    } catch (error) {
      throw new Error("Could not reach the Python API. Check that it is deployed, awake, and allows this Netlify site in CORS settings.");
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = data.detail || data.message || `Request failed (${response.status}).`;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }

  function setApiStatus(mode, text) {
    const status = $("#apiStatus");
    status.className = `api-status${mode === "connected" ? " connected" : mode === "checking" ? " checking" : ""}`;
    $("#apiStatusText").textContent = text;
    $("#connectionHint").hidden = mode === "connected";
  }

  async function checkConnection(url = apiBase(), token = apiToken()) {
    const status = $("#dialogStatus");
    if (!url) {
      setApiStatus("off", "API not connected");
      status.textContent = "Enter your API service URL first.";
      status.className = "dialog-status error";
      return false;
    }
    setApiStatus("checking", "Checking API…");
    status.textContent = "Checking the Python service…";
    status.className = "dialog-status";
    try {
      const headers = token ? { "X-Northstar-Token": token } : {};
      const response = await fetch(`${url.replace(/\/$/, "")}/api/health`, { headers });
      const data = await response.json();
      if (!response.ok || data.status !== "ok") throw new Error("Health check did not return OK.");
      setApiStatus("connected", "Python API connected");
      status.textContent = `Connected · ${data.service} · API ${data.version}`;
      status.className = "dialog-status ok";
      return true;
    } catch (error) {
      setApiStatus("off", "API not connected");
      status.textContent = "Could not connect. Check the URL and CORS configuration.";
      status.className = "dialog-status error";
      return false;
    }
  }

  function openSettings() {
    apiUrlInput.value = apiBase();
    $("#apiToken").value = apiToken();
    $("#dialogStatus").textContent = apiBase() ? "Test the connection before running analyses." : "Your connection is stored in this browser only.";
    $("#dialogStatus").className = "dialog-status";
    dialog.showModal();
  }

  function switchPage(page) {
    const dashboard = page === "dashboard";
    $("#dashboardPage").classList.toggle("active-page", dashboard);
    $("#documentsPage").classList.toggle("active-page", !dashboard);
    $$(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.page === page));
    $("#pageCrumb").textContent = dashboard ? "OVERVIEW" : "DOCUMENT LAB";
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function setCsvFile(file) {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".csv")) return showToast("Choose a .csv file.", "error");
    state.csvFile = file;
    state.sourceCsv = "";
    $("#selectedFile").hidden = false;
    $("#selectedFile").textContent = `✦  ${file.name} · ${(file.size / 1024).toFixed(1)} KB`;
    $("#analyzeCsv").disabled = false;
    $("#resultsSection").hidden = true;
  }

  async function analyzeCsv() {
    if (!state.csvFile) return showToast("Choose a financial CSV first.", "error");
    const button = $("#analyzeCsv");
    setBusy(button, true, "Calculating fundamentals…");
    const form = new FormData();
    form.append("file", state.csvFile);
    try {
      const result = await request("/api/analyze/csv", { method: "POST", body: form });
      state.analysis = result;
      state.sourceCsv = await state.csvFile.text();
      state.selectedTickers = new Set(result.tickers);
      $("#resultsSection").hidden = false;
      $("#resultsTitle").textContent = result.company_count === 1 ? `${result.tickers[0]} financial snapshot` : "Financial snapshot";
      $("#datasetTag").textContent = `${result.filename} · ${result.record_count} PERIODS`;
      renderAnalysis();
      $("#resultsSection").scrollIntoView({ behavior: "smooth", block: "start" });
      showToast(`Stage 1 complete · ${result.record_count} company-year records calculated.`);
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      setBusy(button, false);
    }
  }

  function pct(value, decimals = 1) {
    const number = Number(value);
    return Number.isFinite(number) ? `${(number * 100).toFixed(decimals)}%` : "—";
  }

  function number(value, decimals = 1) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed.toLocaleString(undefined, { maximumFractionDigits: decimals }) : "—";
  }

  function metricsForTicker(ticker) {
    return (state.analysis?.metrics || []).filter((row) => row.ticker === ticker).sort((a, b) => Number(a.fiscal_year) - Number(b.fiscal_year));
  }

  function latestRows() {
    const latest = new Map();
    for (const row of state.analysis?.metrics || []) {
      if (!latest.has(row.ticker) || Number(latest.get(row.ticker).fiscal_year) < Number(row.fiscal_year)) latest.set(row.ticker, row);
    }
    return [...latest.values()];
  }

  function renderKpis() {
    const latest = latestRows();
    const focus = latest.find((row) => row.ticker === state.analysis.tickers[0]) || latest[0] || {};
    const cards = [
      ["LATEST REVENUE", number(focus.revenue, 0), `${focus.currency || "USD"} · ${focus.ticker || "Company"} · FY${focus.fiscal_year || "—"}`, "mint"],
      ["NET MARGIN", pct(focus.net_margin), "Latest reported year", "gold"],
      ["FREE CASH FLOW", number(focus.free_cash_flow, 0), `${focus.currency || "USD"} · Latest year`, ""],
      ["REVIEW FLAGS", String(state.analysis.flags.length), `${state.analysis.flags.filter((flag) => flag.severity === "critical").length} critical · ${state.analysis.flags.filter((flag) => flag.severity === "warning").length} watch`, "orange"],
    ];
    $("#kpiGrid").innerHTML = cards.map(([label, value, foot, tone]) => `<article class="kpi-card ${tone}"><div class="kpi-label">${escapeHtml(label)}</div><div class="kpi-value">${escapeHtml(value)}</div><div class="kpi-foot">${escapeHtml(foot)}</div></article>`).join("");
  }

  function renderChart() {
    const ticker = $("#chartTicker").value || state.analysis.tickers[0];
    const rows = metricsForTicker(ticker).filter((row) => Number.isFinite(Number(row.revenue)));
    const wrap = $("#chartWrap");
    if (!rows.length) {
      wrap.innerHTML = '<div class="empty-state">No revenue history available for this company.</div>';
      return;
    }
    const values = rows.map((row) => Number(row.revenue));
    const min = Math.min(...values), max = Math.max(...values);
    const span = max - min || Math.max(Math.abs(max) * .12, 1);
    const left = 42, right = 625, top = 12, bottom = 132;
    const points = rows.map((row, index) => ({
      row,
      x: rows.length === 1 ? (left + right) / 2 : left + index * (right - left) / (rows.length - 1),
      y: bottom - ((Number(row.revenue) - min) / span) * (bottom - top),
    }));
    const line = points.map((point, index) => `${index ? "L" : "M"}${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ");
    const area = `${line} L${right},${bottom} L${left},${bottom} Z`;
    const grid = [0, 1, 2].map((index) => {
      const y = top + index * ((bottom - top) / 2);
      const amount = max - index * (span / 2);
      return `<line class="chart-gridline" x1="${left}" x2="${right}" y1="${y}" y2="${y}"/><text class="chart-axis" x="0" y="${y + 3}">${escapeHtml(shortMoney(amount))}</text>`;
    }).join("");
    const dots = points.map((point) => `<circle class="chart-point" cx="${point.x}" cy="${point.y}" r="4"><title>FY${escapeHtml(point.row.fiscal_year)} revenue ${escapeHtml(number(point.row.revenue, 0))}</title></circle><text class="chart-axis" text-anchor="middle" x="${point.x}" y="150">${escapeHtml(point.row.fiscal_year)}</text>`).join("");
    wrap.innerHTML = `<svg viewBox="0 0 640 160" role="img" aria-label="Revenue trend for ${escapeHtml(ticker)}">${grid}<defs><linearGradient id="chartGradient" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#9bc9ae" stop-opacity=".22"/><stop offset="1" stop-color="#9bc9ae" stop-opacity="0"/></linearGradient></defs><path class="chart-area" d="${area}"/><path class="chart-line" d="${line}"/>${dots}</svg>`;
    $("#chartRange").textContent = `${rows[0].currency || "USD"} · FY${rows[0].fiscal_year}—${rows.at(-1).fiscal_year}`;
  }

  function shortMoney(value) {
    const abs = Math.abs(value);
    if (abs >= 1e9) return `${(value / 1e9).toFixed(1)}B`;
    if (abs >= 1e6) return `${(value / 1e6).toFixed(1)}M`;
    if (abs >= 1e3) return `${(value / 1e3).toFixed(1)}K`;
    return number(value, 0);
  }

  function renderFlags() {
    const flags = state.analysis.flags.filter((flag) => state.flagFilter === "all" || (state.flagFilter === "critical" ? flag.severity === "critical" : flag.severity === "warning"));
    $("#signalList").innerHTML = flags.length ? flags.map((flag) => `<div class="signal-row"><span class="signal-marker ${escapeHtml(flag.severity)}"></span><div class="signal-copy"><b>${escapeHtml(flag.ticker)}${flag.fiscal_year ? ` · FY${escapeHtml(flag.fiscal_year)}` : ""}</b> — ${escapeHtml(flag.message)}</div><span class="signal-meta">${escapeHtml(String(flag.severity).toUpperCase())}</span></div>`).join("") : '<div class="empty-state">No signals in this view. That means no flags of this type were raised.</div>';
    $("#signalCount").textContent = String(state.analysis.flags.length);
    $("#allSignalCount").textContent = String(state.analysis.flags.length);
    $("#criticalSignalCount").textContent = String(state.analysis.flags.filter((flag) => flag.severity === "critical").length);
    $("#warningSignalCount").textContent = String(state.analysis.flags.filter((flag) => flag.severity === "warning").length);
  }

  const columnLabels = {
    ticker: "Company", fiscal_year: "Year", revenue: "Revenue", revenue_growth: "Revenue growth", gross_margin: "Gross margin", operating_margin: "Operating margin", net_margin: "Net margin", debt_to_assets: "Debt / assets", current_ratio: "Current ratio", free_cash_flow: "Free cash flow", return_on_equity: "Return on equity", interest_coverage: "Interest coverage",
  };
  const preferredColumns = ["ticker", "fiscal_year", "revenue", "revenue_growth", "gross_margin", "operating_margin", "net_margin", "debt_to_assets", "current_ratio", "free_cash_flow", "return_on_equity", "interest_coverage"];

  function formatMetric(key, value) {
    if (value === null || value === undefined || value === "") return "—";
    if (["revenue_growth", "gross_margin", "operating_margin", "net_margin", "debt_to_assets", "return_on_equity", "ebitda_margin", "free_cash_flow_margin"].includes(key)) return pct(value);
    if (["revenue", "free_cash_flow"].includes(key)) return number(value, 0);
    if (typeof value === "number") return number(value, 2);
    return String(value);
  }

  function renderMetricsTable() {
    const query = $("#metricSearch").value.trim().toLowerCase();
    const records = state.analysis.metrics.filter((row) => !query || `${row.ticker} ${row.company_name} ${row.fiscal_year}`.toLowerCase().includes(query));
    const keys = preferredColumns.filter((key) => state.analysis.metrics.some((row) => key in row));
    const headings = keys.map((key) => `<th>${escapeHtml(columnLabels[key] || key.replaceAll("_", " "))}</th>`).join("");
    const body = records.map((row) => `<tr>${keys.map((key) => `<td>${escapeHtml(formatMetric(key, row[key]))}</td>`).join("")}</tr>`).join("");
    $("#metricsTable").innerHTML = `<table class="data-table"><thead><tr>${headings}</tr></thead><tbody>${body || `<tr><td colspan="${keys.length}">No matching company records.</td></tr>`}</tbody></table>`;
    $("#tableCount").textContent = `${records.length} OF ${state.analysis.metrics.length} RECORDS`;
  }

  function renderTickerPicker() {
    const holder = $("#analystTickers");
    holder.innerHTML = state.analysis.tickers.map((ticker) => `<button type="button" class="ticker-chip ${state.selectedTickers.has(ticker) ? "selected" : ""}" data-ticker="${escapeHtml(ticker)}" aria-pressed="${state.selectedTickers.has(ticker)}">${escapeHtml(ticker)}</button>`).join("");
    $("#chartTicker").innerHTML = state.analysis.tickers.map((ticker) => `<option value="${escapeHtml(ticker)}">${escapeHtml(ticker)}</option>`).join("");
  }

  function renderAnalysis() {
    renderKpis();
    renderTickerPicker();
    renderChart();
    renderFlags();
    renderMetricsTable();
    if (state.analysis.warnings?.length) showToast(state.analysis.warnings.join(" · "), "error");
  }

  function markdownText(value) {
    return escapeHtml(value).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/\n/g, "<br>");
  }

  function pickedTickers() {
    return [...state.selectedTickers];
  }

  async function runStage2() {
    if (!state.analysis || !state.csvFile) return showToast("Run Stage 1 before requesting an analyst review.", "error");
    if (!pickedTickers().length) return showToast("Select at least one company ticker.", "error");
    const provider = $("#providerSelect").value;
    if (provider !== "stub" && !confirm(`This sends selected company metrics to ${provider}. Continue?`)) return;
    if ($("#filingQuery").value.trim() && !confirm("Filing search sends the selected ticker and question to SEC EDGAR. Continue?")) return;
    const button = $("#runAnalyst");
    setBusy(button, true, "Reviewing figures…");
    $("#analystResults").innerHTML = '<div class="loading-inline"><span class="loading-spinner"></span> Running the analyst and mechanical checks…</div>';
    const form = new FormData();
    form.append("file", state.csvFile);
    form.append("tickers_json", JSON.stringify(pickedTickers()));
    form.append("provider", provider);
    form.append("model", $("#modelOverride").value.trim());
    form.append("filing_query", $("#filingQuery").value.trim());
    form.append("sec_user_agent", $("#secContact").value.trim());
    try {
      const data = await request("/api/analyze/agents", { method: "POST", body: form });
      $("#analystResults").innerHTML = data.results.map((item) => `<article class="result-card"><h4>${escapeHtml(item.ticker)} · Analyst review</h4><div class="result-badges"><span class="badge ${escapeHtml(String(item.verdict).toLowerCase())}">${escapeHtml(item.verdict)}</span><span class="badge">${item.verified ? "FIGURES VERIFIED" : `${item.issues.length} FIGURE ISSUE(S)`}</span><span class="badge">FLAGS ${item.flags_addressed}/${item.flags_considered}</span></div><div class="result-narrative">${markdownText(item.narrative || "No narrative returned.")}</div>${item.unaddressed_flags?.length ? `<details><summary>Flags needing attention (${item.unaddressed_flags.length})</summary><p>${item.unaddressed_flags.map(escapeHtml).join("<br>")}</p></details>` : ""}${item.issues?.length ? `<details><summary>Figure checks (${item.issues.length})</summary><p>${item.issues.map(escapeHtml).join("<br>")}</p></details>` : ""}${item.critic_notes ? `<details><summary>Reviewer notes</summary><p>${escapeHtml(item.critic_notes)}</p></details>` : ""}${item.filing_sources?.length ? `<details><summary>Filing sources (${item.filing_sources.length})</summary><p>${item.filing_sources.map((source) => `<a href="${escapeHtml(source.source_url)}" target="_blank" rel="noopener">${escapeHtml(source.source_id)} · ${escapeHtml(source.citation)}</a>`).join("<br>")}</p></details>` : ""}</article>`).join("") || '<div class="empty-state">No analyst results.</div>';
      showToast(`Analyst review complete · ${data.results.length} company result(s).`);
    } catch (error) {
      $("#analystResults").innerHTML = "";
      showToast(error.message, "error");
    } finally {
      setBusy(button, false);
    }
  }

  async function runStage4() {
    if (!state.analysis || !state.csvFile) return showToast("Run Stage 1 before comparing peers.", "error");
    if (!pickedTickers().length) return showToast("Select at least one company ticker.", "error");
    const provider = $("#providerSelect").value;
    if (provider !== "stub" && !confirm(`This sends selected company metrics to ${provider}. Continue?`)) return;
    const button = $("#runPeers");
    setBusy(button, true, "Comparing the group…");
    $("#peerResults").innerHTML = '<div class="loading-inline"><span class="loading-spinner"></span> Computing peer medians and checking the narrative…</div>';
    const form = new FormData();
    form.append("file", state.csvFile);
    form.append("tickers_json", JSON.stringify(pickedTickers()));
    form.append("provider", provider);
    form.append("model", $("#modelOverride").value.trim());
    form.append("scope", $("#peerScope").value);
    try {
      const data = await request("/api/analyze/peers", { method: "POST", body: form });
      $("#peerResults").innerHTML = data.results.map((item) => `<article class="result-card"><h4>${escapeHtml(item.ticker)} · ${escapeHtml(item.group || "Peer comparison")}</h4><div class="result-badges"><span class="badge">${item.compared} METRICS COMPARED</span><span class="badge">${item.declined.length} DECLINED</span></div><div class="result-narrative">${markdownText(item.narrative || "No narrative returned.")}</div>${item.rows?.length ? `<div class="peer-table-card"><table class="data-table"><thead><tr><th>METRIC</th><th>COMPANY</th><th>PEER MEDIAN</th><th>GAP</th><th>RANK</th></tr></thead><tbody>${item.rows.map((row) => `<tr><td>${escapeHtml(row.metric.replaceAll("_", " "))}</td><td>${escapeHtml(formatMetric(row.metric, row.value))}</td><td>${escapeHtml(formatMetric(row.metric, row.peer_median))}</td><td>${escapeHtml(formatMetric(row.metric, row.gap))}</td><td>${escapeHtml(row.rank ?? "—")}</td></tr>`).join("")}</tbody></table></div>` : ""}${item.declined?.length ? `<details><summary>Why metrics were declined (${item.declined.length})</summary><p>${item.declined.map((row) => `${escapeHtml(row.metric)}: ${escapeHtml(row.reason)}`).join("<br>")}</p></details>` : ""}${item.issues?.length ? `<details><summary>Verification notes</summary><p>${item.issues.map(escapeHtml).join("<br>")}</p></details>` : ""}</article>`).join("") || '<div class="empty-state">No peer results.</div>';
      showToast(`Peer comparison complete · ${data.results.length} company result(s).`);
    } catch (error) {
      $("#peerResults").innerHTML = "";
      showToast(error.message, "error");
    } finally {
      setBusy(button, false);
    }
  }

  function downloadFile(content, filename, mime) {
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  function setPdfFile(file) {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".pdf")) return showToast("Choose a PDF file.", "error");
    state.pdfFile = file;
    state.pdf = null;
    state.pdfDraft = null;
    $("#selectedPdf").hidden = false;
    $("#selectedPdf").textContent = `✦  ${file.name} · ${(file.size / 1024 / 1024).toFixed(2)} MB`;
    $("#extractPdf").disabled = false;
    $("#pdfResults").hidden = true;
  }

  async function extractPdf() {
    if (!state.pdfFile) return;
    const button = $("#extractPdf");
    setBusy(button, true, "Reading pages and tables…");
    const form = new FormData();
    form.append("file", state.pdfFile);
    try {
      state.pdf = await request("/api/extract/pdf", { method: "POST", body: form });
      state.pdfDraft = null;
      renderPdf();
      $("#pdfResults").hidden = false;
      showToast(`Document ready · ${state.pdf.page_count} pages · ${state.pdf.tables.reduce((count, table) => count + table.values.length, 0)} recognized values.`);
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      setBusy(button, false);
    }
  }

  function renderPdf() {
    const data = state.pdf;
    const values = data.tables.flatMap((table) => table.values);
    const unnormalized = values.filter((value) => value.usd_millions === null).length;
    const pages = data.pages.filter((page) => page.text).map((page) => `<details class="pdf-page"><summary>Page ${page.number} · ${page.text.length.toLocaleString()} characters</summary><pre>${escapeHtml(page.text.slice(0, 5000))}${page.text.length > 5000 ? "\n\n[Page excerpt limited to 5,000 characters]" : ""}</pre></details>`).join("");
    const valueRows = values.map((value) => `<div class="pdf-value-row"><span>${escapeHtml(value.label_in_pdf)}</span><span>${escapeHtml(value.fiscal_year)}</span><span>${escapeHtml(number(value.reported_value, 2))} ${escapeHtml(value.reported_unit)}</span><span>p. ${escapeHtml(value.page)}</span></div>`).join("");
    const tableSnapshots = data.tables.map((table) => `<details class="pdf-page"><summary>${escapeHtml(table.statement.replaceAll("_", " "))} · page ${table.page} · ${escapeHtml(table.reported_unit)}</summary><div class="table-scroll"><table class="data-table"><tbody>${table.rows.map((row) => `<tr>${row.map((cell) => `<td>${escapeHtml(cell)}</td>`).join("")}</tr>`).join("")}</tbody></table></div></details>`).join("");
    $("#pdfResults").innerHTML = `<div class="pdf-overview"><div class="pdf-stat"><span>DOCUMENT PAGES</span><b>${data.page_count}</b></div><div class="pdf-stat"><span>TEXT EXTRACTED</span><b>${number(data.text_characters, 0)}</b></div><div class="pdf-stat"><span>FINANCIAL VALUES</span><b>${values.length}</b></div></div>${data.table_error ? `<div class="pdf-unit-warning">${escapeHtml(data.table_error)}</div>` : ""}${values.length ? `<section class="pdf-section"><h3>Recognized financial statement values</h3>${unnormalized ? `<div class="pdf-unit-warning">${unnormalized} values have unclear units. Keep the reported value and check the source table before comparing.</div>` : ""}<div class="pdf-values"><div class="pdf-value-row header"><span>PDF LABEL</span><span>YEAR</span><span>REPORTED VALUE</span><span>PAGE</span></div>${valueRows}</div><div class="pdf-actions"><button type="button" class="button button-quiet button-small" id="downloadExtracted">↓ Download review rows</button><button type="button" class="button button-outline button-small" id="toggleDraft">Prepare Stage 1 CSV <span>→</span></button></div><div id="draftArea"></div></section>` : `<section class="pdf-section"><h3>No recognizable financial statement rows yet</h3><p class="result-narrative">Northstar did extract the pages below. This report layout may not match the local table patterns.</p></section>`}<section class="pdf-section"><h3>Page text with source references</h3><div class="pdf-page-list">${pages}</div></section>${tableSnapshots ? `<section class="pdf-section"><h3>Original table snapshots</h3><div class="pdf-page-list">${tableSnapshots}</div></section>` : ""}<section class="pdf-section"><h3>Ask about this report</h3><div class="pdf-form-grid"><label class="field-label">PROVIDER<select id="pdfProvider"><option value="anthropic">Anthropic</option><option value="openai">OpenAI</option></select></label><label class="field-label">MODEL OVERRIDE (OPTIONAL)<input id="pdfModel" type="text" placeholder="Use provider default"></label></div><label class="field-label" style="margin-top:10px">YOUR QUESTION<input id="pdfQuestion" type="text" value="Summarize the business, performance drivers, risks, and uncertainties described in this document."></label><label class="consent-row"><input type="checkbox" id="pdfConsent"><span>I understand that up to 40,000 characters of extracted text will be sent to the selected model provider. The narrative is not checked against Stage 1 figures.</span></label><button class="button button-primary" id="analyzePdfText">Analyze document <span>→</span></button><div id="pdfNarrative" class="analysis-results"></div></section>`;
    $("#downloadExtracted")?.addEventListener("click", () => downloadFile(csvFromObjects(values), "northstar_pdf_extracted_rows_review.csv", "text/csv"));
    $("#toggleDraft")?.addEventListener("click", showDraftForm);
    $("#analyzePdfText")?.addEventListener("click", analyzePdfText);
  }

  function csvFromObjects(rows) {
    if (!rows.length) return "";
    const keys = Object.keys(rows[0]);
    const quote = (value) => `"${String(value ?? "").replaceAll('"', '""')}"`;
    return `${keys.map(quote).join(",")}\n${rows.map((row) => keys.map((key) => quote(row[key])).join(",")).join("\n")}\n`;
  }

  function showDraftForm() {
    $("#draftArea").innerHTML = `<div class="pdf-form-grid" style="margin-top:12px"><label class="field-label">TICKER<input id="draftTicker" type="text" placeholder="e.g. AAPL"></label><label class="field-label">COMPANY NAME<input id="draftCompany" type="text" placeholder="e.g. Apple Inc."></label></div><div class="pdf-actions"><button type="button" class="button button-primary button-small" id="createDraft">Build draft CSV</button></div><div id="draftResult"></div>`;
    $("#createDraft").addEventListener("click", createDraft);
  }

  async function createDraft() {
    const form = new FormData();
    form.append("file", state.pdfFile);
    form.append("ticker", $("#draftTicker").value.trim());
    form.append("company_name", $("#draftCompany").value.trim());
    try {
      const data = await request("/api/extract/pdf/stage1-csv", { method: "POST", body: form });
      state.pdfDraft = data;
      const html = `<div class="pdf-unit-warning">${data.notes.length ? data.notes.map(escapeHtml).join("<br>") : "Draft prepared. Verify every number and fill blank required values before Stage 1."}</div><div class="table-scroll"><table class="data-table"><thead><tr>${data.columns.map((column) => `<th>${escapeHtml(column)}</th>`).join("")}</tr></thead><tbody>${data.rows.map((row) => `<tr>${data.columns.map((column) => `<td>${escapeHtml(row[column])}</td>`).join("")}</tr>`).join("")}</tbody></table></div><div class="pdf-actions"><button type="button" class="button button-quiet button-small" id="downloadDraft">↓ Download Stage 1 draft</button></div>`;
      $("#draftResult").innerHTML = html;
      $("#downloadDraft").addEventListener("click", () => downloadFile(csvFromObjects(data.rows), "northstar_stage1_financials_draft.csv", "text/csv"));
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  async function analyzePdfText() {
    const consent = $("#pdfConsent").checked;
    if (!consent) return showToast("Select the consent box before sending document text to a provider.", "error");
    const button = $("#analyzePdfText");
    setBusy(button, true, "Writing page-cited analysis…");
    const form = new FormData();
    form.append("file", state.pdfFile);
    form.append("question", $("#pdfQuestion").value.trim());
    form.append("provider", $("#pdfProvider").value);
    form.append("model", $("#pdfModel").value.trim());
    form.append("consent", "true");
    try {
      const data = await request("/api/analyze/pdf", { method: "POST", body: form });
      $("#pdfNarrative").innerHTML = `<article class="result-card"><div class="result-badges"><span class="badge">${escapeHtml(data.provider.toUpperCase())}</span><span class="badge">${data.pages_used} PAGES</span><span class="badge">${number(data.characters_sent, 0)} CHARS SENT</span></div><div class="result-narrative">${markdownText(data.narrative)}</div></article>`;
      showToast("Document analysis is ready. Check the cited pages in the source above.");
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      setBusy(button, false);
    }
  }

  function bindDropzone(zone, input, onFile) {
    zone.addEventListener("dragover", (event) => { event.preventDefault(); zone.classList.add("dragging"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("dragging"));
    zone.addEventListener("drop", (event) => { event.preventDefault(); zone.classList.remove("dragging"); onFile(event.dataTransfer.files[0]); });
    input.addEventListener("change", () => { onFile(input.files[0]); input.value = ""; });
  }

  $("#openSettings").addEventListener("click", openSettings);
  $("#topSettings").addEventListener("click", openSettings);
  $("#hintSettings").addEventListener("click", openSettings);
  $("#testConnection").addEventListener("click", () => checkConnection(apiUrlInput.value.trim().replace(/\/$/, ""), $("#apiToken").value));
  $("#saveConnection").addEventListener("click", async () => {
    const url = apiUrlInput.value.trim().replace(/\/$/, "");
    if (!/^https?:\/\//i.test(url)) {
      $("#dialogStatus").textContent = "Enter a complete URL beginning with https:// (or http:// for local testing).";
      $("#dialogStatus").className = "dialog-status error";
      return;
    }
    localStorage.setItem("northstarApiUrl", url);
    if ($("#apiToken").value) localStorage.setItem("northstarApiToken", $("#apiToken").value);
    else localStorage.removeItem("northstarApiToken");
    if (await checkConnection(url, apiToken())) dialog.close();
  });
  $$(".nav-item").forEach((button) => button.addEventListener("click", () => switchPage(button.dataset.page)));
  $("#browseCsv").addEventListener("click", () => $("#csvInput").click());
  $("#browsePdf").addEventListener("click", () => $("#pdfInput").click());
  bindDropzone($("#csvDropzone"), $("#csvInput"), setCsvFile);
  bindDropzone($("#pdfDropzone"), $("#pdfInput"), setPdfFile);
  $("#analyzeCsv").addEventListener("click", analyzeCsv);
  $("#extractPdf").addEventListener("click", extractPdf);
  $("#sampleCsv").addEventListener("click", async () => {
    try {
      const response = await fetch("/sample_financials.csv");
      if (!response.ok) throw new Error();
      const content = await response.text();
      state.sourceCsv = content;
      setCsvFile(new File([content], "northstar_sample_financials.csv", { type: "text/csv" }));
      showToast("Sample dataset loaded · synthetic companies and figures.");
    } catch (error) { showToast("Could not load the included sample data.", "error"); }
  });
  $("#chartTicker").addEventListener("change", renderChart);
  $("#metricSearch").addEventListener("input", renderMetricsTable);
  $(".signal-filters").addEventListener("click", (event) => {
    const button = event.target.closest("[data-flag-filter]");
    if (!button) return;
    state.flagFilter = button.dataset.flagFilter;
    $$(".filter-chip").forEach((chip) => chip.classList.toggle("selected", chip === button));
    renderFlags();
  });
  $("#analystTickers").addEventListener("click", (event) => {
    const button = event.target.closest("[data-ticker]");
    if (!button) return;
    if (state.selectedTickers.has(button.dataset.ticker)) state.selectedTickers.delete(button.dataset.ticker);
    else state.selectedTickers.add(button.dataset.ticker);
    renderTickerPicker();
  });
  $("#providerSelect").addEventListener("change", () => {
    const live = $("#providerSelect").value !== "stub";
    $("#providerPrivacy").classList.toggle("live", live);
    $("#providerPrivacy").textContent = live
      ? `Running Stage 2 or Stage 4 sends the selected company metrics to ${$("#providerSelect").value}. You will be asked before each run.`
      : "Stub makes no model calls. Choose a live provider only if you want to send company metrics to its API.";
    $("#peerProviderCaption").textContent = `Uses the selected ${$("#providerSelect").value} provider.`;
  });
  $("#runAnalyst").addEventListener("click", runStage2);
  $("#runPeers").addEventListener("click", runStage4);
  $("#downloadMetrics").addEventListener("click", () => state.analysis && downloadFile(state.analysis.metrics_csv, "northstar_company_metrics.csv", "text/csv"));
  $("#downloadSource").addEventListener("click", () => state.sourceCsv && downloadFile(state.sourceCsv, state.csvFile?.name || "source_financials.csv", "text/csv"));

  const savedUrl = apiBase();
  if (savedUrl) checkConnection(savedUrl);
  else setApiStatus("off", "API not connected");
})();
