"""LLM providers with on-disk caching, token accounting and latency measurement."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import yaml

from .paths import CACHE_DIR, ROOT

DEFAULT_MODEL = os.environ.get("AUDIT_MODEL", "claude-sonnet-5-5")


@dataclass
class LLMResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    cached: bool = False


def extract_sql(text: str) -> str:
    m = re.search(r"```(?:sql|duckdb)?\s*\n(.*?)```", text, flags=re.S | re.I)
    sql = (m.group(1) if m else text).strip()
    return sql.rstrip(";").strip() if sql.count(";") <= 1 else sql


def load_pricing(model: str) -> dict | None:
    p = ROOT / "config" / "pricing.yaml"
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get(model) if p.exists() else None


def cost_usd(model: str, in_tok: int, out_tok: int) -> float | None:
    pr = load_pricing(model)
    if not pr:
        return None
    return in_tok / 1e6 * pr["input_per_mtok"] + out_tok / 1e6 * pr["output_per_mtok"]


class Provider:
    name = "base"
    model = "base"

    def generate(self, system: str, messages: list[dict], meta: dict) -> LLMResponse:  # pragma: no cover
        raise NotImplementedError


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, model: str = DEFAULT_MODEL, max_tokens: int = 4096, use_cache: bool = True):
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover
            raise SystemExit("pip install anthropic") from e
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit("ANTHROPIC_API_KEY is not set. Copy .env.example to .env, paste your key after ANTHROPIC_API_KEY=, and run again.")
        self.client = anthropic.Anthropic(max_retries=5)
        self.model, self.max_tokens, self.use_cache = model, max_tokens, use_cache
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def _key(self, system, messages, meta) -> Path:
        blob = json.dumps({"m": self.model, "s": system, "msgs": messages, "t": meta.get("trial", 0)}, sort_keys=True)
        return CACHE_DIR / (hashlib.sha256(blob.encode()).hexdigest()[:32] + ".json")

    def generate(self, system, messages, meta) -> LLMResponse:
        path = self._key(system, messages, meta)
        if self.use_cache and path.exists():
            d = json.loads(path.read_text(encoding="utf-8"))
            return LLMResponse(**{**d, "cached": True})
        t0 = time.perf_counter()
        # No temperature override: recent models manage sampling/reasoning themselves.
        r = self.client.messages.create(model=self.model, max_tokens=self.max_tokens, system=system, messages=messages)
        text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
        resp = LLMResponse(text, r.usage.input_tokens, r.usage.output_tokens, time.perf_counter() - t0)
        path.write_text(json.dumps(asdict(resp)), encoding="utf-8")
        return resp


class ScriptedProvider(Provider):
    """Deterministic stand-in used for harness self-tests. It is NOT a model and its output is never
    reported as model accuracy: it exists to prove the pipeline (execute -> compare -> classify -> report)."""
    name = "scripted"

    def __init__(self, script: dict[str, str], label: str):
        self.script, self.model = script, label

    def generate(self, system, messages, meta) -> LLMResponse:
        sql = self.script[meta["qid"]]
        return LLMResponse(f"```sql\n{sql}\n```", 0, 0, 0.0)
