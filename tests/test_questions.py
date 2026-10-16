import re
from collections import Counter

from copilot_audit.compare import compare
from copilot_audit.questions import FAMILIES, DIFFICULTIES


def test_bank_size_and_unique_ids(questions):
    assert 60 <= len(questions) <= 100
    assert len({q.id for q in questions}) == len(questions)


def test_family_coverage(questions):
    c = Counter(q.family for q in questions)
    assert set(c) == set(FAMILIES)
    assert all(v >= 6 for v in c.values())
    assert c["control"] >= 10


def test_schema_of_each_question(questions):
    for q in questions:
        assert q.difficulty in DIFFICULTIES, q.id
        assert q.question.strip().endswith(("?", ".")), q.id
        if q.is_trap:
            assert q.naive_sql and q.expected_failure, q.id
        else:
            assert q.naive_sql is None


def test_gold_executes_and_is_deterministic(executor, questions, gold):
    for q in questions:
        g = gold[q.id]
        assert g.ok, f"{q.id}: {g.error}"
        assert g.rows, f"{q.id} returned no rows"
        again = executor.run(q.gold_sql)
        assert again.ok, f"{q.id}: {again.error}"
        assert compare(g, again, q.ordered).match, f"{q.id} is non-deterministic"


def test_gold_never_uses_relative_dates(questions):
    for q in questions:
        assert not re.search(r"current_date|now\(\)|current_timestamp", q.gold_sql, re.I), q.id


def test_every_trap_query_is_really_wrong(executor, questions, gold):
    """A trap that returns the right answer is not a trap."""
    for q in questions:
        if q.naive_sql:
            r = executor.run(q.naive_sql)
            assert r.ok, f"{q.id}: {r.error}"
            assert not compare(gold[q.id], r, q.ordered).match, f"{q.id} trap query matches gold"


def test_alternate_formulations_match_gold(executor, questions, gold):
    n = 0
    for q in questions:
        for a in q.alternates:
            n += 1
            assert compare(gold[q.id], executor.run(a), q.ordered).match, f"{q.id} alternate disagrees"
    assert n >= 20
