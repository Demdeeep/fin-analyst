"""Anthropic adapter contract tests using a fake SDK."""

from types import ModuleType, SimpleNamespace

import pytest

from financial_analyst.agents.providers.base import CompletionError, ProviderConfig
from financial_analyst.agents.providers.anthropic_client import AnthropicClient


def _install_fake_sdk(monkeypatch, response):
    calls = []

    class FakeAnthropic:
        def __init__(self, *, api_key, timeout):
            calls.append(("init", api_key, timeout))
            self.messages = SimpleNamespace(create=self.create)

        def create(self, **kwargs):
            calls.append(("create", kwargs))
            return response

    fake_module = ModuleType("anthropic")
    fake_module.Anthropic = FakeAnthropic
    monkeypatch.setitem(__import__("sys").modules, "anthropic", fake_module)
    return calls


def _response(stop_reason="end_turn", usage=True):
    metadata = SimpleNamespace(input_tokens=13, output_tokens=5) if usage else None
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text="  OK  ")],
        stop_reason=stop_reason,
        usage=metadata,
    )


def test_anthropic_normal_reply_records_finish_and_usage(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-secret")
    calls = _install_fake_sdk(monkeypatch, _response())
    client = AnthropicClient(ProviderConfig(api_key_env="ANTHROPIC_API_KEY"))

    assert client.complete(system="sys", user="prompt") == "OK"
    assert client.last_finish_reason == "end_turn"
    assert client.truncated == []
    assert client.last_usage == {
        "input_tokens": 13, "output_tokens": 5, "total_tokens": 18
    }
    assert calls[-1][1]["messages"] == [{"role": "user", "content": "prompt"}]
    assert calls[-1][1]["system"] == "sys"


def test_anthropic_max_tokens_records_truncation(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-secret")
    _install_fake_sdk(monkeypatch, _response(stop_reason="max_tokens"))
    client = AnthropicClient(ProviderConfig(api_key_env="ANTHROPIC_API_KEY"))

    assert client.complete(system="sys", user="prompt") == "OK"
    assert client.last_finish_reason == "max_tokens"
    assert client.truncated == ["OK"]


def test_anthropic_missing_usage_is_none(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-secret")
    _install_fake_sdk(monkeypatch, _response(usage=False))
    client = AnthropicClient(ProviderConfig(api_key_env="ANTHROPIC_API_KEY"))

    assert client.complete(system="sys", user="prompt") == "OK"
    assert client.last_finish_reason == "end_turn"
    assert client.last_usage is None


def test_anthropic_missing_key_fails_before_sdk_import(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config = ProviderConfig(api_key_env="ANTHROPIC_API_KEY")
    client = AnthropicClient(config)

    with pytest.raises(CompletionError, match="ANTHROPIC_API_KEY is not set"):
        client.complete(system="sys", user="prompt")
