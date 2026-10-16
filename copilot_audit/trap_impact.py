"""Measure what each trap does to the answer, and how well the linter / classifier recognise it.

Everything here is computed from the real warehouse: no model is involved. For every trap question we
execute the gold query and the single-defect trap query and record how far apart they are.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from decimal import Decimal

from . import taxonomy as T
from .classify import classify
from .compare import compare
from .executor import Executor
from .lint import BLOCK, REVIEW, decide, lint
from .paths import RESULTS_DIR
from .questions import Question, load_questions


def _scalar(res):
    if len(res.rows) == 1 and len(res.rows[0]) == 1 and isinstance(res.rows[0][0], (int, float, Decimal)):
        return float(res.rows[0][0])
    return None


def measure(questions: list[Question] | None = None) -> dict:
    questions = questions or load_questions()
    rows, guard = [], {"gold": Counter(), "trap": Counter(), "alt": Counter()}
    confusion = Counter()
    with Executor() as ex:
        for q in questions:
            g = ex.run(q.gold_sql)
            gd = decide(lint(q.gold_sql, q))
            guard["gold"][gd] += 1
            gblock_non_policy = any(f.severity == BLOCK for f in lint(q.gold_sql, q))
            for a in q.alternates:
                ar = ex.run(a)
                assert compare(g, ar, q.ordered).match, f"alternate for {q.id} disagrees with gold"
                guard["alt"][decide(lint(a, q))] += 1
            if not q.naive_sql:
                continue
            n = ex.run(q.naive_sql)
            cmp = compare(g, n, q.ordered)
            att = classify(q, q.naive_sql, n, cmp)
            guard["trap"][decide(lint(q.naive_sql))] += 1      # question-agnostic: what production would see
            confusion[(q.expected_failure, att.label)] += 1
            gs, ns = _scalar(g), _scalar(n)
            rows.append({
                "id": q.id, "family": q.family, "expected": q.expected_failure, "classified": att.label,
                "differs_from_gold": not cmp.match, "gold": gs, "trap": ns,
                "ratio": (ns / gs) if gs not in (None, 0) and ns is not None else None,
                "rows_gold": len(g.rows), "rows_trap": len(n.rows),
                "guardrail_decision": decide(lint(q.naive_sql)), "guardrail_rules": [f.rule for f in lint(q.naive_sql)],
            })
    agree = sum(1 for r in rows if r["expected"] == r["classified"])
    out = {
        "n_questions": len(questions), "n_trap_queries": len(rows),
        "all_traps_change_the_answer": all(r["differs_from_gold"] for r in rows),
        "classifier_agreement": {"agree": agree, "total": len(rows)},
        "guardrail": {k: dict(v) for k, v in guard.items()},
        "rows": rows,
    }
    (RESULTS_DIR).mkdir(exist_ok=True)
    (RESULTS_DIR / "trap_impact.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    return out


def family_summary(out: dict) -> dict[str, dict]:
    fam = defaultdict(list)
    for r in out["rows"]:
        if r["ratio"] is not None:
            fam[r["family"]].append(r["ratio"])
    return {k: {"n": len(v), "min": min(v), "median": sorted(v)[len(v) // 2], "max": max(v)} for k, v in fam.items()}


if __name__ == "__main__":
    o = measure()
    print(json.dumps({k: v for k, v in o.items() if k != "rows"}, indent=2))
