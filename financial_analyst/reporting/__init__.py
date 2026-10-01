"""Template-written reporting.

Stage 1 deliberately contains **no language model**. The narrative is assembled
by :mod:`financial_analyst.reporting.summary` from the templates held here as
data, so that Stage 2 can swap in model-written prose without the pipeline,
the metrics, or the flag set changing shape.

The templates also hold the line the project refuses to cross: they state
values, deltas and flags, and never assert a conclusion. Judgement belongs to
the agents, and a Stage 1 that pre-judges would quietly bias everything that
reads its output.
"""
