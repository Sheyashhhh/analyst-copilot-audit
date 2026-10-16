"""Write REAL model results into README.md between markers. Refuses scripted (self-test) runs."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from . import report, taxonomy as T
from .paths import ROOT, RUNS_DIR
from .stats import pct

START, END = "<!-- RESULTS:START -->", "<!-- RESULTS:END -->"


def results_markdown(run_dirs: list[Path]) -> str:
    df, metas = report.load_runs(run_dirs)
    bad = [m["run_id"] for m in metas if m["provider"] == "scripted"]
    if bad:
        raise SystemExit(f"Refusing to publish scripted self-test runs as model results: {bad}")
    out = []
    summ = report.summarize(df)
    for m in metas:
        rid = m["run_id"]
        s = summ[summ.run == rid]
        out.append(f"**Model:** `{m['model']}`. **Run:** `{rid}` ({m['created'][:10]}). **Questions:** {m['n_questions']} x "
                   f"{m['trials']} trial(s). Full report: `results/REPORT.md`.\n")
        out += ["| Condition | Accuracy (95% CI) | Silent-error rate | Risk-weighted error | $/question | Median latency |",
                "|---|---|---:|---:|---:|---:|"]
        label = {"C0": "C0 DDL only", "C1": "C1 + column descriptions", "C2": "C2 + rules and metrics", "C3": "C3 + guardrail retry"}
        for _, r in s.iterrows():
            cost = "n/a" if np.isnan(r.cost_per_q) else f"${r.cost_per_q:.4f}"
            out.append(f"| {label[r.condition]} | **{pct(r.accuracy)}** ({pct(r.ci_lo)} to {pct(r.ci_hi)}) | {pct(r.silent_error_rate)} | "
                       f"{pct(r.risk_weighted_error)} | {cost} | {r.latency_median:.2f}s |")
        out.append("")
        conds = list(s.condition)
        for a, b in [("C0", "C1"), ("C1", "C2"), ("C0", "C2"), ("C2", "C3")]:
            if a in conds and b in conds:
                n, oa, ob, p, diff, lo, hi = report._paired(df, rid, a, b)
                out.append(f"- **{a} to {b}:** {diff*100:+.1f} points (95% CI {lo*100:+.1f} to {hi*100:+.1f}), "
                           f"{ob} fixed / {oa} broken, McNemar p = {p:.3f}")
        d = df[df.run_id == rid]
        top = conds[-1]
        f = d[(d.condition == top) & (~d.correct)]
        if len(f):
            lab = f.label.value_counts()
            out += ["", f"**Residual failures under {top}** ({len(f)}): " + "; ".join(f"{T.CLASSES[k].title} {v}" for k, v in lab.items()) + ".",
                    f"Unattributed to any known class: {int((f.label == T.OTHER).sum())}."]
            worst = f.sort_values("weight", ascending=False).head(3)
            out += ["", f"Highest-risk failures under {top}:", ""]
            for _, r in worst.iterrows():
                out.append(f"- `{r.qid}` ({T.CLASSES[r.label].title}): `{' '.join(str(r.sql).split())[:160]}`")
        out += ["", "Automation policy from this run:", ""]
        pol = report.policy(df, rid, top)
        out += ["| Family | n | Accuracy (95% CI) | Policy |", "|---|---:|---|---|"]
        for _, r in pol.iterrows():
            out.append(f"| {r['family']} | {r['n questions']} | {r['accuracy']} ({r['95% CI']}) | {r['policy']} |")
        out.append("")
    return "\n".join(out)


def publish(run_names: list[str] | None = None) -> Path:
    dirs = [RUNS_DIR / n for n in run_names] if run_names else \
        [d for d in sorted(RUNS_DIR.glob("*")) if d.is_dir() and not d.name.startswith("selftest")]
    if not dirs:
        raise SystemExit("No model runs found in results/runs. Run `python -m copilot_audit run` first.")
    md = results_markdown(dirs[-1:])
    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    if START not in text or END not in text:
        raise SystemExit("README.md has no results markers.")
    head, rest = text.split(START, 1)
    _, tail = rest.split(END, 1)
    readme.write_text(head + START + "\n" + md + "\n" + END + tail, encoding="utf-8")
    return readme
