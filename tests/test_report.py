"""Drive the full multi-condition report path with a synthetic provider whose accuracy per condition is known."""
import json

import pytest

from copilot_audit import report
from copilot_audit.llm import LLMResponse, Provider
from copilot_audit.questions import holdout_as_questions, load_questions
from copilot_audit.runner import run


class Staged(Provider):
    """C0 wrong on all traps; C1 wrong on fan-out/definition; C2 wrong on fan-out only;
    C3 repeats C2's first answer but fixes it after the guardrail feedback. Holdout: wrong in C0 only."""
    name, model = "synthetic", "claude-sonnet-5-5"

    def __init__(self, qs):
        self.qs = {q.id: q for q in qs}

    def generate(self, system, messages, meta):
        q, cond, retry = self.qs[meta["qid"]], meta["condition"], len(messages) > 1
        wrong_in = {"C0": {"fanout", "definition", "soft_delete", "status_case", "date_boundary", "timezone", "entity_grain"},
                    "C1": {"fanout", "definition"}, "C2": {"fanout"}, "C3": {"fanout"}}[cond]
        if q.id.startswith("H"):
            sql = "SELECT 1" if cond == "C0" else q.gold_sql
        elif q.family in wrong_in and q.naive_sql and not (cond == "C3" and retry):
            sql = q.naive_sql
        else:
            sql = q.gold_sql
        return LLMResponse(f"```sql\n{sql}\n```", 1500, 60, 0.4 if not retry else 0.5)


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory):
    qs = load_questions() + holdout_as_questions()
    out = tmp_path_factory.mktemp("runs")
    d = run(Staged(qs), qs, ["C0", "C1", "C2", "C3"], trials=2, workers=1, out_dir=out, tag="syn", progress=False)
    return d, qs


def test_accuracy_ladder_and_costs_are_recorded(synthetic):
    d, _ = synthetic
    df, metas = report.load_runs([d])
    s = report.summarize(df).set_index("condition")
    assert s.accuracy["C0"] < s.accuracy["C1"] < s.accuracy["C2"] < s.accuracy["C3"]
    assert s.cost_per_q["C0"] > 0 and s.attempts["C3"] > s.attempts["C2"]          # retries are billed and counted
    assert metas[0]["environment"]["duckdb"] and set(metas[0]["prompt_sha256"]) == {"C0", "C1", "C2", "C3"}


def test_guardrail_four_measures_are_consistent_with_raw_records(synthetic):
    d, _ = synthetic
    df, _ = report.load_runs([d])
    g = report.guardrail_table(df, "syn").set_index("condition")
    c3 = df[df.condition == "C3"]
    retried = c3[c3.attempts > 1]
    fixed = int(((~retried.first_correct) & retried.correct).sum())
    assert g.loc["C3", "recovery (retry fixed)"] == f"{fixed}/{int((~retried.first_correct).sum())}"
    assert fixed > 0 and g.loc["C3", "regressions (retry broke)"] == "0"


def test_c3_retry_only_fires_when_the_guardrail_blocks(synthetic):
    d, _ = synthetic
    df, _ = report.load_runs([d])
    c3 = df[df.condition == "C3"]
    assert (c3[c3.attempts > 1].guardrail_first == "BLOCK").all()


def test_full_report_contains_every_section(synthetic, tmp_path, monkeypatch):
    d, _ = synthetic
    monkeypatch.setattr(report, "IMG_DIR", tmp_path)
    out = report.build_report([d], tmp_path / "R.md")
    txt = out.read_text(encoding="utf-8")
    for needle in ["Lift (paired by question)", "Stability across trials", "Development set vs holdout",
                   "Failure taxonomy", "Unattributed failures", "detection (flagged)", "recovery (retry fixed)",
                   "Automation policy", "Silent-error rate", "End-to-end cost of the retry loop", "bootstrap 95% CI"]:
        assert needle in txt, needle
    df, m = report.load_runs([d])
    assert report.figures_run(df, "syn", "claude-sonnet-5-5")        # all figures render without error


def test_policy_never_automates_small_or_registry_families(synthetic):
    d, _ = synthetic
    df, _ = report.load_runs([d])
    pol = report.policy(df, "syn", "C3").set_index("family")
    assert pol.loc["Metric definitions", "policy"] == "REGISTRY ONLY"
    assert not any("AUTOMATE" in p for p in pol.policy)             # every family has far fewer than ~35 questions
    assert any("cannot certify" in r for r in pol.reason)


def test_dev_holdout_split_matches_records(synthetic):
    d, _ = synthetic
    df, _ = report.load_runs([d])
    c0 = df[df.condition == "C0"]
    assert c0[c0.dataset == "holdout"].correct.mean() == 0.0
    assert set(df.dataset) == {"dev", "holdout"}


def test_bootstrap_and_certification_math():
    from copilot_audit.stats import certify_n, paired_bootstrap_ci, wilson
    diff, lo, hi = paired_bootstrap_ci([0] * 50, [1] * 50)
    assert diff == 1.0 and lo == 1.0 and hi == 1.0
    d, lo, hi = paired_bootstrap_ci([0, 1] * 40, [1, 1] * 40)
    assert lo < d < hi
    assert wilson(14, 14)[0] < 0.90 <= wilson(certify_n(1.0), certify_n(1.0))[0]
    assert 30 <= certify_n(1.0) <= 40


def test_publish_writes_real_results_into_readme_and_refuses_scripted(synthetic, tmp_path, monkeypatch):
    from copilot_audit import publish as pub
    d, _ = synthetic
    readme = tmp_path / "README.md"
    readme.write_text("intro\n<!-- RESULTS:START -->\nPENDING\n<!-- RESULTS:END -->\noutro\n", encoding="utf-8")
    monkeypatch.setattr(pub, "ROOT", tmp_path)
    monkeypatch.setattr(pub, "RUNS_DIR", d.parent)
    pub.publish(["syn"])
    txt = readme.read_text(encoding="utf-8")
    assert "PENDING" not in txt and txt.startswith("intro") and txt.rstrip().endswith("outro")
    assert "C3 + guardrail retry" in txt and "Automation policy from this run" in txt and "claude-sonnet-5-5" in txt

    from copilot_audit.llm import ScriptedProvider
    from copilot_audit.questions import load_questions
    from copilot_audit.runner import run
    qs = load_questions()[:3]
    run(ScriptedProvider({q.id: q.gold_sql for q in qs}, "oracle"), qs, ["C0"], out_dir=d.parent, tag="selftest-x", progress=False)
    with pytest.raises(SystemExit, match="scripted"):
        pub.results_markdown([d.parent / "selftest-x"])
