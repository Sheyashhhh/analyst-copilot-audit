import types

import pytest

from copilot_audit import llm


class FakeMessages:
    def __init__(self):
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        usage = types.SimpleNamespace(input_tokens=1200, output_tokens=80)
        block = types.SimpleNamespace(type="text", text="```sql\nSELECT 1\n```")
        return types.SimpleNamespace(content=[block], usage=usage)


def make_provider(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path)
    p = llm.AnthropicProvider("claude-sonnet-5-5")
    p.client = types.SimpleNamespace(messages=FakeMessages())
    return p


def test_request_shape_usage_and_cache(monkeypatch, tmp_path):
    p = make_provider(monkeypatch, tmp_path)
    msgs = [{"role": "user", "content": "q"}]
    a = p.generate("sys", msgs, {"trial": 0})
    b = p.generate("sys", msgs, {"trial": 0})
    c = p.generate("sys", msgs, {"trial": 1})        # different trial -> new call
    assert (a.input_tokens, a.output_tokens, a.cached) == (1200, 80, False)
    assert b.cached and b.text == a.text
    assert len(p.client.messages.calls) == 2
    call = p.client.messages.calls[0]
    assert call["model"] == "claude-sonnet-5-5" and call["system"] == "sys" and "temperature" not in call


def test_cost_uses_pricing_file():
    assert llm.cost_usd("claude-sonnet-5-5", 1_000_000, 1_000_000) == pytest.approx(12.0)
    assert llm.cost_usd("unknown-model", 10, 10) is None


def test_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        llm.AnthropicProvider()
