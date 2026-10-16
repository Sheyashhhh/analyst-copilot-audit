import json

from copilot_audit.llm import LLMResponse, Provider, ScriptedProvider, extract_sql
from copilot_audit.runner import run


def test_extract_sql():
    assert extract_sql("Here:\n```sql\nSELECT 1;\n```\nthanks") == "SELECT 1"
    assert extract_sql("SELECT 2") == "SELECT 2"


def _records(out):
    return [json.loads(l) for l in (out / "predictions.jsonl").read_text(encoding="utf-8").splitlines()]


def test_oracle_scores_perfectly_and_trap_provider_fails_only_traps(questions, tmp_path):
    o = run(ScriptedProvider({q.id: q.gold_sql for q in questions}, "oracle"), questions, ["C0"], out_dir=tmp_path, tag="o", progress=False)
    assert all(r["correct"] for r in _records(o))
    t = run(ScriptedProvider({q.id: q.naive_sql or q.gold_sql for q in questions}, "t"), questions, ["C0"], out_dir=tmp_path, tag="t", progress=False)
    recs = _records(t)
    for r, q in zip(recs, questions):
        assert r["correct"] is (not q.is_trap), q.id
        if q.is_trap:
            assert r["label"] == q.expected_failure


class FlawedThenFixed(Provider):
    """First answer is the trap query; after guardrail feedback it returns gold."""
    name, model = "scripted", "flawed-then-fixed"

    def __init__(self, qs):
        self.qs = {q.id: q for q in qs}

    def generate(self, system, messages, meta):
        q = self.qs[meta["qid"]]
        retry = len(messages) > 1
        return LLMResponse(f"```sql\n{q.gold_sql if retry or not q.naive_sql else q.naive_sql}\n```", 10, 5, 0.1)


def test_c3_guardrail_loop_retries_only_when_blocked(questions, tmp_path):
    qs = [q for q in questions if q.family in {"soft_delete", "control"}]
    out = run(FlawedThenFixed(qs), qs, ["C2", "C3"], out_dir=tmp_path, tag="c3", progress=False)
    recs = _records(out)
    c2 = [r for r in recs if r["condition"] == "C2"]
    c3 = [r for r in recs if r["condition"] == "C3"]
    assert not any(r["correct"] for r in c2 if r["family"] == "soft_delete")
    assert all(r["correct"] for r in c3)
    assert all(r["attempts"] == 2 for r in c3 if r["family"] == "soft_delete")
    assert all(r["attempts"] == 1 for r in c3 if r["family"] == "control")
    assert all(r["input_tokens"] == 20 for r in c3 if r["family"] == "soft_delete")   # both attempts are billed
