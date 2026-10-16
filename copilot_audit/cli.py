from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import report, trap_impact, warehouse
from .env import load_dotenv
from .llm import AnthropicProvider, DEFAULT_MODEL, ScriptedProvider
from .paths import RESULTS_DIR, RUNS_DIR
from .questions import holdout_as_questions, load_questions
from .runner import run
from .semantic import CONDITIONS


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="copilot_audit", description="Audit LLM-generated SQL against a trap-laden warehouse.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="download data, build the warehouse, run trap measurements (no API key)")
    sub.add_parser("doctor", help="check Python, packages, data and API key")
    sub.add_parser("smoke", help="10-question live LLM run on conditions C0 and C2")
    sub.add_parser("build", help="build the DuckDB warehouse from data/raw")
    sub.add_parser("traps", help="measure trap impact, classifier agreement and guardrail behaviour (no LLM)")
    r = sub.add_parser("run", help="evaluate an LLM")
    r.add_argument("--model", default=DEFAULT_MODEL)
    r.add_argument("--conditions", nargs="+", default=["C0", "C1", "C2", "C3"], choices=list(CONDITIONS))
    r.add_argument("--trials", type=int, default=1)
    r.add_argument("--limit", type=int, help="only the first N questions (smoke test)")
    r.add_argument("--families", nargs="+")
    r.add_argument("--set", dest="qset", choices=["dev", "holdout", "all"], default="dev",
                   help="dev = 80 development questions, holdout = 16 unseen-style questions, all = both")
    r.add_argument("--workers", type=int, default=4)
    r.add_argument("--tag")
    pb = sub.add_parser("publish", help="write real model results into README.md (refuses scripted runs)")
    pb.add_argument("runs", nargs="*")
    sub.add_parser("holdout", help="measure linter and classifier on the holdout set")
    sub.add_parser("selftest", help="run the harness with scripted oracle / trap providers")
    rp = sub.add_parser("report", help="render REPORT.md and figures from run directories")
    rp.add_argument("runs", nargs="*", help="run directory names under results/runs (default: all non-selftest)")
    a = ap.parse_args(argv)
    load_dotenv()

    if a.cmd == "setup":
        from .setup import setup
        setup()
        return 0
    if a.cmd == "doctor":
        from .setup import doctor
        return doctor()
    if a.cmd == "publish":
        from .publish import publish
        print("updated", publish(a.runs or None))
        return 0
    if a.cmd == "holdout":
        from . import holdout
        import json as _j
        o = holdout.measure()
        print(_j.dumps({k: v for k, v in o.items() if k not in ("misses", "false_blocks", "misclassified")}, indent=2))
        return 0
    if a.cmd == "smoke":
        out = run(AnthropicProvider(DEFAULT_MODEL), load_questions()[:10], ["C0", "C2"], 1, 4, "smoke")
        print("wrote", out)
        print("report:", report.build_report([out], RESULTS_DIR / "SMOKE_REPORT.md"))
        return 0

    if a.cmd == "build":
        warehouse.build()
    elif a.cmd == "traps":
        o = trap_impact.measure()
        report.figures_trap_impact()
        print(json.dumps({k: v for k, v in o.items() if k != "rows"}, indent=2))
    elif a.cmd == "run":
        qs = {"dev": load_questions, "holdout": holdout_as_questions,
              "all": lambda: load_questions() + holdout_as_questions()}[a.qset]()
        if a.families:
            qs = [q for q in qs if q.family in a.families]
        if a.limit:
            qs = qs[: a.limit]
        out = run(AnthropicProvider(a.model), qs, a.conditions, a.trials, a.workers, a.tag)
        print("wrote", out)
    elif a.cmd == "selftest":
        qs = load_questions()
        oracle = ScriptedProvider({q.id: q.gold_sql for q in qs}, "oracle")
        traps = ScriptedProvider({q.id: (q.naive_sql or q.gold_sql) for q in qs}, "trap-injector")
        for p, tag in [(oracle, "selftest-oracle"), (traps, "selftest-traps")]:
            print("run", run(p, qs, ["C0"], 1, 1, tag, progress=False))
    elif a.cmd == "report":
        if a.runs:
            dirs = [RUNS_DIR / n for n in a.runs]
        else:
            dirs = [d for d in sorted(RUNS_DIR.glob("*")) if d.is_dir() and not d.name.startswith("selftest")]
        selftest = not dirs or all(d.name.startswith("selftest") for d in dirs)
        if not dirs:
            dirs = [d for d in sorted(RUNS_DIR.glob("selftest*")) if d.is_dir()]
        target = RESULTS_DIR / ("SELFTEST_REPORT.md" if selftest else "REPORT.md")
        print("wrote", report.build_report(dirs, target))
    return 0


if __name__ == "__main__":
    sys.exit(main())
