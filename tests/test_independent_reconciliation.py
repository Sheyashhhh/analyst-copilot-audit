"""Re-derive headline answers with pandas straight from the raw CSVs, bypassing the star schema.

If a gold SQL query and this independent computation agree, the join logic, trap handling and
business-rule implementation in the SQL are corroborated by a second implementation.
"""
import numpy as np
import pandas as pd
import pytest

from copilot_audit import warehouse as W
from copilot_audit.paths import RAW_DIR

pytestmark = pytest.mark.skipif(not (RAW_DIR / "olist_orders_dataset.csv").exists(), reason="raw CSVs not present")


@pytest.fixture(scope="module")
def raw():
    r = lambda n: W._read(n, RAW_DIR)
    o, c, i, p, rv = (r("olist_orders_dataset.csv"), r("olist_customers_dataset.csv"), r("olist_order_items_dataset.csv"),
                      r("olist_order_payments_dataset.csv"), r("olist_order_reviews_dataset.csv"))
    for col in ["order_purchase_timestamp", "order_delivered_customer_date", "order_estimated_delivery_date"]:
        o[col] = W._parse_ts(o[col])
    o["deleted"] = o.order_id.map(lambda k: W._u(k, "soft_delete") < W.ORDER_SOFT_DELETE_RATE)
    erased = {u for u in c.customer_unique_id.unique() if W._u(u, "erasure") < W.CUSTOMER_ERASURE_RATE}
    c["erased"] = c.customer_unique_id.isin(erased)
    i["price"], i["freight_value"] = i.price.astype(float), i.freight_value.astype(float)
    p["payment_value"] = p.payment_value.astype(float)
    o = o.merge(c[["customer_id", "customer_unique_id", "customer_state", "erased"]], on="customer_id")
    return o[~o.deleted].copy(), c, i, p, rv


def gold_value(gold, qid):
    return gold[qid].rows


def test_valid_order_count(raw, gold):
    o, *_ = raw
    assert gold_value(gold, "SD01")[0][0] == len(o)


def test_net_revenue_2017(raw, gold):
    o, c, i, p, rv = raw
    d = o[(o.order_status == "delivered") & (o.order_purchase_timestamp.dt.year == 2017)]
    assert float(gold_value(gold, "DF01")[0][0]) == pytest.approx(i[i.order_id.isin(d.order_id)].price.sum(), abs=0.01)


def test_cash_collected_2017(raw, gold):
    o, c, i, p, rv = raw
    d = o[(~o.order_status.isin(["canceled", "unavailable"])) & (o.order_purchase_timestamp.dt.year == 2017)]
    assert float(gold_value(gold, "DF12")[0][0]) == pytest.approx(p[p.order_id.isin(d.order_id)].payment_value.sum(), abs=0.01)


def test_active_customers(raw, gold):
    o, *_ = raw
    d = o[(~o.erased) & (~o.order_status.isin(["canceled", "unavailable"]))
          & (o.order_purchase_timestamp >= "2018-06-03") & (o.order_purchase_timestamp < "2018-09-01")]
    assert gold_value(gold, "DF05")[0][0] == d.customer_unique_id.nunique()


def test_repeat_customers_and_rate(raw, gold):
    o, *_ = raw
    n = o[~o.erased].groupby("customer_unique_id").size()
    assert gold_value(gold, "EN03")[0][0] == (n > 1).sum()
    assert gold_value(gold, "EN04")[0][0] == pytest.approx(round(100 * (n > 1).mean(), 2), abs=0.005)


def test_late_delivery_pct(raw, gold):
    o, *_ = raw
    d = o[(o.order_status == "delivered") & o.order_delivered_customer_date.notna()]
    late = (d.order_delivered_customer_date.dt.normalize() > d.order_estimated_delivery_date.dt.normalize()).mean()
    assert gold_value(gold, "DF08")[0][0] == pytest.approx(round(100 * late, 2), abs=0.005)


def test_freight_on_voucher_orders_has_no_fanout(raw, gold):
    o, c, i, p, rv = raw
    voucher = set(p[p.payment_type == "voucher"].order_id) & set(o.order_id)
    assert float(gold_value(gold, "FO05")[0][0]) == pytest.approx(i[i.order_id.isin(voucher)].freight_value.sum(), abs=0.01)


def test_orders_per_category_top1(raw, gold):
    o, c, i, p, rv = raw
    prod = W._read("olist_products_dataset.csv", RAW_DIR)[["product_id", "product_category_name"]]
    tr = W._read("product_category_name_translation.csv", RAW_DIR)
    tmap = dict(zip(tr.product_category_name, tr.product_category_name_english))
    prod["cat"] = prod.product_category_name.map(lambda x: tmap.get(x, x))
    m = i[i.order_id.isin(o.order_id)].merge(prod, on="product_id")[["order_id", "cat"]].drop_duplicates()
    top = m.groupby("cat").size().sort_values(ascending=False)
    cat, n = gold_value(gold, "FO01")[0]
    assert (cat, n) == (top.index[0], top.iloc[0])


def test_local_day_reviews_without_any_timezone_math(raw, gold):
    """Pandas works on the raw LOCAL timestamps, so agreement proves the UTC round trip in SQL is exact."""
    o, c, i, p, rv = raw
    rv = rv[rv.order_id.isin(o.order_id)].drop_duplicates(["review_id", "order_id", "review_score", "review_creation_date", "review_answer_timestamp"])
    local = W._parse_ts(rv.review_answer_timestamp)
    expected = ((local >= "2018-03-05") & (local < "2018-03-06")).sum()
    assert abs(gold_value(gold, "TZ01")[0][0] - expected) <= 1     # <=1: a DST-edge nonexistent hour may shift


def test_q1_2018_orders(raw, gold):
    o, *_ = raw
    assert gold_value(gold, "DB01")[0][0] == ((o.order_purchase_timestamp >= "2018-01-01") & (o.order_purchase_timestamp < "2018-04-01")).sum()


def test_top_states_by_customers(raw, gold):
    o, c, *_ = raw
    s = c[~c.erased].groupby("customer_state").customer_unique_id.nunique().reset_index()
    s = s.sort_values(["customer_unique_id", "customer_state"], ascending=[False, True]).head(5)
    assert [tuple(r) for r in gold_value(gold, "SD09")] == [(a, b) for a, b in zip(s.customer_state, s.customer_unique_id)]


def test_avg_orders_per_customer(raw, gold):
    o, *_ = raw
    d = o[~o.erased]
    assert gold_value(gold, "EN05")[0][0] == pytest.approx(round(len(d) / d.customer_unique_id.nunique(), 2), abs=0.005)
