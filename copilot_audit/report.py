"""Charts and markdown reports."""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from . import taxonomy as T
from .paths import IMG_DIR, RESULTS_DIR
from .questions import FAMILIES
from .stats import certify_n, mcnemar_exact, paired_bootstrap_ci, pct, wilson

INK, MUTED, GRID = "#1f2937", "#6b7280", "#e5e7eb"
PAL = {"BLOCK": "#b91c1c", "REVIEW": "#d97706", "ALLOW": "#9ca3af"}
FAM_COLOR = {"fanout": "#b91c1c", "soft_delete": "#c2410c", "definition": "#7c3aed", "entity_grain": "#2563eb",
             "date_boundary": "#0f766e", "timezone": "#0891b2", "status_case": "#4d7c0f"}
FAM_LABEL = {"control": "Controls (no trap)", "soft_delete": "Soft deletes", "status_case": "Status casing",
             "fanout": "Join fan-out", "date_boundary": "Date boundaries", "timezone": "Timezones",
             "definition": "Metric definitions", "entity_grain": "Entity grain"}
COND_LABEL = {"C0": "C0  DDL only", "C1": "C1  + column\ndescriptions", "C2": "C2  + rules &\nmetrics", "C3": "C3  + guardrail\nretry"}


def _style():
    plt.rcParams.update({"font.family": "DejaVu Sans", "axes.edgecolor": GRID, "axes.labelcolor": INK,
                         "xtick.color": MUTED, "ytick.color": MUTED, "axes.spines.top": False,
                         "axes.spines.right": False, "axes.titleweight": "bold", "axes.titlesize": 12,
                         "axes.titlelocation": "left", "figure.dpi": 160, "savefig.bbox": "tight",
                         "savefig.facecolor": "white"})


# ---------------------------------------------------------------- trap-impact figures (no model involved)
def figures_trap_impact(path: Path = RESULTS_DIR / "trap_impact.json") -> list[Path]:
    _style()
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    d = json.loads(path.read_text(encoding="utf-8"))
    df = pd.DataFrame(d["rows"])
    out = []

    # 1. deviation from the correct answer, scalar questions
    s = df[df.ratio.notna()].copy()
    s["dev"] = (s.ratio - 1) * 100
    order = [f for f in ["fanout", "definition", "soft_delete", "status_case", "date_boundary", "timezone", "entity_grain"]
             if f in set(s.family)]
    fig, ax = plt.subplots(figsize=(9, 5.2))
    rng = np.random.default_rng(3)
    for i, fam in enumerate(order):
        sub = s[s.family == fam]
        y = i + rng.uniform(-0.16, 0.16, len(sub))
        ax.scatter(sub.dev, y, s=46, color=FAM_COLOR[fam], alpha=.85, edgecolor="white", linewidth=.7, zorder=3)
    ax.axvline(0, color=INK, lw=1)
    ax.set_yticks(range(len(order)), [FAM_LABEL[f] for f in order])
    ax.invert_yaxis()
    ax.set_xlabel("Trap answer vs correct answer (% deviation, single-number questions)")
    ax.xaxis.grid(True, color=GRID, zorder=0)
    ax.set_title("How far each trap moves the answer", pad=28)
    ax.text(0, 1.03, "Each dot is one question answered by a query that is correct except for a single trap.",
            transform=ax.transAxes, color=MUTED, fontsize=9)
    p = IMG_DIR / "trap_deviation.png"; fig.savefig(p); plt.close(fig); out.append(p)

    # 2. guardrail decision on trap queries by family
    g = df.groupby(["family", "guardrail_decision"]).size().unstack(fill_value=0)
    for c in ["BLOCK", "REVIEW", "ALLOW"]:
        if c not in g:
            g[c] = 0
    g = g.loc[[f for f in order if f in g.index]]
    fig, ax = plt.subplots(figsize=(9, 4.4))
    left = np.zeros(len(g))
    for c in ["BLOCK", "REVIEW", "ALLOW"]:
        ax.barh([FAM_LABEL[f] for f in g.index], g[c], left=left, color=PAL[c], label=c, height=.62)
        for i, (v, l) in enumerate(zip(g[c], left)):
            if v:
                ax.text(l + v / 2, i, int(v), ha="center", va="center", color="white", fontsize=9, fontweight="bold")
        left += g[c].values
    ax.invert_yaxis()
    ax.set_xlabel("Trap queries")
    ax.legend(ncol=3, frameon=False, loc="lower right", bbox_to_anchor=(1, 1.0))
    ax.set_title("What a question-agnostic SQL guardrail does with each trap", pad=26)
    p = IMG_DIR / "guardrail_on_traps.png"; fig.savefig(p); plt.close(fig); out.append(p)
    return out


# ---------------------------------------------------------------- model-run report
def load_runs(run_dirs: list[Path]) -> tuple[pd.DataFrame, list[dict]]:
    frames, metas = [], []
    for d in run_dirs:
        metas.append(json.loads((d / "meta.json").read_text(encoding="utf-8")))
        frames.append(pd.read_json(d / "predictions.jsonl", lines=True))
    return pd.concat(frames, ignore_index=True), metas


LOUD = [T.SCHEMA_ERROR, T.SYNTAX_ERROR, T.UNSAFE]


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (run, cond), g in df.groupby(["run_id", "condition"]):
        k, n = int(g.correct.sum()), len(g)
        lo, hi = wilson(k, n)
        silent = (~g.correct) & (g.exec_status == "ok")
        w = g.weight if "weight" in g else pd.Series(1, index=g.index)
        rows.append({"run": run, "model": g.model.iloc[0], "condition": cond, "n": n, "correct": k, "accuracy": k / n,
                     "ci_lo": lo, "ci_hi": hi, "silent_error_rate": silent.mean(),
                     "risk_weighted_error": float((w * (~g.correct)).sum() / w.sum()),
                     "cost_usd": g.cost_usd.sum() if g.cost_usd.notna().any() else np.nan,
                     "cost_per_q": g.cost_usd.mean() if g.cost_usd.notna().any() else np.nan,
                     "latency_mean": g.latency_s.mean(), "latency_median": g.latency_s.median(),
                     "latency_p95": g.latency_s.quantile(.95),
                     "in_tok": g.input_tokens.mean(), "out_tok": g.output_tokens.mean(),
                     "attempts": g.attempts.mean()})
    return pd.DataFrame(rows).sort_values(["run", "condition"])


def _per_question(df: pd.DataFrame, run: str, cond: str) -> pd.Series:
    """One 0/1 per question: correct in every trial. Questions, not trials, are the unit of analysis."""
    g = df[(df.run_id == run) & (df.condition == cond)]
    return (g.groupby("qid").correct.mean() == 1).astype(int)


def _paired(df: pd.DataFrame, run: str, a: str, b: str):
    x, y = _per_question(df, run, a), _per_question(df, run, b)
    j = pd.concat([x, y], axis=1, keys=["a", "b"]).dropna()
    only_a, only_b = int(((j.a == 1) & (j.b == 0)).sum()), int(((j.a == 0) & (j.b == 1)).sum())
    diff, lo, hi = paired_bootstrap_ci(list(j.a), list(j.b))
    return len(j), only_a, only_b, mcnemar_exact(only_a, only_b), diff, lo, hi


def guardrail_table(df: pd.DataFrame, run: str) -> pd.DataFrame:
    """Detection, prevention, recovery and false blocks, measured on first-attempt SQL."""
    rows = []
    for cond, g in df[df.run_id == run].groupby("condition"):
        fc = g.first_correct if "first_correct" in g else g.correct
        wrong, right = g[~fc], g[fc]
        blocked, flagged = g.guardrail_first == "BLOCK", g.guardrail_first != "ALLOW"
        row = {"condition": cond, "first-attempt wrong": len(wrong),
               "detection (flagged)": f"{int(flagged[~fc].sum())}/{len(wrong)}" if len(wrong) else "n/a",
               "prevention (blocked)": f"{int(blocked[~fc].sum())}/{len(wrong)}" if len(wrong) else "n/a",
               "false blocks": f"{int(blocked[fc].sum())}/{len(right)}" if len(right) else "n/a",
               "false flags": f"{int(flagged[fc].sum())}/{len(right)}" if len(right) else "n/a"}
        if cond == "C3":
            retried = g[g.attempts > 1]
            fixed = int(((~retried.first_correct) & retried.correct).sum()) if len(retried) else 0
            broke = int((retried.first_correct & ~retried.correct).sum()) if len(retried) else 0
            row["recovery (retry fixed)"] = f"{fixed}/{int((~retried.first_correct).sum())}" if len(retried) else "0/0"
            row["regressions (retry broke)"] = str(broke)
        rows.append(row)
    return pd.DataFrame(rows)


def figures_run(df: pd.DataFrame, run: str, tag: str) -> list[Path]:
    _style()
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    d = df[df.run_id == run]
    out = []
    conds = [c for c in ["C0", "C1", "C2", "C3"] if c in set(d.condition)]
    # accuracy by condition
    fig, ax = plt.subplots(figsize=(8, 4.6))
    for i, c in enumerate(conds):
        g = d[d.condition == c]; k, n = int(g.correct.sum()), len(g); lo, hi = wilson(k, n)
        ax.bar(i, k / n, color="#1d4ed8" if c != "C0" else "#9ca3af", width=.6)
        ax.errorbar(i, k / n, yerr=[[k / n - lo], [hi - k / n]], color=INK, capsize=4, lw=1.2)
        ax.text(i, hi + .02, pct(k / n), ha="center", fontweight="bold", color=INK)
    ax.set_xticks(range(len(conds)), [COND_LABEL[c] for c in conds])
    ax.set_ylim(0, 1.12); ax.yaxis.set_major_formatter(lambda v, _: f"{int(v*100)}%")
    ax.set_ylabel("Execution accuracy (result-set equivalence)")
    ax.set_title(f"Accuracy by context condition  -  {tag}", pad=10)
    p = IMG_DIR / "accuracy_by_condition.png"; fig.savefig(p); plt.close(fig); out.append(p)
    # family x condition heatmap
    fams = [f for f in FAMILIES if f in set(d.family)]
    m = np.array([[d[(d.family == f) & (d.condition == c)].correct.mean() for c in conds] for f in fams])
    fig, ax = plt.subplots(figsize=(7.4, 4.6))
    im = ax.imshow(m, cmap="RdYlGn", vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(conds)), [c for c in conds]); ax.set_yticks(range(len(fams)), [FAM_LABEL[f] for f in fams])
    for i in range(len(fams)):
        for j in range(len(conds)):
            ax.text(j, i, f"{m[i, j]*100:.0f}%", ha="center", va="center", fontsize=10, fontweight="bold", color=INK)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_title("Accuracy by question family", pad=10)
    p = IMG_DIR / "family_heatmap.png"; fig.savefig(p); plt.close(fig); out.append(p)
    # taxonomy of failures
    f = d[~d.correct]
    if len(f):
        t = f.groupby(["condition", "label"]).size().unstack(fill_value=0).reindex(conds).fillna(0)
        fig, ax = plt.subplots(figsize=(8.6, 4.6))
        bottom = np.zeros(len(t)); cmap = plt.get_cmap("tab20")
        for i, lab in enumerate(t.columns):
            ax.bar([COND_LABEL[c].split("\n")[0] for c in t.index], t[lab], bottom=bottom, label=T.CLASSES[lab].title, color=cmap(i), width=.62)
            bottom += t[lab].values
        ax.legend(frameon=False, fontsize=8, bbox_to_anchor=(1.01, 1), loc="upper left")
        ax.set_ylabel("Failed questions"); ax.set_title("Failure taxonomy by condition", pad=10)
        p = IMG_DIR / "failure_taxonomy.png"; fig.savefig(p); plt.close(fig); out.append(p)
    return out


def policy(df: pd.DataFrame, run: str, cond: str) -> pd.DataFrame:
    """Automation tier per family, from questions (not trials), with the statistics shown.

    AUTOMATE needs a Wilson 95% lower bound >= 90% and zero silent failures. Families that are 100% accurate on
    too few questions cannot be certified, so they stay under human review and the table says how many questions
    would be needed. Registered-metric families never leave the registry."""
    rows = []
    d = df[(df.run_id == run) & (df.condition == cond)]
    for fam in [f for f in FAMILIES if f in set(d.family)]:
        g = d[d.family == fam]
        pq = g.groupby("qid").correct.mean() == 1
        k, n = int(pq.sum()), len(pq)
        lo, hi = wilson(k, n)
        silent_q = int(g[(~g.correct) & (g.exec_status == "ok")].qid.nunique())
        need = certify_n(k / n if n else 1.0)
        if fam == "definition":
            tier, why = "REGISTRY ONLY", "competing metric definitions: answer from governed SQL, not generation"
        elif lo >= .90 and silent_q == 0:
            tier, why = "AUTOMATE with sampled audit", "lower bound >= 90%, no silent failures"
        elif k / n >= .95 and silent_q == 0:
            tier, why = "HUMAN REVIEW", f"accuracy high but n={n} cannot certify 90%; ~{need} questions needed"
        elif k / n >= .80:
            tier, why = "HUMAN REVIEW", "80-95% accuracy or silent failures present"
        else:
            tier, why = "BLOCK free-form SQL", "below 80%: use registry or governed views"
        rows.append({"family": FAM_LABEL[fam], "n questions": n, "accuracy": pct(k / n), "95% CI": f"{pct(lo)} to {pct(hi)}",
                     "silent-failure questions": silent_q, "policy": tier, "reason": why})
    return pd.DataFrame(rows)


def build_report(run_dirs: list[Path], out: Path = RESULTS_DIR / "REPORT.md") -> Path:
    df, metas = load_runs(run_dirs)
    summ = summarize(df)
    lines = ["# Evaluation report", ""]
    for m in metas:
        scripted = m["provider"] == "scripted"
        tag = "HARNESS SELF-TEST (scripted, not a model)" if scripted else m["model"]
        lines += [f"## Run `{m['run_id']}`  -  {tag}", ""]
        if scripted:
            lines += ["> These answers are produced by a script, not a language model. The oracle scores 100% and the "
                      "trap-injector scores low *by construction*. Neither number says anything about model accuracy; "
                      "they only prove that the execute, compare, classify and report pipeline works.", ""]
        lines += [f"{m['n_questions']} questions x {len(m['conditions'])} condition(s) x {m['trials']} trial(s). "
                  f"Environment: {m.get('environment', {})}. Prompt hashes: {m.get('prompt_sha256', {})}.", ""]
        s = summ[summ.run == m["run_id"]]
        lines += ["| Condition | n | Accuracy | 95% CI | Silent-error rate | Risk-weighted error | $/question |",
                  "|---|---:|---:|---|---:|---:|---:|"]
        for _, r in s.iterrows():
            cost = "n/a" if np.isnan(r.cost_per_q) else f"${r.cost_per_q:.4f}"
            lines.append(f"| {r.condition} | {r.n} | {pct(r.accuracy)} | {pct(r.ci_lo)} to {pct(r.ci_hi)} | "
                         f"{pct(r.silent_error_rate)} | {pct(r.risk_weighted_error)} | {cost} |")
        lines += ["", "Silent-error rate = wrong answers that executed successfully / all answers. "
                  "Risk-weighted error weights money and registered-metric questions 3x (see `questions.risk_weight`).", ""]
        lines += ["| Condition | Latency mean | median | p95 | Tokens in / out | Model calls per question |", "|---|---:|---:|---:|---:|---:|"]
        for _, r in s.iterrows():
            lines.append(f"| {r.condition} | {r.latency_mean:.2f}s | {r.latency_median:.2f}s | {r.latency_p95:.2f}s | "
                         f"{r.in_tok:.0f} / {r.out_tok:.0f} | {r.attempts:.2f} |")
        conds = list(s.condition)
        if "C2" in conds and "C3" in conds:
            c2, c3 = s[s.condition == "C2"].iloc[0], s[s.condition == "C3"].iloc[0]
            lat = c3.latency_mean / c2.latency_mean if c2.latency_mean else float("nan")
            cost = (c3.cost_per_q / c2.cost_per_q) if c2.cost_per_q and not np.isnan(c2.cost_per_q) else float("nan")
            lines += ["", f"End-to-end cost of the retry loop (C3 vs C2, retries included): latency x{lat:.2f}, cost x{cost:.2f}."]
        lines += ["", "### Lift (paired by question)", ""]
        for a, b in [("C0", "C1"), ("C1", "C2"), ("C0", "C2"), ("C2", "C3")]:
            if a in conds and b in conds:
                n, oa, ob, p, diff, lo, hi = _paired(df, m["run_id"], a, b)
                lines.append(f"- **{a} to {b}**: {diff*100:+.1f} points (paired bootstrap 95% CI {lo*100:+.1f} to {hi*100:+.1f}); "
                             f"{ob} questions fixed, {oa} broken, exact McNemar p = {p:.4f}, n = {n} questions.")
        d = df[df.run_id == m["run_id"]]
        if m["trials"] > 1:
            lines += ["", "### Stability across trials", ""]
            for c in conds:
                g = d[d.condition == c]
                per_trial = g.groupby("trial").correct.mean()
                consistent = (g.groupby("qid").correct.agg(lambda x: x.nunique() == 1)).mean()
                lines.append(f"- {c}: per-trial accuracy {pct(per_trial.min())} to {pct(per_trial.max())}; "
                             f"{pct(consistent)} of questions gave the same outcome in every trial.")
        lines += ["", "### Accuracy by family", ""]
        pv = d.pivot_table(index="family", columns="condition", values="correct", aggfunc="mean").reindex(
            [f for f in FAMILIES if f in set(d.family)])
        lines += ["| Family | " + " | ".join(pv.columns) + " |", "|---|" + "---:|" * len(pv.columns)]
        for fam, row in pv.iterrows():
            lines.append(f"| {FAM_LABEL[fam]} | " + " | ".join(pct(v) for v in row) + " |")
        if "dataset" in d and d.dataset.nunique() > 1:
            lines += ["", "### Development set vs holdout", "",
                      "| Condition | Dev accuracy | Holdout accuracy |", "|---|---:|---:|"]
            for c in conds:
                g = d[d.condition == c]
                lines.append(f"| {c} | {pct(g[g.dataset == 'dev'].correct.mean())} | {pct(g[g.dataset == 'holdout'].correct.mean())} |")
        lines += ["", "### Failure taxonomy", ""]
        f = d[~d.correct]
        if len(f):
            tb = f.groupby(["label", "condition"]).size().unstack(fill_value=0)
            lines += ["| Class | Silent? | " + " | ".join(tb.columns) + " |", "|---|:-:|" + "---:|" * len(tb.columns)]
            for lab, row in tb.iterrows():
                lines.append(f"| {T.CLASSES[lab].title} | {'yes' if T.CLASSES[lab].silent else 'no'} | " + " | ".join(str(v) for v in row) + " |")
            un = f[f.label == T.OTHER]
            lines += ["", f"**Unattributed failures: {len(un)} of {len(f)} ({pct(len(un)/len(f))})**. These are errors the taxonomy "
                      "cannot yet explain; read them in `predictions.jsonl` and extend the taxonomy (docs/taxonomy.md)."]
        else:
            lines.append("No failures.")
        lines += ["", "### Guardrail: detection, prevention, recovery, false blocks", "",
                  "Measured on the first-attempt SQL, before any retry. Detection = wrong answers flagged (BLOCK or REVIEW); "
                  "prevention = wrong answers blocked; false blocks = correct answers blocked.", ""]
        gt = guardrail_table(df, m["run_id"])
        lines += ["| " + " | ".join(gt.columns) + " |", "|" + "---|" * len(gt.columns)]
        for _, r in gt.iterrows():
            lines.append("| " + " | ".join("" if (isinstance(v, float) and np.isnan(v)) else str(v) for v in r) + " |")
        top = conds[-1]
        lines += ["", f"### Automation policy (from condition {top})", ""]
        pol = policy(df, m["run_id"], top)
        lines += ["| " + " | ".join(pol.columns) + " |", "|" + "---|" * len(pol.columns)]
        for _, r in pol.iterrows():
            lines.append("| " + " | ".join(str(v) for v in r) + " |")
        lines.append("")
        if not scripted:
            figures_run(df, m["run_id"], m["model"])
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
