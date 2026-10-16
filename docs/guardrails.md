# Guardrail design and automation policy

## Layers

```
question ─► [1 context: semantic layer] ─► LLM ─► SQL
                                                    │
                       [2 static linter: ALLOW / REVIEW / BLOCK]
                                                    │
                       [3 read-only sandbox: SELECT-only, no file access, timeout, row cap]
                                                    │
                          result ─► [4 metric registry: governed answer for registered metrics]
                                                    │
                       [5 sampled human audit of ALLOW decisions, full review of REVIEW]
```

1. **Semantic layer** (`semantic/semantic_layer.yaml`): column meanings, warehouse rules, canonical metric SQL.
2. **Linter** (`copilot_audit/lint.py`): AST rules. BLOCK = known-wrong pattern, REVIEW = needs a human or a governed metric.
3. **Sandbox** (`copilot_audit/executor.py`): read-only database, single SELECT, external file access disabled and config locked, 30 s timeout, 10,000-row cap. Tested against DDL, DML, `COPY`, `read_csv('/etc/passwd')`, multi-statement input and a runaway cross join.
4. **Metric registry**: for registered metrics (net revenue, GMV, cash collected, AOV, active customers, late-delivery rate, freight ratio) the answer should come from the canonical SQL, not from generation.
5. **Human audit**: ALLOW is not "correct"; it means "no known hazard".

## Rules

| Rule | Severity | Evidence class | What it checks |
|---|---|---|---|
| G01 | BLOCK | soft delete | order facts read without `fact_orders.is_deleted` |
| G02 | BLOCK | soft delete | customer-level metric without `dim_customer.is_deleted` |
| G03 | BLOCK | status | `order_status` not wrapped in `lower(trim())` |
| G04 | BLOCK | fan-out | non-DISTINCT aggregate over a measure repeated by a joined sibling/child fact in the same SELECT |
| G05 | BLOCK | definition | `CURRENT_DATE`, `NOW()` etc. on a frozen snapshot |
| G06 | BLOCK | date boundary | `<=`, `>`, `=`, `BETWEEN` on a TIMESTAMP with a date-only literal |
| G07 | BLOCK | timezone | `review_answered_at` used without time-zone conversion |
| G08 | REVIEW | entity grain | `customer_id`/`customer_key` as the person in `COUNT(DISTINCT)` or `GROUP BY` |
| G09 | REVIEW | (advisory) | `SUM`/`AVG` over a money column: route through the metric registry |
| G10 | BLOCK | fan-out | question-aware: row count taken at a finer grain than the question asks for |
| M01 | BLOCK | definition | question-aware: SQL violates the registered metric definition |

G10 and M01 need the question's metadata, so they are used for attribution and for a registry-aware production guardrail, not for the question-agnostic linter.

## Rule changes made after the holdout (v2)

| Defect found by the holdout | Fix |
|---|---|
| Correct `deleted_at IS NULL` blocked by G01 | `deleted_at IS NULL` counts as a soft-delete predicate; `IS NOT NULL` (which selects the deleted rows) does not |
| Correct `ILIKE '%shipped%'` blocked by G03 | `LIKE`/`ILIKE` with `%...%` accepted; exact `ILIKE 'shipped'` still flagged (misses padded values) |
| Correct join pinned to `payment_sequential = 1` blocked by G04 | an equality on a primary-key component (`payment_sequential`, `order_item_id`) makes that child at most one row per order |
| Fan-out hidden in a CTE or derived table missed | G04 follows column lineage through row-preserving CTEs and subqueries; aggregating or `DISTINCT` subqueries reset the grain |
| `GROUP BY customer_key` missed by G08 | grouping by any per-order customer key is grouping by order |
| `sum(price) + sum(freight)` denominator and `IN ('delivered','shipped')` missed by M01 | metric conformance checks the denominator expression and that the status set is exactly `delivered` |

Each has a regression test in `tests/test_holdout.py`. While writing those tests a fix of mine turned out to be wrong (`deleted_at IS NOT NULL` parses as `NOT (deleted_at IS NULL)` and was wrongly accepted); the test caught it.

## Measured behaviour

**Development set** (written alongside the rules, so a regression suite, not an estimate of recall):

| Population | n | ALLOW | REVIEW | BLOCK |
|---|---:|---:|---:|---:|
| Gold queries | 80 | 60 | 20 | **0** |
| Control (no-trap) gold queries | 14 | **14** | 0 | 0 |
| Correct alternate formulations | 21 | 18 | 3 | **0** |
| Trap queries (question-agnostic) | 66 | 7 | 16 | 43 |

All 20 REVIEW decisions on gold are G09 (revenue-like aggregates, sent to the registry by policy). The 7 trap queries allowed are FO01, FO02, FO07, FO12 (row counts where the question wanted orders) and DF06, DF07, DF08 (a different metric definition); both need intent.

**Holdout** (16 questions, 32 wrong variants of which 11 evasive, 5 unusual correct variants; `python -m copilot_audit holdout`):

| Measure | v1 (frozen, `results/holdout_v1.json`) | v2 (after fixes; no longer held out) |
|---|---:|---:|
| Wrong variants flagged (detection) | 27 / 32 | 29 / 32 |
| Wrong variants blocked (prevention) | 18 / 32 | 20 / 32 |
| Evasive variants flagged / blocked | 6 / 11, 2 / 11 | 9 / 11, 5 / 11 |
| Correct variants blocked (false blocks) | 3 / 5 | 0 / 5 |
| Classifier agreement | 24 / 32 | 30 / 32 |

H03 variant 1 was mislabelled as fan-out in v1; it is wrong because of `item_count >= 3` and belongs to no taxonomy class. It is relabelled from v2 onward, and the v1 file is left as originally measured.

### Four separate measures

A single "guardrail success rate" hides trade-offs, so a run reports:

| Measure | Definition |
|---|---|
| Detection | wrong first-attempt answers flagged (BLOCK or REVIEW) / wrong first-attempt answers |
| Prevention | wrong first-attempt answers BLOCKed / wrong first-attempt answers |
| Recovery | blocked wrong answers that the retry turned into correct answers / blocked wrong answers |
| False blocks | correct first-attempt answers BLOCKed / correct first-attempt answers |

Plus **regressions**: correct answers the retry made wrong. Detection is not prevention (REVIEW lets a query through to a human), and prevention is not recovery (a blocked query still has to be fixed).

## Known blind spots

| Blind spot | Why | Remedy |
|---|---|---|
| Exclusive date ends (`< '2018-03-15'` for "1 to 15 March inclusive") | right or wrong depending on what the user meant | confirm date intent; probe rows at the boundary |
| Row counts at the wrong grain with no sibling join | depends on the question | question-aware G10 via the registry |
| Alternative metric definitions with correct syntax | a definition, not a defect | metric registry (M01 needs the question) |
| Fan-out through `SELECT *` CTEs | column lineage cannot be followed through `*` | expand stars in the generator, or review |
| Anything the rules have not been taught | static analysis proves absence of known patterns only | holdout monitoring, `UNATTRIBUTED_LOGIC_ERROR` backlog |

H08 and H09 (exclusive date ends) are asserted as still-missed by a test so the gap cannot be quietly forgotten.

## Automation policy

Per family, with the question as the unit (`report.policy()`):

| Tier | Rule |
|---|---|
| AUTOMATE with sampled audit | Wilson 95% lower bound >= 90% and zero silent failures |
| HUMAN REVIEW | accuracy >= 80%, or accuracy >= 95% with too few questions to certify (the report prints how many are needed) |
| BLOCK free-form SQL | accuracy below 80% |
| REGISTRY ONLY | metric-definition questions, always |

Certification arithmetic: 14 of 14 correct has a lower bound of 78%; about 35 questions at 100% are needed to reach 90%. Add questions to a family before promoting it.

Structural recommendations that follow from the trap measurements regardless of any model's score:

1. No unsupervised generation that joins two of `fact_order_items`, `fact_payments`, `fact_reviews` in one SELECT; expose pre-aggregated order-grain views.
2. Expose only filtered views (`valid_orders`, `active_customers`) so soft-delete and erasure predicates cannot be forgotten.
3. Rename `customer_id` to `order_customer_ref` and publish `customer_id` only as the person key.
4. Convert timestamps to `TIMESTAMPTZ` at ingestion; a column description is a weaker control than a type.
5. Normalise `order_status` at ingestion; a linter is a weaker control than clean data.
6. Registered metrics are answered from their canonical SQL; free-form generation is an exploration aid, not an answer source.
