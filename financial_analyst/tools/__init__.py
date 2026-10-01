"""Deterministic tools available to (and eventually callable by) agents.

Layering rule: :mod:`calculations` is a pure leaf module -- it imports nothing
from the rest of the package and nothing from pandas. Everything that touches
files or dataframes lives in a sibling module.
"""
