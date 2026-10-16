"""Evaluation runner: generate -> (guardrail loop) -> execute -> compare -> classify -> record."""
from __future__ import annotations

import hashlib
import json
import platform
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import taxonomy as T
from .classify import classify
from .compare import compare
from .executor import Executor
from .lint import BLOCK, decide, lint
from .llm import LLMResponse, Provider, cost_usd, extract_sql
from .paths import RUNS_DIR
from .questions import Question
from .semantic import SYSTEM_PROMPT, build_context, build_user_prompt

MAX_GUARDRAIL_RETRIES = 1


def _environment() -> dict:
    import duckdb, sqlglot
    return {"python": platform.python_version(), "duckdb": duckdb.__version__, "sqlglot": sqlglot.__version__,
            "os": platform.system()}


def _generate(provider: Provider, q: Question, condition: str, trial: int) -> dict:
    """Produce the SQL for one question under one condition; C3 adds a lint-and-retry loop."""
    user = build_user_prompt(condition, q.question)
    messages = [{"role": "user", "content": user}]
    meta = {"qid": q.id, "trial": trial, "condition": condition}
    attempts, in_tok, out_tok, latency = 0, 0, 0, 0.0
    first_sql = None
    while True:
        resp: LLMResponse = provider.generate(SYSTEM_PROMPT, messages, meta)
        attempts += 1
        in_tok += resp.input_tokens
        out_tok += resp.output_tokens
        latency += resp.latency_s
        sql = extract_sql(resp.text)
        first_sql = first_sql or sql
        if condition != "C3" or attempts > MAX_GUARDRAIL_RETRIES:
            break
        blocking = [f for f in lint(sql) if f.severity == BLOCK]
        if not blocking:
            break
        feedback = "An automated review flagged this query:\n" + "\n".join(f"- {f.rule}: {f.message}" for f in blocking) \
                   + "\nRewrite the query so these issues are fixed. Return only the SQL in a ```sql fence."
        messages += [{"role": "assistant", "content": resp.text}, {"role": "user", "content": feedback}]
    return {"sql": sql, "first_sql": first_sql, "attempts": attempts, "input_tokens": in_tok,
            "output_tokens": out_tok, "latency_s": latency}


def run(provider: Provider, questions: list[Question], conditions: list[str], trials: int = 1,
        workers: int = 4, tag: str | None = None, out_dir: Path | None = None, progress: bool = True) -> Path:
    run_id = tag or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{provider.model}"
    out = (out_dir or RUNS_DIR) / run_id
    out.mkdir(parents=True, exist_ok=True)
    jobs = [(q, c, t) for c in conditions for t in range(trials) for q in questions]

    with Executor() as ex:
        gold = {q.id: ex.run(q.gold_sql) for q in questions}
        for qid, g in gold.items():
            assert g.ok, f"gold query {qid} failed: {g.error}"

        def work(job):
            q, c, t = job
            return job, _generate(provider, q, c, t)

        records = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for n, (job, gen) in enumerate(pool.map(work, jobs), 1):
                q, c, t = job
                res = ex.run(gen["sql"])
                cmp = compare(gold[q.id], res, q.ordered)
                att = classify(q, gen["sql"], res, cmp)
                first_findings = lint(gen["first_sql"])
                if gen["first_sql"] == gen["sql"]:
                    first_correct = cmp.match
                else:
                    first_correct = compare(gold[q.id], ex.run(gen["first_sql"]), q.ordered).match
                records.append({
                    "run_id": run_id, "model": provider.model, "condition": c, "trial": t, "qid": q.id,
                    "family": q.family, "difficulty": q.difficulty, "metric": q.metric, "dataset": q.dataset,
                    "weight": q.weight, "first_correct": first_correct,
                    "sql": gen["sql"], "first_sql": gen["first_sql"], "attempts": gen["attempts"],
                    "exec_status": res.status, "exec_ms": round(res.elapsed_ms, 2),
                    "correct": cmp.match, "reason": cmp.reason, "label": att.label, "signals": att.signals,
                    "rules": att.findings, "guardrail_first": decide(first_findings),
                    "guardrail_rules_first": [f.rule for f in first_findings],
                    "input_tokens": gen["input_tokens"], "output_tokens": gen["output_tokens"],
                    "latency_s": round(gen["latency_s"], 3),
                    "cost_usd": cost_usd(provider.model, gen["input_tokens"], gen["output_tokens"]),
                    "evidence": att.evidence,
                })
                if progress and n % 20 == 0:
                    print(f"  {n}/{len(jobs)} done")
    with (out / "predictions.jsonl").open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, default=str) + "\n")
    meta = {"run_id": run_id, "model": provider.model, "provider": provider.name, "conditions": conditions,
            "trials": trials, "n_questions": len(questions), "created": datetime.now(timezone.utc).isoformat(),
            "max_guardrail_retries": MAX_GUARDRAIL_RETRIES, "max_tokens": getattr(provider, "max_tokens", None),
            "temperature": "provider default (not overridden)",
            "prompt_sha256": {c: hashlib.sha256((SYSTEM_PROMPT + build_context(c)).encode()).hexdigest()[:16] for c in conditions},
            "question_ids_sha256": hashlib.sha256(",".join(q.id for q in questions).encode()).hexdigest()[:16],
            "environment": _environment()}
    (out / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return out
