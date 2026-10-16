"""Evaluate the linter and classifier on the HOLDOUT set (questions/holdout.yaml).

The development set (questions/0*.yaml) was written alongside the rules, so agreement there is partly circular.
The holdout set was written afterwards, in different phrasing and SQL styles, with deliberately evasive wrong
queries and unusually-written correct ones. Its first measurement is frozen in results/holdout_v1.json.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict

from .classify import classify
from .compare import compare
from .executor import Executor
from .lint import BLOCK, REVIEW, decide, lint
from .paths import RESULTS_DIR
from .questions import load_holdout


def measure() -> dict:
    qs = load_holdout()
    wrong_rows, ok_rows, problems = [], [], []
    with Executor() as ex:
        for q in qs:
            g = ex.run(q.gold_sql)
            assert g.ok and g.rows, f"holdout gold {q.id} failed"
            ok_rows.append({"id": q.id, "kind": "gold", "decision": decide(lint(q.gold_sql, q)),
                            "block_agnostic": decide(lint(q.gold_sql)) == BLOCK,
                            "rules": [f.rule for f in lint(q.gold_sql)]})
            for n, s in enumerate(q.correct):
                r = ex.run(s)
                if not compare(g, r, q.ordered).match:
                    problems.append(f"{q.id} correct[{n}] does not match gold")
                fs = lint(s)
                ok_rows.append({"id": q.id, "kind": "correct_variant", "decision": decide(fs),
                                "block_agnostic": decide(fs) == BLOCK, "rules": [f.rule for f in fs]})
            for n, w in enumerate(q.wrong):
                r = ex.run(w["sql"])
                cmp = compare(g, r, q.ordered)
                if cmp.match:
                    problems.append(f"{q.id} wrong[{n}] equals gold")
                att = classify(q, w["sql"], r, cmp)
                fs = lint(w["sql"])                       # what production sees: SQL only
                wrong_rows.append({
                    "id": q.id, "variant": n, "family": q.family, "expected": w["expected_failure"],
                    "evasive": bool(w.get("evasive")), "classified": att.label,
                    "decision": decide(fs), "rules": [f.rule for f in fs],
                    "taxonomy_signals": sorted({f.taxonomy for f in fs if f.taxonomy})})
    n = len(wrong_rows)
    flagged = sum(r["decision"] != "ALLOW" for r in wrong_rows)
    blocked = sum(r["decision"] == BLOCK for r in wrong_rows)
    right = [r for r in ok_rows if r["kind"] != "gold" or True]
    by_class = defaultdict(lambda: Counter())
    for r in wrong_rows:
        by_class[r["expected"]]["n"] += 1
        by_class[r["expected"]]["flagged"] += r["decision"] != "ALLOW"
        by_class[r["expected"]]["blocked"] += r["decision"] == BLOCK
    ev = [r for r in wrong_rows if r["evasive"]]
    pl = [r for r in wrong_rows if not r["evasive"]]
    cv = [r for r in ok_rows if r["kind"] == "correct_variant"]
    out = {
        "n_questions": len(qs), "n_wrong_variants": n, "n_correct_variants": len(cv),
        "authoring_problems": problems,
        "detection_flagged": [flagged, n], "prevention_blocked": [blocked, n],
        "plain_variants": {"n": len(pl), "flagged": sum(r["decision"] != "ALLOW" for r in pl),
                           "blocked": sum(r["decision"] == BLOCK for r in pl)},
        "evasive_variants": {"n": len(ev), "flagged": sum(r["decision"] != "ALLOW" for r in ev),
                             "blocked": sum(r["decision"] == BLOCK for r in ev)},
        "false_blocks_correct_variants": [sum(r["block_agnostic"] for r in cv), len(cv)],
        "false_flags_correct_variants": [sum(r["decision"] != "ALLOW" for r in cv), len(cv)],
        "gold_blocked": sum(r["block_agnostic"] for r in ok_rows if r["kind"] == "gold"),
        "classifier_agreement": [sum(r["expected"] == r["classified"] for r in wrong_rows), n],
        "by_class": {k: dict(v) for k, v in by_class.items()},
        "misses": [r for r in wrong_rows if r["decision"] == "ALLOW"],
        "false_blocks": [r for r in cv if r["block_agnostic"]],
        "misclassified": [r for r in wrong_rows if r["expected"] != r["classified"]],
    }
    return out


if __name__ == "__main__":
    o = measure()
    name = "holdout_v1.json" if "--freeze" in sys.argv else "holdout.json"
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / name).write_text(json.dumps(o, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in o.items() if k not in ("misses", "false_blocks", "misclassified")}, indent=2))
    print("\nMISSES (wrong query the linter ALLOWed):")
    for m in o["misses"]:
        print(" ", m["id"], m["variant"], m["expected"], "evasive" if m["evasive"] else "plain")
    print("FALSE BLOCKS (correct query blocked):")
    for m in o["false_blocks"]:
        print(" ", m["id"], m["rules"])
    print("MISCLASSIFIED:")
    for m in o["misclassified"]:
        print(" ", m["id"], m["variant"], m["expected"], "->", m["classified"])
