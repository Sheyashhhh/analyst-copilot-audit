import json

from copilot_audit.paths import ROOT


def q(ex, sql):
    return ex.run(sql).rows


def test_row_counts_match_olist_public_totals(executor):
    assert q(executor, "SELECT count(*) FROM fact_orders")[0][0] == 99441
    assert q(executor, "SELECT count(*) FROM fact_order_items")[0][0] == 112650
    assert q(executor, "SELECT count(*) FROM fact_payments")[0][0] == 103886
    assert float(q(executor, "SELECT sum(price) FROM fact_order_items")[0][0]) == 13591643.70
    assert float(q(executor, "SELECT sum(payment_value) FROM fact_payments")[0][0]) == 16008872.12


def test_trap_soft_delete_present(executor):
    n = q(executor, "SELECT count(*) FROM fact_orders WHERE is_deleted")[0][0]
    assert 0.02 < n / 99441 < 0.03
    assert q(executor, "SELECT count(*) FROM dim_customer WHERE is_deleted")[0][0] > 0


def test_trap_status_casing_present(executor):
    raw = q(executor, "SELECT count(DISTINCT order_status) FROM fact_orders")[0][0]
    clean = q(executor, "SELECT count(DISTINCT lower(trim(order_status))) FROM fact_orders")[0][0]
    assert clean == 8 and raw > 3 * clean


def test_trap_fanout_cardinalities(executor):
    assert q(executor, "SELECT count(*) FROM fact_orders WHERE item_count > 1")[0][0] > 9000
    assert q(executor, "SELECT count(*) FROM (SELECT order_id FROM fact_payments GROUP BY 1 HAVING count(*)>1)")[0][0] > 2000
    assert q(executor, "SELECT count(*) FROM (SELECT order_id FROM fact_reviews GROUP BY 1 HAVING count(*)>1)")[0][0] > 400


def test_trap_customer_id_is_per_order(executor):
    ids, people = q(executor, "SELECT count(DISTINCT customer_id), count(DISTINCT customer_unique_id) FROM dim_customer")[0]
    assert ids == 99441 and people == 96096


def test_trap_timestamps_are_naive(executor):
    types = dict(q(executor, "SELECT column_name, data_type FROM information_schema.columns WHERE table_name='fact_orders'"))
    assert types["order_purchase_ts"] == "TIMESTAMP"          # no TZ
    assert q(executor, "SELECT data_type FROM information_schema.columns WHERE column_name='review_answered_at'")[0][0] == "TIMESTAMP"


def test_integrity(executor):
    assert q(executor, "SELECT count(*) FROM fact_order_items i LEFT JOIN fact_orders o USING(order_id) WHERE o.order_id IS NULL")[0][0] == 0
    assert q(executor, "SELECT count(*) FROM fact_orders WHERE item_count <> (SELECT count(*) FROM fact_order_items i WHERE i.order_id = fact_orders.order_id)")[0][0] == 0
    assert q(executor, "SELECT count(*) FROM fact_orders WHERE order_payment_total <> coalesce((SELECT sum(payment_value) FROM fact_payments p WHERE p.order_id = fact_orders.order_id),0)")[0][0] == 0


def test_manifest_written():
    m = json.loads((ROOT / "data" / "trap_manifest.json").read_text(encoding="utf-8"))
    assert m["seed"] == 20260101 and m["row_counts"]["fact_orders"] == 99441
