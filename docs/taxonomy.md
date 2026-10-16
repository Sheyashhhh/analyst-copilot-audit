# Failure taxonomy

Eleven classes. Eight describe wrong answers that a dashboard would happily display ("silent"), three are loud engine-level failures. The silent classes are the product: they are the reason unsupervised text-to-SQL is risky.

| Code | Silent | Owning layer | One-line cause |
|---|:-:|---|---|
| `FANOUT_INFLATION` | yes | modelling | one-to-many or sibling join repeats rows before aggregation |
| `SOFT_DELETE_IGNORED` | yes | data contract | `is_deleted` rows counted |
| `DEFINITION_DRIFT` | yes | semantic layer | a reasonable but non-canonical metric definition |
| `ENTITY_GRAIN_ERROR` | yes | modelling | `customer_id` (per order) treated as the person |
| `DATE_BOUNDARY_ERROR` | yes | SQL craft | date-only end bound on a TIMESTAMP drops the last day |
| `TIMEZONE_MISMATCH` | yes | data contract | UTC-naive value used as local time |
| `STATUS_CASE_MISMATCH` | yes | data quality | dirty categorical compared raw |
| `UNATTRIBUTED_LOGIC_ERROR` | yes | model | wrong, and no known signature explains it |
| `SCHEMA_HALLUCINATION` | no | model | unknown column or table; the engine rejects it |
| `SYNTAX_OR_RUNTIME_ERROR` | no | model | does not parse or fails at runtime |
| `BLOCKED_UNSAFE_OR_TIMEOUT` | no | platform | non-SELECT, forbidden function, or time limit |

Each class below shows the gold query beside the single-defect trap query from the question bank, plus what the trap does to the real number.

## FANOUT_INFLATION (FO05)

*"What is the total freight value on orders that were paid at least partly with a voucher?"*

```sql
-- gold: 77,769.55
SELECT sum(i.freight_value)
FROM fact_order_items i JOIN fact_orders o ON o.order_id = i.order_id
WHERE NOT o.is_deleted
  AND EXISTS (SELECT 1 FROM fact_payments p
              WHERE p.order_id = o.order_id AND p.payment_type = 'voucher');

-- trap: 122,424.78 (+57.4%)  items x payments is many-to-many through order_id
SELECT sum(i.freight_value)
FROM fact_order_items i
JOIN fact_orders o   ON o.order_id = i.order_id
JOIN fact_payments p ON p.order_id = o.order_id
WHERE NOT o.is_deleted AND p.payment_type = 'voucher';
```

Cure: use `EXISTS`/`IN` for filters, or pre-aggregate each child fact to the order grain in its own CTE.

## SOFT_DELETE_IGNORED (SD01)

Gold counts 96,989 orders; omitting `NOT is_deleted` returns 99,441 (+2.5%). Small enough to pass a sanity check, which is what makes it dangerous.

## DEFINITION_DRIFT (DF01, DF05, DF08)

"Revenue in 2017": item price on delivered orders = 5,817,340.96. Summing `payment_value` for the same orders = 6,753,471.36 (+16.1%), because payments include freight and instalment interest. Both are defensible; only one is the company's definition.

"Active customers": gold 17,537. A query anchored on `CURRENT_DATE` returns **0**, because the snapshot ends in August 2018.

## ENTITY_GRAIN_ERROR (EN03)

Repeat customers: gold 2,840; grouping by `customer_id` returns **0**, because Olist assigns a new `customer_id` to every order. A confident, plausible, completely wrong answer.

## DATE_BOUNDARY_ERROR (DB02)

`order_purchase_ts = DATE '2017-11-24'` returns 0 orders for Black Friday; the correct half-open range returns 1,148.

## TIMEZONE_MISMATCH (TZ02)

The busiest local hour for answering reviews is 23:00 (São Paulo). Bucketing the UTC value says 02:00.

## STATUS_CASE_MISMATCH (ST01)

`order_status = 'delivered'` finds 75,123 orders; `lower(trim(order_status)) = 'delivered'` finds 94,093 (-20.2% under the injected noise rate).

## How to extend the taxonomy

1. Run the evaluation; open the `UNATTRIBUTED_LOGIC_ERROR` rows in `predictions.jsonl`.
2. If several share a cause, add a rule `Gxx` in `lint.py`, a class in `taxonomy.py`, a question family with gold and trap queries, and the corresponding tests.
3. `make test` will insist the new trap query really differs from gold and is attributed to exactly one class.
