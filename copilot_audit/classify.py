"""Attribute each non-matching query to a taxonomy class."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import taxonomy as T
from .compare import Comparison
from .executor import ExecResult
from .lint import lint


@dataclass
class Attribution:
    label: str
    signals: list[str] = field(default_factory=list)   # every class the static analysis evidences
    findings: list[str] = field(default_factory=list)  # rule ids
    evidence: dict = field(default_factory=dict)


def classify(question, sql: str, res: ExecResult, cmp: Comparison) -> Attribution:
    if cmp.match:
        return Attribution(T.PASS)
    if res.status in ("blocked", "timeout"):
        return Attribution(T.UNSAFE, evidence={"error": res.error})
    if res.status == "error":
        label = T.SCHEMA_ERROR if res.error_kind == "schema" else T.SYNTAX_ERROR
        return Attribution(label, evidence={"error": res.error})
    findings = lint(sql, question)
    signals = sorted({f.taxonomy for f in findings if f.taxonomy})
    rules = [f.rule for f in findings]
    ev = dict(cmp.detail, reason=cmp.reason)
    for p in T.PRIORITY:
        if p in signals:
            return Attribution(p, signals, rules, ev)
    return Attribution(T.OTHER, signals, rules, ev)
