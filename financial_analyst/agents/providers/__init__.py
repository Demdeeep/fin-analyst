"""LLM providers.

Stage 2 depends on exactly one method, :meth:`LLMClient.complete`. Everything
else -- the agent loop, tool calling, claim verification -- is written against
that seam, so swapping a model never touches the agents.

Three implementations ship: a deterministic :mod:`stub` used by every test and
as the default, plus lazy OpenAI and Anthropic adapters that need no provider SDK
until a live call is made.

Stage 1's rule still applies: this layer may be non-deterministic, everything
downstream of it is not.
"""

from dataclasses import replace
from typing import Optional

from .base import LLMClient, CompletionError, ProviderConfig
from .anthropic_client import AnthropicClient
from .stub import ScriptedLLM

__all__ = [
    "LLMClient", "CompletionError", "ProviderConfig", "ScriptedLLM", "AnthropicClient"
]


def build_client(provider: str = "stub", model: Optional[str] = None, **options):
    """Return a client for ``provider``.

    ``provider="auto"`` prefers OpenAI when a key is configured and falls back to
    the deterministic stub, so a missing key degrades to a warning rather than an
    exception. That matters because the alternative is a pipeline that looks
    broken for entirely the wrong reason.

    ``model`` is an explicit keyword rather than part of ``**options`` because the
    stub takes no model argument: passing ``--model`` with ``--provider stub``
    should be a no-op, not a ``TypeError``.
    """
    from .base import ProviderConfig
    from .stub import ScriptedLLM

    if provider == "stub":
        return ScriptedLLM(**options)

    if provider == "openai":
        from .openai_client import OpenAIClient

        config = ProviderConfig(**options)
        if model is not None:
            config = replace(config, model=model)
        return OpenAIClient(config=config)

    if provider == "anthropic":
        from .anthropic_client import AnthropicClient, DEFAULT_MODEL

        config = ProviderConfig(
            api_key_env="ANTHROPIC_API_KEY",
            model=model or options.pop("model", DEFAULT_MODEL),
            **options,
        )
        return AnthropicClient(config=config)

    if provider == "auto":
        config = ProviderConfig(**options)
        if model is not None:
            config = replace(config, model=model)
        if config.is_configured:
            from .openai_client import OpenAIClient

            return OpenAIClient(config=config)
        return ScriptedLLM()

    raise ValueError(
        f"unknown provider {provider!r}; expected stub, openai, anthropic or auto"
    )


def describe(provider: str = "auto", model: Optional[str] = None, **options) -> str:
    """One line on which provider a run would actually use, and why.

    Worth having before a live run: ``provider='auto'`` with no key silently
    produces stub output, and a report that looks finished but was written by a
    ``ScriptedLLM`` placeholder is the worst possible outcome of a mistyped flag.
    """
    if provider == "stub":
        return "stub (deterministic; no model call)"
    if provider == "anthropic":
        from .anthropic_client import DEFAULT_MODEL

        options["api_key_env"] = "ANTHROPIC_API_KEY"
        options.setdefault("model", model or DEFAULT_MODEL)
        config = ProviderConfig(**options)
        if not config.is_configured:
            return f"anthropic UNAVAILABLE ({config.api_key_env} not set)"
        return f"anthropic model={config.model}"
    config = ProviderConfig(**options)
    if model is not None:
        config = replace(config, model=model)
    if provider == "openai":
        if not config.is_configured:
            return f"openai UNAVAILABLE ({config.api_key_env} not set)"
        return f"openai model={config.model}"
    if provider == "auto":
        if config.is_configured:
            return f"auto -> openai model={config.model}"
        return f"auto -> stub (no {config.api_key_env})"
    raise ValueError(
        f"unknown provider {provider!r}; expected stub, openai, anthropic or auto"
    )
