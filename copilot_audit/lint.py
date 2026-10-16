"""Static SQL analysis against the warehouse's known hazards.

The same analyser serves two jobs:

* **Guardrail** (pre-execution, question-agnostic): ``decide(lint(sql))`` returns ALLOW / REVIEW / BLOCK.
* **Failure attribution** (post-hoc, question-aware): ``lint(sql, question)`` additionally checks metric
  conformance and counting grain, and is used by ``classify.py`` to name *why* a wrong query is wrong.

Rules are deliberately conservative AST checks, not regexes: a rule fires on structure
(e.g. "SUM over an orders-grain measure inside a SELECT that also joins fact_order_items").
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

import duckdb
import sqlglot
from sqlglot import exp

from . import taxonomy as T
from .paths import SCHEMA_SQL

FACT_GRAIN = {"fact_orders": "order", "fact_order_items": "item", "fact_payments": "payment", "fact_reviews": "review"}
# For a measure living on table T, which other facts in the same SELECT repeat T's rows?
FANS = {
    "fact_orders": {"fact_order_items", "fact_payments", "fact_reviews"},
    "fact_order_items": {"fact_payments", "fact_reviews"},
    "fact_payments": {"fact_order_items", "fact_reviews"},
    "fact_reviews": {"fact_order_items", "fact_payments"},
}
CHILD_FACTS = {"fact_order_items", "fact_payments", "fact_reviews"}
MONEY_COLS = {"price", "freight_value", "payment_value", "order_payment_total"}
CUSTOMER_ID_COLS = {"customer_unique_id", "customer_id", "customer_key"}
PER_ORDER_ID_COLS = {"customer_id", "customer_key"}
TS_COLS = {"order_purchase_ts", "order_approved_ts", "order_delivered_carrier_ts", "order_delivered_customer_ts",
           "order_estimated_delivery_ts", "shipping_limit_ts", "review_answered_at", "deleted_at"}

BLOCK, REVIEW, ALLOW = "BLOCK", "REVIEW", "ALLOW"


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str        # BLOCK | REVIEW
    taxonomy: str | None  # taxonomy class this finding evidences (None = advisory)
    message: str


RULES = {
    "G01": "Soft-deleted orders not excluded (fact_orders.is_deleted)",
    "G02": "Erased customers not excluded from a customer-level metric (dim_customer.is_deleted)",
    "G03": "order_status compared without lower(trim(...))",
    "G04": "Aggregate over a measure repeated by a one-to-many join (fan-out)",
    "G05": "Relative-date function on a frozen snapshot",
    "G06": "Date-only boundary on a TIMESTAMP column",
    "G07": "UTC-naive review timestamp used without AT TIME ZONE conversion",
    "G08": "customer_id / customer_key used as the person identity",
    "G09": "Revenue-like aggregate outside the metric registry",
    "G10": "Row count taken at a finer grain than the question asks for",
    "M01": "SQL does not conform to the registered metric definition",
}


@lru_cache(maxsize=1)
def _column_owners() -> dict[str, set[str]]:
    con = duckdb.connect(":memory:")
    con.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
    owners: dict[str, set[str]] = {}
    for t, c in con.execute("SELECT table_name, column_name FROM information_schema.columns").fetchall():
        owners.setdefault(c, set()).add(t)
    con.close()
    return owners


def _parse(sql: str):
    try:
        return sqlglot.parse_one(sql, read="duckdb")
    except sqlglot.errors.SqlglotError:
        return None


def _cte_names(tree) -> set[str]:
    return {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}


def _nearest_select(node):
    return node.find_ancestor(exp.Select)


def _scope_tables(select, ctes: set[str]) -> dict[str, str]:
    """alias -> base table for tables that belong directly to this SELECT (not nested subqueries)."""
    out = {}
    for t in select.find_all(exp.Table):
        if _nearest_select(t) is select and t.name.lower() not in ctes:
            out[(t.alias_or_name or t.name).lower()] = t.name.lower()
    return out


def _resolve(col: exp.Column, alias_map: dict[str, str]) -> str | None:
    if col.table:
        return alias_map.get(col.table.lower())
    owners = _column_owners().get(col.name.lower(), set())
    inter = [t for t in alias_map.values() if t in owners]
    return inter[0] if len(set(inter)) == 1 else None


def _global_alias_map(tree, ctes) -> dict[str, str]:
    m = {}
    for t in tree.find_all(exp.Table):
        if t.name.lower() not in ctes:
            m[(t.alias_or_name or t.name).lower()] = t.name.lower()
    return m


def _is_midnight_literal(node) -> bool:
    """'2018-03-31', DATE '2018-03-31' or TIMESTAMP '2018-03-31 00:00:00' (all denote midnight)."""
    if isinstance(node, exp.Cast):
        node = node.this
    if isinstance(node, exp.Literal) and node.is_string:
        return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}( 00:00(:00(\.0+)?)?)?", node.this.strip()))
    return False


def _bare_ts(node) -> bool:
    return isinstance(node, exp.Column) and node.name.lower() in TS_COLS


# --------------------------------------------------------------------------- rules
def _g01_g02(tree, ctes, amap) -> list[Finding]:
    out = []
    tables = set(amap.values())
    facts = tables & set(FACT_GRAIN)
    del_cols = [c for c in tree.find_all(exp.Column) if c.name.lower() == "is_deleted"]

    def deleted_at_is_null() -> bool:
        return any(c.name.lower() == "deleted_at" and isinstance(c.parent, exp.Is) and isinstance(c.parent.expression, exp.Null)
                   and not isinstance(c.parent.parent, exp.Not)          # IS NOT NULL selects the deleted rows
                   for c in tree.find_all(exp.Column))

    def covers(table: str) -> bool:
        if table == "fact_orders" and deleted_at_is_null():     # deleted_at IS NULL <=> NOT is_deleted
            return True
        for c in del_cols:
            if c.table:
                if amap.get(c.table.lower()) == table:
                    return True
            else:  # unqualified: benefit of the doubt for every table in play that owns it
                if table in tables:
                    return True
        return False

    if facts and not covers("fact_orders"):
        out.append(Finding("G01", BLOCK, T.SOFT_DELETE, "Query reads order facts but never filters fact_orders.is_deleted."))

    # customer-level metric?
    entity_use = False
    for cnt in tree.find_all(exp.Count):
        if isinstance(cnt.this, exp.Distinct):
            if any(c.name.lower() in CUSTOMER_ID_COLS for c in cnt.find_all(exp.Column)):
                entity_use = True
    for sel in tree.find_all(exp.Select):
        for p in sel.expressions:
            inner = p.this if isinstance(p, exp.Alias) else p
            if isinstance(inner, exp.Column) and inner.name.lower() in {"customer_unique_id", "customer_id"} and sel.args.get("group"):
                entity_use = True
    if entity_use and "dim_customer" in tables and not covers("dim_customer"):
        out.append(Finding("G02", BLOCK, T.SOFT_DELETE, "Customer-level metric without excluding erased customers (dim_customer.is_deleted)."))
    return out


def _g03(tree) -> list[Finding]:
    for col in tree.find_all(exp.Column):
        if col.name.lower() != "order_status":
            continue
        if isinstance(col.parent, (exp.ILike, exp.Like)) and col.parent.this is col:
            pat = col.parent.expression
            if isinstance(pat, exp.Literal) and pat.is_string and pat.this.startswith("%") and pat.this.endswith("%"):
                continue      # '%shipped%' tolerates case and padding; legitimate idiom
        wrappers = set()
        n = col.parent
        while n is not None and not isinstance(n, (exp.Select, exp.Where, exp.Group, exp.Having)):
            if isinstance(n, exp.Lower):
                wrappers.add("lower")
            elif isinstance(n, exp.Trim):
                wrappers.add("trim")
            n = n.parent
        if wrappers != {"lower", "trim"}:
            return [Finding("G03", BLOCK, T.STATUS_CASE,
                            "order_status holds mixed case and padded values; compare lower(trim(order_status)).")]
    return []


def _row_preserving(select) -> bool:
    """A SELECT that neither groups, aggregates nor de-duplicates keeps one output row per joined row."""
    if select.args.get("group") or select.args.get("distinct"):
        return False
    return not any(_nearest_select(a) is select for a in select.find_all(exp.AggFunc))


def _cte_defs(tree) -> dict:
    return {c.alias_or_name.lower(): c.this for c in tree.find_all(exp.CTE)}


def _derived_sources(sel, cte_defs) -> dict:
    """alias -> inner SELECT for CTE references and derived tables that feed this SELECT directly."""
    out = {}
    for t in sel.find_all(exp.Table):
        if _nearest_select(t) is sel and t.name.lower() in cte_defs:
            out[(t.alias_or_name or t.name).lower()] = cte_defs[t.name.lower()]
    for sq in sel.find_all(exp.Subquery):
        if _nearest_select(sq) is sel and isinstance(sq.parent, (exp.From, exp.Join)) and isinstance(sq.this, exp.Select):
            out[(sq.alias or "").lower()] = sq.this
    return out


def _scope_info(select, ctes, cte_defs, depth=0):
    """(fact tables visible in this scope's rows, output-column -> fact lineage, row_preserving)."""
    amap = _scope_tables(select, ctes)
    facts = {t for t in amap.values() if t in FACT_GRAIN}
    derived = _derived_sources(select, cte_defs) if depth < 6 else {}
    infos = {a: _scope_info(d, ctes, cte_defs, depth + 1) for a, d in derived.items()}
    for a, (f, _, rp) in infos.items():
        if rp:
            facts |= f
    lineage: dict[str, set] = {}
    for e in select.expressions:
        name = (e.alias_or_name or "").lower()
        tabs: set = set()
        for col in e.find_all(exp.Column):
            tabs |= _col_tables(col, amap, infos)
        if name and tabs:
            lineage[name] = tabs
    return facts, lineage, _row_preserving(select)


def _col_tables(col, amap, infos) -> set:
    base = _resolve(col, amap)
    if base in FACT_GRAIN:
        return {base}
    if col.table and col.table.lower() in infos:
        return set(infos[col.table.lower()][1].get(col.name.lower(), set()))
    if not col.table:
        found: set = set()
        for _, lin, _ in infos.values():
            found |= lin.get(col.name.lower(), set())
        return found
    return set()


def _pk_restricted(sel, amap) -> set[str]:
    """Child facts pinned to one row per order by an equality on a primary-key component (e.g. payment_sequential = 1)."""
    pinned = {"payment_sequential": "fact_payments", "order_item_id": "fact_order_items"}
    out = set()
    for eq in sel.find_all(exp.EQ):
        if _nearest_select(eq) is not sel:
            continue
        for c, lit in ((eq.this, eq.expression), (eq.expression, eq.this)):
            if isinstance(c, exp.Column) and c.name.lower() in pinned and isinstance(lit, exp.Literal) and not lit.is_string:
                if _resolve(c, amap) == pinned[c.name.lower()]:
                    out.add(pinned[c.name.lower()])
    return out


def _g04_g10(tree, ctes, question) -> list[Finding]:
    """Fan-out (G04) and wrong counting grain (G10), per SELECT scope, following lineage through row-preserving CTEs."""
    out: list[Finding] = []
    grain_q = getattr(question, "grain", None) if question else None
    cdefs = _cte_defs(tree)
    for sel in tree.find_all(exp.Select):
        amap = _scope_tables(sel, ctes)
        facts, _, _ = _scope_info(sel, ctes, cdefs)
        if not facts:
            continue
        infos = {a: _scope_info(d, ctes, cdefs) for a, d in _derived_sources(sel, cdefs).items()}
        restricted = _pk_restricted(sel, amap)
        for agg in sel.find_all(exp.AggFunc):
            if _nearest_select(agg) is not sel or isinstance(agg, (exp.Min, exp.Max)):
                continue
            if isinstance(agg.this, exp.Distinct):      # COUNT(DISTINCT key) is the cure, not the disease
                continue
            tabs: set = set()
            for c in agg.find_all(exp.Column):
                tabs |= _col_tables(c, amap, infos)
            tabs &= set(FACT_GRAIN)
            if isinstance(agg, exp.Count) and isinstance(agg.this, exp.Star):
                kids = (facts & CHILD_FACTS) - restricted
                if len(kids) >= 2:
                    out.append(Finding("G04", BLOCK, T.FANOUT,
                                       f"COUNT(*) over {sorted(facts)}: sibling child facts multiply rows."))
                elif grain_q and len(facts) >= 2:
                    finest = next(iter(kids)) if kids else "fact_orders"
                    if FACT_GRAIN[finest] != grain_q and (facts - restricted) - {"fact_orders"}:
                        out.append(Finding("G10", BLOCK, T.FANOUT,
                                           f"COUNT(*) counts {FACT_GRAIN[finest]} rows but the question counts {grain_q}s."))
                continue
            if len(facts) < 2 or not tabs:
                continue
            # An expression's grain is the finest fact it touches (orders is the coarsest).
            for t in [x for x in tabs if x != "fact_orders"] or ["fact_orders"]:
                clash = (FANS[t] & facts) - restricted
                if clash:
                    out.append(Finding("G04", BLOCK, T.FANOUT,
                                       f"{type(agg).__name__.upper()} over a {FACT_GRAIN[t]}-grain measure while "
                                       f"{sorted(clash)} are joined in the same SELECT or hidden CTE."))
                    break
    return _dedupe(out)


def _g05(tree) -> list[Finding]:
    if any(True for _ in tree.find_all(exp.CurrentDate, exp.CurrentTimestamp, exp.CurrentTime, exp.CurrentDatetime)) or \
            any(f.name.lower() in {"now", "today", "current_localtimestamp"} for f in tree.find_all(exp.Anonymous)):
        return [Finding("G05", BLOCK, T.DEFINITION,
                        "Relative date on a frozen snapshot (data ends 2018-08-31); anchor to the as-of date.")]
    return []


def _g06(tree) -> list[Finding]:
    hits = []
    for node in tree.find_all(exp.LTE, exp.GT, exp.EQ, exp.GTE, exp.LT, exp.Between):
        if isinstance(node, exp.Between):
            if _bare_ts(node.this) and _is_midnight_literal(node.args.get("high")):
                hits.append(node)
            continue
        l, r = node.this, node.expression
        if _bare_ts(l) and _is_midnight_literal(r) and isinstance(node, (exp.LTE, exp.GT, exp.EQ)):
            hits.append(node)
        elif _bare_ts(r) and _is_midnight_literal(l) and isinstance(node, (exp.GTE, exp.LT, exp.EQ)):
            hits.append(node)
    if hits:
        return [Finding("G06", BLOCK, T.DATE_BOUNDARY,
                        "Inclusive / equality comparison of a TIMESTAMP with a date-only literal drops the final day; "
                        "use >= start AND < next_day.")]
    return []


def _tz_converted(col) -> bool:
    """True when the column sits inside AT TIME ZONE, timezone(), or convert_timezone()."""
    n = col.parent
    while n is not None:
        if isinstance(n, exp.AtTimeZone):
            return True
        if isinstance(n, exp.ConvertTimezone):
            return True
        if isinstance(n, exp.Anonymous) and n.name.lower() in {"timezone", "convert_timezone"}:
            return True
        n = n.parent
    return False


def _g07(tree) -> list[Finding]:
    for col in tree.find_all(exp.Column):
        if col.name.lower() == "review_answered_at" and not _tz_converted(col):
            return [Finding("G07", BLOCK, T.TIMEZONE,
                            "review_answered_at is UTC-naive while order timestamps are America/Sao_Paulo-naive; "
                            "convert with AT TIME ZONE.")]
    return []


def _g08(tree) -> list[Finding]:
    def per_order(c):
        return isinstance(c, exp.Column) and c.name.lower() in PER_ORDER_ID_COLS
    for cnt in tree.find_all(exp.Count):
        if isinstance(cnt.this, exp.Distinct) and any(per_order(c) and not _in_join_on(c) for c in cnt.find_all(exp.Column)):
            return [Finding("G08", REVIEW, T.ENTITY, "customer_id identifies an order, not a person; use customer_unique_id.")]
    for sel in tree.find_all(exp.Select):
        grp = sel.args.get("group")
        cols = []
        if grp:
            cols += [c for c in grp.find_all(exp.Column)]
        for p in sel.expressions:
            inner = p.this if isinstance(p, exp.Alias) else p
            if grp and isinstance(inner, exp.Column):
                cols.append(inner)
        if any(per_order(c) for c in cols):
            return [Finding("G08", REVIEW, T.ENTITY, "Grouping by customer_id groups by order, not by person.")]
    return []


def _in_join_on(col) -> bool:
    return col.find_ancestor(exp.Join) is not None


def _g09(tree, ctes) -> list[Finding]:
    for agg in tree.find_all(exp.Sum, exp.Avg):
        if any(c.name.lower() in MONEY_COLS for c in agg.find_all(exp.Column)):
            return [Finding("G09", REVIEW, None, "Revenue-like aggregate; route through the governed metric registry.")]
    return []


def _dedupe(fs: list[Finding]) -> list[Finding]:
    seen, out = set(), []
    for f in fs:
        k = (f.rule, f.message)
        if k not in seen:
            seen.add(k)
            out.append(f)
    return out


# ---------------------------------------------------------------- metric conformance
def _agg_columns(tree) -> set[str]:
    cols = set()
    for agg in tree.find_all(exp.Sum, exp.Avg):
        cols |= {c.name.lower() for c in agg.find_all(exp.Column)}
    return cols


def _literals(tree) -> set[str]:
    return {l.this.lower() for l in tree.find_all(exp.Literal) if l.is_string}


def metric_violations(tree, sql: str, metric: str) -> list[str]:
    cols, lits, low = _agg_columns(tree), _literals(tree), sql.lower()
    v: list[str] = []
    statuses = lits & {"delivered", "shipped", "invoiced", "processing", "created", "approved", "canceled", "unavailable"}
    delivered_only = metric in {"net_revenue", "aov", "late_delivery_rate", "freight_ratio"}
    if delivered_only and "delivered" in statuses and statuses != {"delivered"}:
        v.append(f"restricted to delivered orders only, but the query also admits {sorted(statuses - {'delivered'})}")
    if metric == "net_revenue":
        if "payment_value" in cols or "order_payment_total" in cols:
            v.append("revenue measured on payments instead of item price")
        if "freight_value" in cols:
            v.append("freight included in revenue")
        if "delivered" not in lits:
            v.append("not restricted to delivered orders")
    elif metric == "gmv":
        if "delivered" in lits and not {"canceled", "unavailable"} <= lits:
            v.append("GMV restricted to delivered instead of excluding canceled/unavailable")
        if "freight_value" in cols or "payment_value" in cols:
            v.append("GMV must be item price only")
    elif metric == "cash_collected":
        if "delivered" in lits and not {"canceled", "unavailable"} <= lits:
            v.append("cash collected restricted to delivered instead of excluding canceled/unavailable")
    elif metric == "aov":
        if "order_payment_total" in cols or "payment_value" in cols:
            v.append("AOV computed from payments instead of net revenue / orders")
        if "delivered" not in lits:
            v.append("AOV not restricted to delivered orders")
    elif metric == "active_customers":
        if not {"canceled", "unavailable"} <= lits:
            v.append("active customers must exclude canceled/unavailable orders")
        ninety = "2018-06-03" in low or re.search(r"interval\s*'?\(?\s*(89|90)\b", low) or "90 day" in low
        if not ninety:
            v.append("active window must be the 90 days ending 2018-08-31 (starts 2018-06-03)")
    elif metric == "late_delivery_rate":
        has_date_cast = any(isinstance(c, exp.Cast) and c.to.is_type("date") and
                            any(col.name.lower() == "order_delivered_customer_ts" for col in c.find_all(exp.Column))
                            for c in tree.find_all(exp.Cast)) or "date(" in low or "date_trunc('day'" in low
        if not has_date_cast:
            v.append("late must compare calendar dates, not timestamps")
    elif metric == "freight_ratio":
        for agg in tree.find_all(exp.Sum):
            names = {c.name.lower() for c in agg.find_all(exp.Column)}
            if {"price", "freight_value"} <= names:
                v.append("denominator must be net revenue (price only), not price + freight")
        for div in tree.find_all(exp.Div):
            den = {c.name.lower() for c in div.expression.find_all(exp.Column)}
            if {"price", "freight_value"} <= den:
                v.append("denominator must be net revenue (price only), not price + freight")
    return v


# --------------------------------------------------------------------------- API
def lint(sql: str, question=None) -> list[Finding]:
    tree = _parse(sql)
    if tree is None:
        return []
    ctes = _cte_names(tree)
    amap = _global_alias_map(tree, ctes)
    findings: list[Finding] = []
    findings += _g01_g02(tree, ctes, amap)
    findings += _g03(tree)
    findings += _g04_g10(tree, ctes, question)
    findings += _g05(tree)
    findings += _g06(tree)
    findings += _g07(tree)
    findings += _g08(tree)
    findings += _g09(tree, ctes)
    metric = getattr(question, "metric", None) if question else None
    if metric:
        for msg in metric_violations(tree, sql, metric):
            findings.append(Finding("M01", BLOCK, T.DEFINITION, f"{metric}: {msg}"))
    return _dedupe(findings)


def decide(findings: list[Finding]) -> str:
    if any(f.severity == BLOCK for f in findings):
        return BLOCK
    if any(f.severity == REVIEW for f in findings):
        return REVIEW
    return ALLOW
