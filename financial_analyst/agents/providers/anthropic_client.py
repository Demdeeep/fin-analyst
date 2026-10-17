"""Anthropic messages adapter with the same call surface as OpenAIClient."""

from __future__ import annotations

from typing import Optional

from .base import CompletionError, ProviderConfig

DEFAULT_MODEL = "claude-haiku-4-5-20251001"


class AnthropicClient:
    """Lazy Anthropic adapter; importing this module needs no SDK or key."""

    def __init__(self, config: Optional[ProviderConfig] = None):
        self.config = config or ProviderConfig(
            api_key_env="ANTHROPIC_API_KEY", model=DEFAULT_MODEL
        )
        self._client = None
        self.truncated: list[str] = []
        self.last_finish_reason: Optional[str] = None
        self.last_usage: Optional[dict[str, int]] = None

    def _sdk(self):
        if self._client is None:
            # Check credentials before importing the optional SDK.
            key = self.config.api_key
            if key is None:
                raise CompletionError(
                    f"{self.config.api_key_env} is not set, so no live call can be made"
                )
            try:
                from anthropic import Anthropic  # type: ignore
            except ImportError as error:
                raise CompletionError(
                    "the 'anthropic' package is not installed; "
                    "install it (pip install -e '.[anthropic]')"
                ) from error
            self._client = Anthropic(api_key=key, timeout=self.config.timeout_seconds)
        return self._client

    def complete(self, *, system: str, user: str, **options) -> str:
        client = self._sdk()
        try:
            response = client.messages.create(
                model=options.get("model", self.config.model),
                max_tokens=options.get("max_tokens", self.config.max_tokens),
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except CompletionError:
            raise
        except Exception as error:  # SDK raises a broad family of errors
            raise CompletionError(
                f"anthropic request failed: {type(error).__name__}"
            ) from error

        self.last_finish_reason = getattr(response, "stop_reason", None)
        usage = getattr(response, "usage", None)
        if usage is None:
            self.last_usage = None
        else:
            input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
            total_tokens = getattr(usage, "total_tokens", None)
            self.last_usage = {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": int(total_tokens) if total_tokens is not None
                else input_tokens + output_tokens,
            }

        text = "".join(
            getattr(block, "text", "")
            for block in (getattr(response, "content", None) or [])
            if getattr(block, "type", None) == "text"
        ).strip()
        if self.last_finish_reason == "max_tokens":
            self.truncated.append(text)
        if not text:
            raise CompletionError(
                "anthropic returned an empty completion "
                f"(finish_reason={self.last_finish_reason})",
                finish_reason=self.last_finish_reason,
            )
        return text
