# Methodology

## 1. What is measured

For every (question, condition) pair the harness produces one SQL query from the model and records:

| Quantity | How |
|---|---|
| **Execution accuracy** | The query runs and its result set is equivalent to the gold result set (rules below). |
| **Failure class** | `classify.py` attributes every non-matching query to one class of the taxonomy. |
| **Cost** | `input_tokens x price + output_tokens x price`, from the API usage object. Reasoning tokens are billed as output and are included. Prices live in `config/pricing.yaml`. |
| **Latency** | Wall-clock seconds for the model call(s), summed across guardrail retries. SQL execution time is recorded separately (`exec_ms`). |
| **Guardrail verdict** | ALLOW / REVIEW / BLOCK from the static linter on the first-attempt SQL, so its precision and recall can be measured against ground truth. |

## 2. Result-set equivalence

String comparison against reference SQL is not used. Two results are equivalent when:

1. **Columns.** Every gold column can be matched to a distinct prediction column holding the same values. Names and order are ignored, extra prediction columns are tolerated.
2. **Rows.** Rows agree as a multiset, or as an ordered sequence when the question specifies ordering. Ordered gold queries always carry deterministic tie-breakers, and the question text says how ties break.
3. **Numbers.** Compared to the precision of the gold answer: within half a unit of its last decimal place. `Decimal`, `int` and `float` are interchangeable. Gold queries round where the question says "round to N decimals", so a model that ignores the rounding instruction is marked wrong.
4. **Failures.** Execution errors, timeouts, truncated results and empty-versus-non-empty results never match.
5. **Multiplicity.** Duplicate rows count: a result with the same distinct rows but different repeat counts is different, so fan-out cannot be certified.
6. **Precision.** A fractional gold column is held to at least two decimals even if the gold value ends in zeros (0.10 does not accept 0.12). Integer columns are exact.
7. **Ties.** Ordered comparison is strict about tie order; every ordered question states its tie-break in the question text.

The comparator is covered by 19 unit tests, including adversarial cases (duplicate-row multiplicity, constant and identical columns, ambiguous column assignment resolved by search over row alignment, NaN vs NULL, empty results, decimal precision boundaries, tie order), and validated against 21 independently-written correct formulations (CTEs, `EXISTS`, `GROUP BY ALL`, `timezone()` function style, different date idioms), all of which it accepts.

## 3. Gold standard

Each of the 80 questions has a gold query and a hand-written single-defect "trap query". Validation, all enforced by `pytest`:

- Every gold query executes, returns rows, and is deterministic across runs.
- No gold query uses a relative-date function.
- **Every trap query returns a different result from gold**, so none of the 66 traps is a no-op.
- 21 alternate correct formulations agree with gold.
- **12 independent checks (13 headline answers) re-derive the gold results in pandas directly from the raw CSVs**, bypassing the star schema and the SQL entirely (`tests/test_independent_reconciliation.py`). Examples: net revenue 2017, active customers, repeat-customer count and rate, late-delivery rate, freight on voucher orders, and review counts for a local calendar day computed with *no* timezone arithmetic (which proves the UTC round trip in SQL is exact).
- The warehouse reconciles to Olist's published totals (99,441 orders, 112,650 items, items sum 13,591,643.70, payments sum 16,008,872.12).

Because the trap queries differ from gold in exactly one respect, the classifier can be tested against known causes (section 5).

## 4. Context conditions (semantic-layer ablation)

| ID | Prompt contains | Isolates |
|---|---|---|
| C0 | `CREATE TABLE` statements | The default text-to-SQL demo |
| C1 | + column descriptions, grain per table, timezone convention | Does documenting *what columns are* help? |
| C2 | + warehouse rules and defined metrics (canonical SQL) | Does documenting *what they mean here* help? |
| C3 | C2 + lint-and-retry: if the linter BLOCKs the first answer, its messages go back to the model once | What does a guardrail loop add, and at what cost in tokens and latency? |

The conditions are cumulative so each tier's marginal lift can be attributed. The same questions are used in every condition, so lifts are tested with an exact paired McNemar test on discordant questions.

Questions, not trials, are the unit of analysis: a question counts as correct only if it is correct in every trial. Lift is reported with an exact McNemar test on discordant questions and a paired bootstrap 95% interval on the difference. Uncertainty: Wilson 95% intervals on every accuracy. With 80 questions an interval is roughly +/-10 points, so the report shows intervals and never ranks conditions on point estimates alone. Use `--trials N` for repeated runs.

## 4b. Additional measures

- **Silent-error rate:** wrong answers that executed successfully / all answers.
- **Risk-weighted error:** wrong answers weighted 3x when the question involves money or a registered metric, 1x otherwise (`questions.risk_weight`). A wrong product count and a financial figure overstated by 50% should not weigh the same; adjust the heuristic to your own cost of error.
- **Cost and latency:** per question, with mean, median and p95 latency; C3 includes the retry call.
- **Stability:** with `--trials N`, per-trial accuracy range and the share of questions with identical outcomes in every trial.
- **Reproducibility metadata:** each run stores prompt hashes per condition, the question-set hash, model, max tokens, and Python/DuckDB/sqlglot versions.

## 4c. Holdout set

`questions/holdout.yaml` holds 16 questions written after the linter and classifier, in different phrasing and SQL styles, with 32 wrong variants (11 deliberately evasive) and 5 unusually written correct variants. It tests whether the rules generalise and whether correct SQL is wrongly blocked. Its first measurement is frozen in `results/holdout_v1.json` (pinned by a test); fixes made afterwards are reported as v2 and are explicitly no longer held out. Models can be evaluated on it with `--set holdout`; reports split accuracy into development and holdout.

## 5. Validating the failure taxonomy

`classify.py` is deterministic and uses the linter's AST rules plus question metadata (`metric`, counting `grain`). Against the 66 hand-labelled trap queries it recovers the intended class 66 of 66 times, and each trap query evidences exactly one class.

This number measures internal consistency, not independent validity. On the holdout (written afterwards, with evasive variants) agreement was 24 of 32 on first measurement and 30 of 32 after the linter fixes; every disagreement was a variant falling into `UNATTRIBUTED_LOGIC_ERROR`, which is the correct failure mode for a classifier that does not recognise a pattern. The labels, the rules and the questions were written by the same author, so agreement is expected. What it does establish is that (a) each trap is attributable to a single cause, and (b) the classifier does not mis-attribute across classes. The honest test of the taxonomy is how many *real* model failures land in `UNATTRIBUTED_LOGIC_ERROR` after a run; that residue is reported prominently and is the backlog for new classes.

## 6. Threats to validity

- **Authored traps.** The traps are realistic but chosen by the author. The development-set guardrail figures are a regression suite. The holdout is a fairer test but shares the author; independent validation needs another author's questions and a second model family's outputs (`--model`).
- **Overlay vs. native.** Soft deletes, status noise and the UTC review convention are injected. Their *rates* (2.5% / 20% / all reviews) are design parameters, so measured deviations such as "-20% on delivered orders" reflect the injected noise rate. The per-order `customer_id`, the one-to-many cardinalities and naive timestamps are native to Olist.
- **Definitions are a choice.** Where two definitions are plausible (revenue, active customer, late delivery), the gold answer is the one in `semantic/semantic_layer.yaml`. A model that picks the other definition is wrong *relative to this warehouse's contract*, which is exactly the claim under test.
- **Question wording.** Questions that need a definition are written the way a business user would ask ("what was our revenue in 2017?"), without leaking the definition.
- **Single warehouse, single dialect.** DuckDB SQL on an e-commerce schema. Other dialects and schemas will shift the numbers.
- **Static analysis cannot see through CTEs.** A fan-out hidden inside a CTE that is later aggregated is invisible to the linter. Result-set comparison still catches it.
