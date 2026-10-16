"""Internal consistency of the failure taxonomy and the guardrail on the curated trap set."""
from collections import Counter

from copilot_audit import taxonomy as T
from copilot_audit.classify import classify
from copilot_audit.compare import compare
from copilot_audit.lint import BLOCK, decide, lint


def test_classifier_recovers_the_labelled_trap_for_every_trap_query(executor, questions, gold):
    wrong = []
    for q in questions:
        if not q.naive_sql:
            continue
        r = executor.run(q.naive_sql)
        att = classify(q, q.naive_sql, r, compare(gold[q.id], r, q.ordered))
        if att.label != q.expected_failure:
            wrong.append((q.id, q.expected_failure, att.label))
    assert not wrong, wrong


def test_trap_queries_are_attributed_to_a_single_class(executor, questions, gold):
    for q in questions:
        if q.naive_sql:
            sig = {f.taxonomy for f in lint(q.naive_sql, q) if f.taxonomy}
            assert sig == {q.expected_failure}, (q.id, sig)


def test_gold_queries_are_never_blocked(questions):
    blocked = [q.id for q in questions if decide(lint(q.gold_sql, q)) == BLOCK]
    assert not blocked, blocked


def test_correct_alternates_are_never_blocked(questions):
    for q in questions:
        for a in q.alternates:
            assert decide(lint(a, q)) != BLOCK, q.id


def test_control_questions_are_allowed_outright(questions):
    for q in questions:
        if q.family == "control":
            assert decide(lint(q.gold_sql, q)) == "ALLOW", q.id


def test_engine_errors_are_classified_as_loud_failures(executor, questions, gold):
    q = questions[0]
    r = executor.run("SELECT nonexistent_col FROM fact_orders")
    att = classify(q, "SELECT nonexistent_col FROM fact_orders", r, compare(gold[q.id], r))
    assert att.label == T.SCHEMA_ERROR and not T.CLASSES[att.label].silent


def test_taxonomy_lists_each_class_once():
    assert len(T.CLASSES) == len({c.code for c in T.CLASSES.values()})
