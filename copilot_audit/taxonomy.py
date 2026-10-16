"""The failure taxonomy: the vocabulary shared by the linter, the classifier and the report."""
from __future__ import annotations

from dataclasses import dataclass

PASS = "PASS"
SOFT_DELETE = "SOFT_DELETE_IGNORED"
STATUS_CASE = "STATUS_CASE_MISMATCH"
FANOUT = "FANOUT_INFLATION"
DATE_BOUNDARY = "DATE_BOUNDARY_ERROR"
TIMEZONE = "TIMEZONE_MISMATCH"
DEFINITION = "DEFINITION_DRIFT"
ENTITY = "ENTITY_GRAIN_ERROR"
SCHEMA_ERROR = "SCHEMA_HALLUCINATION"
SYNTAX_ERROR = "SYNTAX_OR_RUNTIME_ERROR"
UNSAFE = "BLOCKED_UNSAFE_OR_TIMEOUT"
OTHER = "UNATTRIBUTED_LOGIC_ERROR"


@dataclass(frozen=True)
class FailureClass:
    code: str
    title: str
    layer: str          # which layer of the stack owns the defect
    silent: bool        # True when the query runs and returns plausible numbers
    description: str
    mitigation: str


CLASSES: dict[str, FailureClass] = {c.code: c for c in [
    FailureClass(FANOUT, "Fan-out at the wrong grain", "modelling", True,
                 "A one-to-many (or sibling many-to-many) join repeats rows, so SUM/AVG/COUNT over the repeated "
                 "side are inflated or silently re-weighted.",
                 "Pre-aggregate each fact to the question's grain in its own CTE; count DISTINCT keys; route "
                 "multi-fact questions through governed metrics."),
    FailureClass(SOFT_DELETE, "Soft deletes ignored", "data contract", True,
                 "Rows flagged is_deleted are still counted because the predicate was never written.",
                 "Expose soft-deleted rows only through views that already filter them; lint for the predicate."),
    FailureClass(DEFINITION, "Business-definition drift", "semantic layer", True,
                 "The SQL implements a reasonable but non-canonical definition of a metric "
                 "(revenue basis, active-customer window, late-delivery rule, relative dates).",
                 "Publish canonical metric definitions and inject them into the prompt; block free-form SQL for "
                 "registered metrics."),
    FailureClass(ENTITY, "Wrong entity grain", "modelling", True,
                 "customer_id (per order) is treated as the person; the real identity is customer_unique_id.",
                 "Rename or hide the misleading key; document identity in the semantic layer."),
    FailureClass(DATE_BOUNDARY, "Date-boundary error", "SQL craft", True,
                 "A date-only upper bound on a TIMESTAMP column silently drops the final day.",
                 "Half-open intervals (>= start, < next-day) enforced by linter."),
    FailureClass(TIMEZONE, "Timezone mismatch", "data contract", True,
                 "A UTC-naive timestamp is bucketed or compared as if it were local time (or vice versa).",
                 "Store tz-aware types, or document the convention per column and lint for AT TIME ZONE."),
    FailureClass(STATUS_CASE, "Status normalisation missed", "data quality", True,
                 "A raw comparison on a dirty categorical column misses differently-cased or padded values.",
                 "Clean at ingestion; until then lint for lower(trim()) on the column."),
    FailureClass(SCHEMA_ERROR, "Schema hallucination", "model", False,
                 "The query references a column or table that does not exist. Loud: the database rejects it.",
                 "Retry loop with the error message; schema-grounded prompt."),
    FailureClass(SYNTAX_ERROR, "Syntax / runtime error", "model", False,
                 "The query fails to parse or errors at runtime. Loud.",
                 "Retry loop."),
    FailureClass(UNSAFE, "Blocked (unsafe statement or timeout)", "platform", False,
                 "Non-SELECT statement, forbidden function or time limit exceeded.",
                 "Read-only role, statement allow-list, timeouts."),
    FailureClass(OTHER, "Unattributed logic error", "model", True,
                 "The result is wrong but no known trap signature explains it. These are the failures "
                 "the taxonomy has not yet named.",
                 "Human review; add a class if a pattern emerges."),
]}

# Order used to pick a single primary label when several signals fire.
PRIORITY = [FANOUT, SOFT_DELETE, ENTITY, TIMEZONE, DATE_BOUNDARY, STATUS_CASE, DEFINITION]

FAMILY_TO_CLASS = {
    "fanout": FANOUT, "soft_delete": SOFT_DELETE, "definition": DEFINITION, "entity_grain": ENTITY,
    "date_boundary": DATE_BOUNDARY, "timezone": TIMEZONE, "status_case": STATUS_CASE,
}
