"""Holdout set: authoring validity, the frozen v1 measurement, and regression tests for each v2 fix."""
import json
from types import SimpleNamespace as NS

import pytest

from copilot_audit import holdout
from copilot_audit.compare import compare
from copilot_audit.lint import BLOCK, decide, lint
from copilot_audit.paths import RESULTS_DIR
from copilot_audit.questions import load_holdout


@pytest.fixture(scope="module")
def hq():
    return load_holdout()


@pytest.fixture(scope="module")
def measured():
    return holdout.measure()


def test_holdout_authoring_is_valid(executor, hq):
    """Every gold runs, every wrong variant really differs, every 'correct' variant really matches."""
    assert len(hq) >= 16
    for q in hq:
        g = executor.run(q.gold_sql)
        assert g.ok and g.rows, q.id
        for n, w in enumerate(q.wrong):
            assert not compare(g, executor.run(w["sql"]), q.ordered).match, f"{q.id} wrong[{n}] equals gold"
        for n, c in enumerate(q.correct):
            assert compare(g, executor.run(c), q.ordered).match, f"{q.id} correct[{n}] differs from gold"


def test_holdout_has_evasive_and_correct_variants(hq):
    assert sum(w.get("evasive", False) for q in hq for w in q.wrong) >= 10
    assert sum(len(q.correct) for q in hq) >= 5


def test_v1_measurement_is_frozen_and_unflattering():
    """The first holdout measurement, taken before any rule was changed. Never overwrite this file."""
    v1 = json.loads((RESULTS_DIR / "holdout_v1.json").read_text(encoding="utf-8"))
    assert v1["detection_flagged"] == [27, 32] and v1["prevention_blocked"] == [18, 32]
    assert v1["evasive_variants"] == {"n": 11, "flagged": 6, "blocked": 2}
    assert v1["false_blocks_correct_variants"] == [3, 5]
    assert v1["classifier_agreement"] == [24, 32]


def test_v2_fixes_removed_false_blocks_on_correct_sql(measured):
    assert measured["false_blocks_correct_variants"][0] == 0
    assert measured["gold_blocked"] == 0


def test_known_blind_spots_stay_documented(measured):
    """Exclusive date bounds are intent-dependent; a syntax rule cannot know 15 March should have been 16 March.
    If you fix these, update docs/guardrails.md and delete this test deliberately."""
    allowed = {(m["id"], m["variant"]) for m in measured["misses"]}
    assert {("H08", 1), ("H09", 1)} <= allowed


def test_evasive_variants_are_still_harder_than_plain_ones(measured):
    p, e = measured["plain_variants"], measured["evasive_variants"]
    assert e["flagged"] / e["n"] <= p["flagged"] / p["n"]


# ---- regression tests, one per v2 fix ----
def rules(sql, **kw):
    return {f.rule for f in lint(sql, NS(grain=kw.get("grain"), metric=kw.get("metric")))}


def test_fix_deleted_at_is_null_is_a_soft_delete_predicate():
    assert "G01" not in rules("SELECT count(*) FROM fact_orders WHERE deleted_at IS NULL")
    assert "G01" in rules("SELECT count(*) FROM fact_orders WHERE deleted_at IS NOT NULL")


def test_fix_ilike_substring_is_a_valid_status_idiom():
    base = "SELECT count(*) FROM fact_orders WHERE NOT is_deleted AND "
    assert "G03" not in rules(base + "order_status ILIKE '%shipped%'")
    assert "G03" in rules(base + "order_status ILIKE 'shipped'")          # exact ILIKE still misses padded values


def test_fix_pk_restricted_child_does_not_fan_out():
    ok = "SELECT count(*) FROM fact_reviews r JOIN fact_orders o USING(order_id) JOIN fact_payments p ON p.order_id=o.order_id AND p.payment_sequential = 1 WHERE NOT o.is_deleted"
    bad = ok.replace(" AND p.payment_sequential = 1", "")
    assert "G04" not in rules(ok) and "G04" in rules(bad)


def test_fix_fanout_hidden_in_cte_and_derived_table_is_found():
    cte = """WITH x AS (SELECT i.price FROM fact_orders o JOIN fact_order_items i ON i.order_id=o.order_id
                        JOIN fact_payments p ON p.order_id=o.order_id WHERE NOT o.is_deleted)
             SELECT sum(price) FROM x"""
    derived = """SELECT avg(t) FROM (SELECT o.order_payment_total t FROM fact_orders o
                 JOIN fact_order_items i ON i.order_id=o.order_id WHERE NOT o.is_deleted) z"""
    assert "G04" in rules(cte) and "G04" in rules(derived)


def test_fix_cte_that_aggregates_or_dedupes_is_not_flagged():
    agg = """WITH a AS (SELECT order_id, sum(price) p FROM fact_order_items GROUP BY 1)
             SELECT sum(a.p) FROM fact_orders o JOIN a USING(order_id) WHERE NOT o.is_deleted"""
    dedupe = """SELECT sum(o.order_payment_total) FROM fact_orders o
                JOIN (SELECT DISTINCT order_id FROM fact_order_items) i ON i.order_id=o.order_id WHERE NOT o.is_deleted"""
    assert "G04" not in rules(agg) and "G04" not in rules(dedupe)


def test_fix_grouping_by_customer_key_is_grouping_by_order():
    sql = "SELECT count(*) FROM fact_orders o JOIN dim_customer c ON c.customer_key=o.customer_key WHERE NOT o.is_deleted AND NOT c.is_deleted GROUP BY o.customer_key"
    assert "G08" in rules(sql)


def test_fix_metric_denominator_and_status_set():
    freight = "SELECT 100.0*sum(i.freight_value)/(sum(i.price)+sum(i.freight_value)) FROM fact_orders o JOIN fact_order_items i USING(order_id) WHERE NOT o.is_deleted AND lower(trim(o.order_status))='delivered'"
    assert "M01" in rules(freight, metric="freight_ratio")
    extra = "SELECT sum(i.price) FROM fact_orders o JOIN fact_order_items i USING(order_id) WHERE NOT o.is_deleted AND lower(trim(o.order_status)) IN ('delivered','shipped')"
    assert "M01" in rules(extra, metric="net_revenue")
    exact = extra.replace("IN ('delivered','shipped')", "= 'delivered'")
    assert "M01" not in rules(exact, metric="net_revenue")


def test_per_order_aggregate_joined_to_sibling_child_is_still_fanout():
    """A pre-aggregated item total re-joined to the multi-row payments table repeats per payment row."""
    sql = """WITH a AS (SELECT order_id, sum(price) p FROM fact_order_items GROUP BY 1)
             SELECT sum(a.p) FROM fact_orders o JOIN a USING(order_id)
             JOIN fact_payments p2 ON p2.order_id=o.order_id WHERE NOT o.is_deleted"""
    assert "G04" in rules(sql)
