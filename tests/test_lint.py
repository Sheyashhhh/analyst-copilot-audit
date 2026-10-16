from types import SimpleNamespace as NS

import pytest

from copilot_audit import taxonomy as T
from copilot_audit.lint import BLOCK, REVIEW, decide, lint


def rules(sql, **kw):
    return {f.rule for f in lint(sql, NS(grain=kw.get("grain"), metric=kw.get("metric")))}


def test_g01_soft_delete():
    assert "G01" in rules("SELECT count(*) FROM fact_orders")
    assert "G01" in rules("SELECT sum(price) FROM fact_order_items")
    assert "G01" not in rules("SELECT count(*) FROM fact_orders WHERE NOT is_deleted")
    assert "G01" not in rules("SELECT count(*) FROM fact_orders o WHERE o.is_deleted = FALSE")


def test_g01_not_triggered_by_dimension_only_queries():
    assert rules("SELECT count(*) FROM dim_seller") == set()


def test_g02_customer_erasure_only_for_customer_metrics():
    assert "G02" in rules("SELECT count(DISTINCT customer_unique_id) FROM dim_customer")
    assert "G02" not in rules("SELECT count(DISTINCT customer_unique_id) FROM dim_customer WHERE NOT is_deleted")
    # revenue by state is a financial fact: erased customers keep their orders
    assert "G02" not in rules("SELECT c.state, count(*) FROM fact_orders o JOIN dim_customer c ON c.customer_key=o.customer_key WHERE NOT o.is_deleted GROUP BY 1")


@pytest.mark.parametrize("expr,flag", [
    ("order_status = 'delivered'", True), ("lower(order_status) = 'delivered'", True),
    ("lower(trim(order_status)) = 'delivered'", False), ("trim(lower(order_status)) = 'delivered'", False),
    ("upper(trim(order_status)) = 'DELIVERED'", True)])
def test_g03_status(expr, flag):
    assert ("G03" in rules(f"SELECT count(*) FROM fact_orders WHERE NOT is_deleted AND {expr}")) is flag


def test_g04_fanout_detection_and_cures():
    bad = """SELECT sum(o.order_payment_total) FROM fact_orders o JOIN fact_order_items i ON i.order_id=o.order_id WHERE NOT o.is_deleted"""
    assert "G04" in rules(bad)
    sibling = """SELECT sum(i.price), sum(p.payment_value) FROM fact_orders o JOIN fact_order_items i USING(order_id)
                 JOIN fact_payments p USING(order_id) WHERE NOT o.is_deleted"""
    assert "G04" in rules(sibling)
    ok_distinct = """SELECT count(DISTINCT o.order_id) FROM fact_orders o JOIN fact_order_items i USING(order_id) WHERE NOT o.is_deleted"""
    assert "G04" not in rules(ok_distinct)
    ok_item_measure = """SELECT sum(i.price) FROM fact_orders o JOIN fact_order_items i USING(order_id) WHERE NOT o.is_deleted"""
    assert "G04" not in rules(ok_item_measure)
    ok_cte = """WITH a AS (SELECT order_id, sum(price) p FROM fact_order_items GROUP BY 1),
                     b AS (SELECT order_id, sum(payment_value) v FROM fact_payments GROUP BY 1)
                SELECT sum(a.p), sum(b.v) FROM fact_orders o JOIN a USING(order_id) JOIN b USING(order_id) WHERE NOT o.is_deleted"""
    assert "G04" not in rules(ok_cte)


def test_g10_needs_question_grain():
    sql = "SELECT count(*) FROM fact_orders o JOIN fact_order_items i USING(order_id) WHERE NOT o.is_deleted"
    assert "G10" not in rules(sql)
    assert "G10" in rules(sql, grain="order")


def test_g05_relative_dates():
    for fn in ["CURRENT_DATE", "now()", "CURRENT_TIMESTAMP"]:
        assert "G05" in rules(f"SELECT count(*) FROM fact_orders WHERE NOT is_deleted AND order_purchase_ts > {fn} - INTERVAL 30 DAY")


@pytest.mark.parametrize("cond,flag", [
    ("order_purchase_ts BETWEEN '2018-01-01' AND '2018-03-31'", True),
    ("order_purchase_ts <= '2018-03-31'", True), ("order_purchase_ts = DATE '2018-03-31'", True),
    ("order_purchase_ts >= '2018-01-01' AND order_purchase_ts < '2018-04-01'", False),
    ("order_purchase_ts::DATE BETWEEN DATE '2018-01-01' AND DATE '2018-03-31'", False),
    ("order_purchase_ts <= TIMESTAMP '2018-03-31 23:59:59'", False)])
def test_g06_boundaries(cond, flag):
    assert ("G06" in rules(f"SELECT count(*) FROM fact_orders WHERE NOT is_deleted AND {cond}")) is flag


def test_g07_timezone():
    base = "SELECT count(*) FROM fact_reviews r JOIN fact_orders o USING(order_id) WHERE NOT o.is_deleted AND "
    assert "G07" in rules(base + "r.review_answered_at::DATE = DATE '2018-03-05'")
    assert "G07" not in rules(base + "(r.review_answered_at AT TIME ZONE 'UTC') AT TIME ZONE 'America/Sao_Paulo' >= TIMESTAMP '2018-03-05'")
    assert "G07" not in rules(base + "timezone('America/Sao_Paulo', timezone('UTC', r.review_answered_at))::DATE = DATE '2018-03-05'")


def test_g08_entity_grain_is_review_not_block():
    f = lint("SELECT count(DISTINCT c.customer_id) FROM fact_orders o JOIN dim_customer c ON c.customer_key=o.customer_key WHERE NOT o.is_deleted AND NOT c.is_deleted")
    assert any(x.rule == "G08" for x in f)
    assert decide(f) == REVIEW


def test_join_keys_do_not_trigger_entity_rule():
    sql = "SELECT count(DISTINCT c.customer_unique_id) FROM fact_orders o JOIN dim_customer c ON c.customer_key=o.customer_key WHERE NOT o.is_deleted AND NOT c.is_deleted"
    assert "G08" not in rules(sql)


def test_metric_conformance():
    wrong = "SELECT sum(p.payment_value) FROM fact_orders o JOIN fact_payments p USING(order_id) WHERE NOT o.is_deleted AND lower(trim(o.order_status))='delivered'"
    assert "M01" in rules(wrong, metric="net_revenue")
    right = "SELECT sum(i.price) FROM fact_orders o JOIN fact_order_items i USING(order_id) WHERE NOT o.is_deleted AND lower(trim(o.order_status))='delivered'"
    assert "M01" not in rules(right, metric="net_revenue")


def test_decision_ladder():
    assert decide([]) == "ALLOW"
    assert decide(lint("SELECT count(*) FROM fact_orders")) == BLOCK
    assert decide(lint("SELECT sum(price) FROM fact_order_items i JOIN fact_orders o USING(order_id) WHERE NOT o.is_deleted")) == REVIEW


def test_unparseable_sql_yields_no_findings():
    assert lint("SELEC nonsense (") == []


def test_every_finding_maps_to_a_taxonomy_class():
    for f in lint("SELECT count(*) FROM fact_orders WHERE order_status='x' AND order_purchase_ts <= '2018-01-01'"):
        assert f.taxonomy in T.CLASSES
