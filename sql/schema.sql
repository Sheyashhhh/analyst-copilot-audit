-- Star schema for the Olist marketplace warehouse.
-- Money is DECIMAL (exact); event times are deliberately timezone-naive TIMESTAMPs.

CREATE TABLE dim_date (
    date_key       DATE PRIMARY KEY,
    year           INTEGER,
    quarter        INTEGER,
    month          INTEGER,
    month_name     VARCHAR,
    day_of_week    INTEGER,
    is_weekend     BOOLEAN
);

CREATE TABLE dim_customer (
    customer_key        INTEGER PRIMARY KEY,
    customer_id         VARCHAR NOT NULL,
    customer_unique_id  VARCHAR NOT NULL,
    zip_code_prefix     VARCHAR,
    city                VARCHAR,
    state               VARCHAR,
    is_deleted          BOOLEAN NOT NULL
);

CREATE TABLE dim_product (
    product_key        INTEGER PRIMARY KEY,
    product_id         VARCHAR NOT NULL,
    category_name      VARCHAR,
    name_length        INTEGER,
    description_length INTEGER,
    photos_qty         INTEGER,
    weight_g           INTEGER,
    length_cm          INTEGER,
    height_cm          INTEGER,
    width_cm           INTEGER
);

CREATE TABLE dim_seller (
    seller_key       INTEGER PRIMARY KEY,
    seller_id        VARCHAR NOT NULL,
    zip_code_prefix  VARCHAR,
    city             VARCHAR,
    state            VARCHAR
);

CREATE TABLE fact_orders (
    order_id                     VARCHAR PRIMARY KEY,
    customer_key                 INTEGER NOT NULL,
    order_status                 VARCHAR NOT NULL,
    order_purchase_ts            TIMESTAMP NOT NULL,
    order_approved_ts            TIMESTAMP,
    order_delivered_carrier_ts   TIMESTAMP,
    order_delivered_customer_ts  TIMESTAMP,
    order_estimated_delivery_ts  TIMESTAMP,
    item_count                   INTEGER NOT NULL,
    order_payment_total          DECIMAL(12,2) NOT NULL,
    is_deleted                   BOOLEAN NOT NULL,
    deleted_at                   TIMESTAMP
);

CREATE TABLE fact_order_items (
    order_id            VARCHAR NOT NULL,
    order_item_id       INTEGER NOT NULL,
    product_key         INTEGER NOT NULL,
    seller_key          INTEGER NOT NULL,
    shipping_limit_ts   TIMESTAMP,
    price               DECIMAL(10,2) NOT NULL,
    freight_value       DECIMAL(10,2) NOT NULL,
    PRIMARY KEY (order_id, order_item_id)
);

CREATE TABLE fact_payments (
    order_id              VARCHAR NOT NULL,
    payment_sequential    INTEGER NOT NULL,
    payment_type          VARCHAR NOT NULL,
    payment_installments  INTEGER NOT NULL,
    payment_value         DECIMAL(10,2) NOT NULL,
    PRIMARY KEY (order_id, payment_sequential)
);

CREATE TABLE fact_reviews (
    review_id            VARCHAR NOT NULL,
    order_id             VARCHAR NOT NULL,
    review_score         INTEGER NOT NULL,
    review_created_date  DATE,
    review_answered_at  TIMESTAMP
);
