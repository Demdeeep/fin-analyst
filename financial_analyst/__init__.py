"""Multi-agent financial analyst.

Stage 1 (implemented): deterministic analysis of a static CSV of company
financials -- pure ratio math in :mod:`financial_analyst.tools.calculations`,
dataframe plumbing in :mod:`financial_analyst.tools.metrics`, rule-based
anomaly detection in :mod:`financial_analyst.tools.anomalies`, and a
template-written narrative in :mod:`financial_analyst.reporting`.

Stage 2+ (reserved): LLM agents under :mod:`financial_analyst.agents`.
"""

__version__ = "0.1.0"
