"""Build the star schema in DuckDB from the raw Olist CSVs and overlay the audit traps.

Real data, synthetic traps
--------------------------
The Olist export is genuinely messy (per-order customer ids, one-to-many reviews,
naive timestamps, unavailable orders with no items). It is also too clean in one way:
it has no soft deletes and its status column is already normalised. Those two traps,
plus a mixed timezone convention, are overlaid *deterministically* (seeded hash of the
business key), so every build is byte-for-byte reproducible and every overlay is
documented in ``data/trap_manifest.json``.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from .paths import DB_PATH, RAW_DIR, SCHEMA_SQL, ROOT

SEED = 20260101
ORDER_SOFT_DELETE_RATE = 0.025
CUSTOMER_ERASURE_RATE = 0.015
STATUS_NOISE_RATE = 0.20
LOCAL_TZ = "America/Sao_Paulo"

REQUIRED_FILES = [
    "olist_customers_dataset.csv",
    "olist_orders_dataset.csv",
    "olist_order_items_dataset.csv",
    "olist_order_payments_dataset.csv",
    "olist_order_reviews_dataset.csv",
    "olist_products_dataset.csv",
    "olist_sellers_dataset.csv",
    "product_category_name_translation.csv",
]


def _u(key: str, salt: str) -> float:
    """Deterministic uniform(0,1) draw from a business key."""
    digest = hashlib.md5(f"{SEED}:{salt}:{key}".encode()).hexdigest()
    return int(digest[:12], 16) / float(16**12)


def _read(name: str, raw_dir: Path) -> pd.DataFrame:
    df = pd.read_csv(raw_dir / name, dtype=str, encoding="utf-8-sig", keep_default_na=False, na_values=[""])
    df.columns = [c.strip().lower() for c in df.columns]
    return df


def _parse_ts(s: pd.Series) -> pd.Series:
    """Parse either ISO ('2017-10-02 10:56:33', Kaggle original) or 'dd-mm-yyyy HH:MM' (some mirrors)."""
    s = s.astype("string")
    iso = s.str.match(r"^\d{4}-\d{2}-\d{2}", na=False)
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    if iso.any():
        out[iso] = pd.to_datetime(s[iso], format="mixed")
    dmy = s.notna() & ~iso
    if dmy.any():
        out[dmy] = pd.to_datetime(s[dmy], format="%d-%m-%Y %H:%M", errors="coerce")
    return out


def _status_variant(order_id: str, status: str) -> str:
    if _u(order_id, "status_noise") >= STATUS_NOISE_RATE:
        return status
    pick = int(_u(order_id, "status_kind") * 5)
    return [status.upper(), status.capitalize(), f"{status} ", f" {status}", status.title().upper()[:1] + status[1:].upper()][pick]


def build(raw_dir: Path = RAW_DIR, db_path: Path = DB_PATH, verbose: bool = True) -> dict:
    missing = [f for f in REQUIRED_FILES if not (raw_dir / f).exists()]
    if missing:
        raise FileNotFoundError(f"Missing raw files in {raw_dir}: {missing}. Run `make data`.")

    log = print if verbose else (lambda *a, **k: None)
    if db_path.exists():
        db_path.unlink()
    con = duckdb.connect(str(db_path))
    con.execute(SCHEMA_SQL.read_text(encoding="utf-8"))

    # ---------------- dim_customer ----------------
    cust = _read("olist_customers_dataset.csv", raw_dir)
    cust["customer_key"] = np.arange(1, len(cust) + 1)
    erased = {u for u in cust.customer_unique_id.unique() if _u(u, "erasure") < CUSTOMER_ERASURE_RATE}
    cust["is_deleted"] = cust.customer_unique_id.isin(erased)
    dim_customer = cust.rename(columns={"customer_zip_code_prefix": "zip_code_prefix",
                                        "customer_city": "city", "customer_state": "state"})[
        ["customer_key", "customer_id", "customer_unique_id", "zip_code_prefix", "city", "state", "is_deleted"]]
    con.register("v", dim_customer); con.execute("INSERT INTO dim_customer SELECT * FROM v"); con.unregister("v")

    # ---------------- dim_product (+ category translation) ----------------
    prod = _read("olist_products_dataset.csv", raw_dir)
    tr = _read("product_category_name_translation.csv", raw_dir)
    tmap = dict(zip(tr.product_category_name, tr.product_category_name_english))
    prod["product_key"] = np.arange(1, len(prod) + 1)
    # Works for both the Kaggle original (Portuguese names) and mirrors that already translated them.
    prod["category_name"] = prod.product_category_name.map(lambda c: tmap.get(c, c) if isinstance(c, str) else None)
    prod = prod.rename(columns={"product_name_lenght": "name_length", "product_description_lenght": "description_length",
                                "product_photos_qty": "photos_qty", "product_weight_g": "weight_g",
                                "product_length_cm": "length_cm", "product_height_cm": "height_cm",
                                "product_width_cm": "width_cm"})
    cols = ["product_key", "product_id", "category_name", "name_length", "description_length", "photos_qty",
            "weight_g", "length_cm", "height_cm", "width_cm"]
    for c in cols[3:]:
        prod[c] = pd.to_numeric(prod[c], errors="coerce").astype("Int64")
    con.register("v", prod[cols]); con.execute("INSERT INTO dim_product SELECT * FROM v"); con.unregister("v")

    # ---------------- dim_seller ----------------
    sel = _read("olist_sellers_dataset.csv", raw_dir)
    sel["seller_key"] = np.arange(1, len(sel) + 1)
    sel = sel.rename(columns={"seller_zip_code_prefix": "zip_code_prefix", "seller_city": "city", "seller_state": "state"})
    con.register("v", sel[["seller_key", "seller_id", "zip_code_prefix", "city", "state"]])
    con.execute("INSERT INTO dim_seller SELECT * FROM v"); con.unregister("v")

    # ---------------- fact_order_items ----------------
    items = _read("olist_order_items_dataset.csv", raw_dir)
    items["shipping_limit_ts"] = _parse_ts(items.shipping_limit_date)
    items["order_item_id"] = items.order_item_id.astype(int)
    items["price"] = items.price.astype(float).round(2)
    items["freight_value"] = items.freight_value.astype(float).round(2)
    items = items.merge(prod[["product_id", "product_key"]], on="product_id", how="left") \
                 .merge(sel[["seller_id", "seller_key"]], on="seller_id", how="left")
    assert items.product_key.notna().all() and items.seller_key.notna().all(), "orphan item dimension keys"
    fi = items[["order_id", "order_item_id", "product_key", "seller_key", "shipping_limit_ts", "price", "freight_value"]]
    con.register("v", fi); con.execute("INSERT INTO fact_order_items SELECT * FROM v"); con.unregister("v")

    # ---------------- fact_payments ----------------
    pay = _read("olist_order_payments_dataset.csv", raw_dir)
    pay["payment_sequential"] = pay.payment_sequential.astype(int)
    pay["payment_installments"] = pay.payment_installments.astype(int)
    pay["payment_value"] = pay.payment_value.astype(float).round(2)
    con.register("v", pay[["order_id", "payment_sequential", "payment_type", "payment_installments", "payment_value"]])
    con.execute("INSERT INTO fact_payments SELECT * FROM v"); con.unregister("v")

    # ---------------- fact_orders (with traps) ----------------
    o = _read("olist_orders_dataset.csv", raw_dir)
    for c in ["order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date",
              "order_delivered_customer_date", "order_estimated_delivery_date"]:
        o[c] = _parse_ts(o[c])
    o = o.merge(cust[["customer_id", "customer_key"]], on="customer_id", how="left")
    assert o.customer_key.notna().all(), "orphan orders"
    agg_i = fi.groupby("order_id").size().rename("item_count")
    agg_p = pay.groupby("order_id").payment_value.sum().round(2).rename("order_payment_total")
    o = o.merge(agg_i, on="order_id", how="left").merge(agg_p, on="order_id", how="left")
    o["item_count"] = o.item_count.fillna(0).astype(int)
    o["order_payment_total"] = o.order_payment_total.fillna(0.0)

    o["is_deleted"] = o.order_id.map(lambda k: _u(k, "soft_delete") < ORDER_SOFT_DELETE_RATE)
    hours = o.order_id.map(lambda k: 1 + int(_u(k, "delete_lag") * 24 * 20))
    o["deleted_at"] = np.where(o.is_deleted, o.order_purchase_timestamp + pd.to_timedelta(hours, unit="h"), pd.NaT)
    o["deleted_at"] = pd.to_datetime(o["deleted_at"])
    canonical = o.order_status.copy()
    o["order_status"] = [_status_variant(i, s) for i, s in zip(o.order_id, canonical)]

    fo = o.rename(columns={"order_purchase_timestamp": "order_purchase_ts", "order_approved_at": "order_approved_ts",
                           "order_delivered_carrier_date": "order_delivered_carrier_ts",
                           "order_delivered_customer_date": "order_delivered_customer_ts",
                           "order_estimated_delivery_date": "order_estimated_delivery_ts"})[
        ["order_id", "customer_key", "order_status", "order_purchase_ts", "order_approved_ts",
         "order_delivered_carrier_ts", "order_delivered_customer_ts", "order_estimated_delivery_ts",
         "item_count", "order_payment_total", "is_deleted", "deleted_at"]]
    con.register("v", fo); con.execute("INSERT INTO fact_orders SELECT * FROM v"); con.unregister("v")

    # ---------------- fact_reviews (UTC-naive: a different convention from fact_orders) ----------------
    r = _read("olist_order_reviews_dataset.csv", raw_dir)
    r = r.drop_duplicates(["review_id", "order_id", "review_score", "review_creation_date", "review_answer_timestamp"])
    created = _parse_ts(r.review_creation_date)
    answered_local = _parse_ts(r.review_answer_timestamp)
    answered_utc = (answered_local.dt.tz_localize(LOCAL_TZ, ambiguous=np.ones(len(r), dtype=bool),
                                                  nonexistent="shift_forward")
                    .dt.tz_convert("UTC").dt.tz_localize(None))
    fr = pd.DataFrame({"review_id": r.review_id, "order_id": r.order_id,
                       "review_score": r.review_score.astype(int),
                       "review_created_date": created.dt.date, "review_answered_at": answered_utc})
    con.register("v", fr); con.execute("INSERT INTO fact_reviews SELECT * FROM v"); con.unregister("v")

    # ---------------- dim_date ----------------
    con.execute("""
        INSERT INTO dim_date
        SELECT d::DATE, year(d), quarter(d), month(d), monthname(d), isodow(d), isodow(d) >= 6
        FROM generate_series(DATE '2016-01-01', DATE '2018-12-31', INTERVAL 1 DAY) t(d)
    """)

    manifest = _manifest(con)
    (ROOT / "data" / "trap_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    con.execute("CHECKPOINT")
    con.close()
    log("warehouse built:", db_path)
    for k, v in manifest["row_counts"].items():
        log(f"  {k:<18}{v:>10,}")
    return manifest


def _manifest(con: duckdb.DuckDBPyConnection) -> dict:
    q = lambda s: con.execute(s).fetchone()[0]
    tables = ["dim_date", "dim_customer", "dim_product", "dim_seller", "fact_orders",
              "fact_order_items", "fact_payments", "fact_reviews"]
    return {
        "seed": SEED,
        "row_counts": {t: q(f"SELECT count(*) FROM {t}") for t in tables},
        "traps": {
            "soft_delete_orders": q("SELECT count(*) FROM fact_orders WHERE is_deleted"),
            "soft_delete_customers_rows": q("SELECT count(*) FROM dim_customer WHERE is_deleted"),
            "status_raw_distinct": q("SELECT count(DISTINCT order_status) FROM fact_orders"),
            "status_canonical_distinct": q("SELECT count(DISTINCT lower(trim(order_status))) FROM fact_orders"),
            "orders_with_multiple_items": q("SELECT count(*) FROM fact_orders WHERE item_count > 1"),
            "orders_with_multiple_payments": q("SELECT count(*) FROM (SELECT order_id FROM fact_payments GROUP BY 1 HAVING count(*) > 1)"),
            "orders_with_multiple_reviews": q("SELECT count(*) FROM (SELECT order_id FROM fact_reviews GROUP BY 1 HAVING count(*) > 1)"),
            "customer_ids_vs_unique_people": [q("SELECT count(DISTINCT customer_id) FROM dim_customer"),
                                               q("SELECT count(DISTINCT customer_unique_id) FROM dim_customer")],
        },
        "fingerprint": {
            "orders": q("SELECT count(*) FROM fact_orders"),
            "items_price_sum": float(q("SELECT sum(price) FROM fact_order_items")),
            "payments_sum": float(q("SELECT sum(payment_value) FROM fact_payments")),
        },
    }


if __name__ == "__main__":
    build()
