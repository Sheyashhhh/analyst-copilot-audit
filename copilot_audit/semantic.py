"""Render the warehouse schema + semantic layer into prompt context, one cumulative tier at a time.

Conditions
----------
C0  ddl            CREATE TABLE statements only. What any text-to-SQL demo gives the model.
C1  +descriptions  C0 plus column descriptions (what each column is, units, time-zone convention).
C2  +metrics       C1 plus business rules and defined metrics (the full semantic layer).
C3  +guardrail     C2 plus a lint-and-retry loop (implemented in runner.py, same prompt as C2).
"""
from __future__ import annotations

import re
from functools import lru_cache

import yaml

from .paths import SCHEMA_SQL, SEMANTIC_YAML

CONDITIONS = {
    "C0": "DDL only",
    "C1": "DDL + column descriptions",
    "C2": "DDL + descriptions + rules & metrics (semantic layer)",
    "C3": "Semantic layer + guardrail retry loop",
}

SYSTEM_PROMPT = (
    "You are a senior analytics engineer answering business questions against a DuckDB data warehouse. "
    "Write exactly one read-only DuckDB SQL query that answers the question. "
    "Return only the SQL inside a single ```sql code fence, with no explanation. "
    "Use ROUND(...) to the precision the question requests, and add ORDER BY only when the question asks for ordering."
)


@lru_cache(maxsize=1)
def _layer() -> dict:
    return yaml.safe_load(SEMANTIC_YAML.read_text(encoding="utf-8"))


def _ddl_blocks() -> list[tuple[str, str]]:
    sql = SCHEMA_SQL.read_text(encoding="utf-8")
    sql = re.sub(r"--.*", "", sql)
    blocks = re.findall(r"CREATE TABLE (\w+) \((.*?)\n\);", sql, flags=re.S)
    return [(name, body) for name, body in blocks]


def render_ddl(descriptions: bool) -> str:
    layer = _layer()["tables"]
    out = []
    for name, body in _ddl_blocks():
        entries: list[tuple[str, str]] = []
        for raw in body.strip().splitlines():
            code = raw.strip().rstrip(",")
            if not code:
                continue
            m = re.match(r"(\w+)\s", code)
            note = ""
            if descriptions and m and m.group(1).upper() != "PRIMARY":
                note = layer.get(name, {}).get("columns", {}).get(m.group(1), "")
            entries.append((code, note))
        lines = []
        for i, (code, note) in enumerate(entries):
            comma = "," if i < len(entries) - 1 else ""
            lines.append(f"  {code}{comma}" + (f"  -- {note}" if note else ""))
        header = f"-- {name}"
        if descriptions:
            t = layer.get(name, {})
            header += f": {t.get('description', '')} Grain: {t.get('grain', '')}."
        out.append(f"{header}\nCREATE TABLE {name} (\n" + "\n".join(lines) + "\n);")
    return "\n\n".join(out)


def render_rules_and_metrics() -> str:
    layer = _layer()
    rules = "\n".join(f"{i}. {r}" for i, r in enumerate(layer["rules"], 1))
    metrics = []
    for key, m in layer["metrics"].items():
        metrics.append(f"- {key} (also called: {', '.join(m['aliases'])})\n  Definition: {m['definition']}\n  Canonical SQL:\n"
                       + "\n".join("    " + l for l in m["sql"].strip().splitlines()))
    return (f"Warehouse conventions (as-of date {layer['as_of_date']}, local time zone {layer['timezone']}):\n{rules}\n\n"
            "Defined business metrics. When a question uses a defined metric (or one of its aliases), "
            "implement exactly that definition:\n" + "\n".join(metrics))


def build_context(condition: str) -> str:
    if condition == "C0":
        return "Schema:\n\n" + render_ddl(descriptions=False)
    if condition == "C1":
        return "Schema (with column descriptions):\n\n" + render_ddl(descriptions=True)
    if condition in {"C2", "C3"}:
        return ("Schema (with column descriptions):\n\n" + render_ddl(descriptions=True)
                + "\n\n" + render_rules_and_metrics())
    raise ValueError(f"unknown condition {condition}")


def build_user_prompt(condition: str, question: str) -> str:
    return f"{build_context(condition)}\n\nQuestion: {question}\n"
