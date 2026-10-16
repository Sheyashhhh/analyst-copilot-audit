# Evaluation report

## Run `selftest-oracle`  -  HARNESS SELF-TEST (scripted, not a model)

> These answers are produced by a script, not a language model. The oracle scores 100% and the trap-injector scores low *by construction*. Neither number says anything about model accuracy; they only prove that the execute, compare, classify and report pipeline works.

80 questions x 1 condition(s) x 1 trial(s). Environment: {'python': '3.12.3', 'duckdb': '1.5.6', 'sqlglot': '30.23.0', 'os': 'Linux'}. Prompt hashes: {'C0': '99cc68aaff4f6464'}.

| Condition | n | Accuracy | 95% CI | Silent-error rate | Risk-weighted error | $/question |
|---|---:|---:|---|---:|---:|---:|
| C0 | 80 | 100.0% | 95.4% to 100.0% | 0.0% | 0.0% | n/a |

Silent-error rate = wrong answers that executed successfully / all answers. Risk-weighted error weights money and registered-metric questions 3x (see `questions.risk_weight`).

| Condition | Latency mean | median | p95 | Tokens in / out | Model calls per question |
|---|---:|---:|---:|---:|---:|
| C0 | 0.00s | 0.00s | 0.00s | 0 / 0 | 1.00 |

### Lift (paired by question)


### Accuracy by family

| Family | C0 |
|---|---:|
| Controls (no trap) | 100.0% |
| Soft deletes | 100.0% |
| Status casing | 100.0% |
| Join fan-out | 100.0% |
| Date boundaries | 100.0% |
| Timezones | 100.0% |
| Metric definitions | 100.0% |
| Entity grain | 100.0% |

### Failure taxonomy

No failures.

### Guardrail: detection, prevention, recovery, false blocks

Measured on the first-attempt SQL, before any retry. Detection = wrong answers flagged (BLOCK or REVIEW); prevention = wrong answers blocked; false blocks = correct answers blocked.

| condition | first-attempt wrong | detection (flagged) | prevention (blocked) | false blocks | false flags |
|---|---|---|---|---|---|
| C0 | 0 | n/a | n/a | 0/80 | 20/80 |

### Automation policy (from condition C0)

| family | n questions | accuracy | 95% CI | silent-failure questions | policy | reason |
|---|---|---|---|---|---|---|
| Controls (no trap) | 14 | 100.0% | 78.5% to 100.0% | 0 | HUMAN REVIEW | accuracy high but n=14 cannot certify 90%; ~35 questions needed |
| Soft deletes | 10 | 100.0% | 72.2% to 100.0% | 0 | HUMAN REVIEW | accuracy high but n=10 cannot certify 90%; ~35 questions needed |
| Status casing | 8 | 100.0% | 67.6% to 100.0% | 0 | HUMAN REVIEW | accuracy high but n=8 cannot certify 90%; ~35 questions needed |
| Join fan-out | 14 | 100.0% | 78.5% to 100.0% | 0 | HUMAN REVIEW | accuracy high but n=14 cannot certify 90%; ~35 questions needed |
| Date boundaries | 8 | 100.0% | 67.6% to 100.0% | 0 | HUMAN REVIEW | accuracy high but n=8 cannot certify 90%; ~35 questions needed |
| Timezones | 6 | 100.0% | 61.0% to 100.0% | 0 | HUMAN REVIEW | accuracy high but n=6 cannot certify 90%; ~35 questions needed |
| Metric definitions | 12 | 100.0% | 75.7% to 100.0% | 0 | REGISTRY ONLY | competing metric definitions: answer from governed SQL, not generation |
| Entity grain | 8 | 100.0% | 67.6% to 100.0% | 0 | HUMAN REVIEW | accuracy high but n=8 cannot certify 90%; ~35 questions needed |

## Run `selftest-traps`  -  HARNESS SELF-TEST (scripted, not a model)

> These answers are produced by a script, not a language model. The oracle scores 100% and the trap-injector scores low *by construction*. Neither number says anything about model accuracy; they only prove that the execute, compare, classify and report pipeline works.

80 questions x 1 condition(s) x 1 trial(s). Environment: {'python': '3.12.3', 'duckdb': '1.5.6', 'sqlglot': '30.23.0', 'os': 'Linux'}. Prompt hashes: {'C0': '99cc68aaff4f6464'}.

| Condition | n | Accuracy | 95% CI | Silent-error rate | Risk-weighted error | $/question |
|---|---:|---:|---|---:|---:|---:|
| C0 | 80 | 17.5% | 10.7% to 27.3% | 82.5% | 89.2% | n/a |

Silent-error rate = wrong answers that executed successfully / all answers. Risk-weighted error weights money and registered-metric questions 3x (see `questions.risk_weight`).

| Condition | Latency mean | median | p95 | Tokens in / out | Model calls per question |
|---|---:|---:|---:|---:|---:|
| C0 | 0.00s | 0.00s | 0.00s | 0 / 0 | 1.00 |

### Lift (paired by question)


### Accuracy by family

| Family | C0 |
|---|---:|
| Controls (no trap) | 100.0% |
| Soft deletes | 0.0% |
| Status casing | 0.0% |
| Join fan-out | 0.0% |
| Date boundaries | 0.0% |
| Timezones | 0.0% |
| Metric definitions | 0.0% |
| Entity grain | 0.0% |

### Failure taxonomy

| Class | Silent? | C0 |
|---|:-:|---:|
| Date-boundary error | yes | 8 |
| Business-definition drift | yes | 12 |
| Wrong entity grain | yes | 8 |
| Fan-out at the wrong grain | yes | 14 |
| Soft deletes ignored | yes | 10 |
| Status normalisation missed | yes | 8 |
| Timezone mismatch | yes | 6 |

**Unattributed failures: 0 of 66 (0.0%)**. These are errors the taxonomy cannot yet explain; read them in `predictions.jsonl` and extend the taxonomy (docs/taxonomy.md).

### Guardrail: detection, prevention, recovery, false blocks

Measured on the first-attempt SQL, before any retry. Detection = wrong answers flagged (BLOCK or REVIEW); prevention = wrong answers blocked; false blocks = correct answers blocked.

| condition | first-attempt wrong | detection (flagged) | prevention (blocked) | false blocks | false flags |
|---|---|---|---|---|---|
| C0 | 66 | 59/66 | 43/66 | 0/14 | 0/14 |

### Automation policy (from condition C0)

| family | n questions | accuracy | 95% CI | silent-failure questions | policy | reason |
|---|---|---|---|---|---|---|
| Controls (no trap) | 14 | 100.0% | 78.5% to 100.0% | 0 | HUMAN REVIEW | accuracy high but n=14 cannot certify 90%; ~35 questions needed |
| Soft deletes | 10 | 0.0% | 0.0% to 27.8% | 10 | BLOCK free-form SQL | below 80%: use registry or governed views |
| Status casing | 8 | 0.0% | 0.0% to 32.4% | 8 | BLOCK free-form SQL | below 80%: use registry or governed views |
| Join fan-out | 14 | 0.0% | 0.0% to 21.5% | 14 | BLOCK free-form SQL | below 80%: use registry or governed views |
| Date boundaries | 8 | 0.0% | 0.0% to 32.4% | 8 | BLOCK free-form SQL | below 80%: use registry or governed views |
| Timezones | 6 | 0.0% | 0.0% to 39.0% | 6 | BLOCK free-form SQL | below 80%: use registry or governed views |
| Metric definitions | 12 | 0.0% | 0.0% to 24.3% | 12 | REGISTRY ONLY | competing metric definitions: answer from governed SQL, not generation |
| Entity grain | 8 | 0.0% | 0.0% to 32.4% | 8 | BLOCK free-form SQL | below 80%: use registry or governed views |
