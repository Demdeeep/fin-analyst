"""LLM providers.

Stage 2 depends on exactly one method, :meth:`LLMClient.complete`. Everything
else -- the agent loop, tool calling, claim verification -- is written against
that seam, so swapping a model never touches the agents.

Two implementations ship: a deterministic :mod:`stub` used by every test and as
the default when no key is configured, and a real OpenAI adapter in
:mod:`openai_client` that is imported lazily so the package works with no
``openai`` dependency installed.

Stage 1's rule still applies: this layer may be non-deterministic, everything
downstream of it is not.
"""

from .base import LLMClient, CompletionError, ProviderConfig
from .stub import ScriptedLLM

__all__ = ["LLMClient", "CompletionError", "ProviderConfig", "ScriptedLLM"]


def build_client(provider: str = "stub", **options):
    """Return a client for ``provider``.

    ``provider="auto"`` prefers OpenAI when a key is configured and falls back to
    the deterministic stub, so a missing key degrades to a warning rather than an
    exception. That matters because the alternative is a pipeline that looks
    broken for entirely the wrong reason.
    """
    from .base import ProviderConfig
    from .stub import ScriptedLLM

    if provider == "stub":
        return ScriptedLLM(**options)

    if provider == "openai":
        from .openai_client import OpenAIClient

        return OpenAIClient(config=ProviderConfig(**options))

    if provider == "auto":
        config = ProviderConfig(**options)
        if config.is_configured:
            from .openai_client import OpenAIClient

            return OpenAIClient(config=config)
        return ScriptedLLM()

    raise ValueError(f"unknown provider {provider!r}; expected stub, openai or auto")


def describe(provider: str = "auto", **options) -> str:
    """One line on which provider a run would actually use, and why.

    Worth having before a live run: ``provider='auto'`` with no key silently
    produces stub output, and a report that looks finished but was written by a
    ``ScriptedLLM`` placeholder is the worst possible outcome of a mistyped flag.
    """
    config = ProviderConfig(**options)
    if provider == "stub":
        return "stub (deterministic; no model call)"
    if provider == "openai":
        if not config.is_configured:
            return f"openai UNAVAILABLE ({config.api_key_env} not set)"
        return f"openai model={config.model}"
    if provider == "auto":
        if config.is_configured:
            return f"auto -> openai model={config.model}"
        return f"auto -> stub (no {config.api_key_env})"
    raise ValueError(f"unknown provider {provider!r}; expected stub, openai or auto")
