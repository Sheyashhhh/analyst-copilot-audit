"""Result-set equivalence: the correctness criterion for the whole audit.

There are many correct queries for any business question, so SQL text is never compared.
Two result sets are equivalent when, after normalisation:

* every gold column can be matched to a distinct prediction column holding the same values
  (column names and column order are ignored; extra prediction columns are tolerated);
* the rows agree as a multiset, or as a sequence when the question is ordered;
* numbers agree to the precision of the gold answer (half a unit in its last decimal place).
"""
from __future__ import annotations

import datetime as dt
import decimal
from collections import Counter
from dataclasses import dataclass, field

from .executor import ExecResult


@dataclass
class Comparison:
    match: bool
    reason: str                      # match | exec_error | truncated | empty_result | row_count | column_values | row_values | order
    detail: dict = field(default_factory=dict)


def _is_num(v) -> bool:
    return isinstance(v, (int, float, decimal.Decimal)) and not isinstance(v, bool)


def _norm(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, float) and v != v:       # NaN is its own value, never numerically "equal" to anything
        return "NaN"
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, str):
        return v
    return v


def _decimals(x: float) -> int:
    s = f"{x:.6f}".rstrip("0")
    return 0 if "." not in s else len(s.split(".")[1])


def _num_eq(a: float, b: float, d: int) -> bool:
    return abs(a - b) <= 0.5 * 10 ** (-d) + 1e-9


def _val_eq(a, b, d: int) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if _is_num(a) and _is_num(b):
        return _num_eq(float(a), float(b), d)
    return a == b


def _sort_key(v, d: int):
    if v is None:
        return (0, 0, "")
    if _is_num(v):
        return (1, float(v), "")
    return (2, 0, str(v))


def _col(rows, j):
    return [r[j] for r in rows]


def _cols_match(gcol, pcol, d: int, ordered: bool) -> bool:
    if len(gcol) != len(pcol):
        return False
    if ordered:
        return all(_val_eq(a, b, d) for a, b in zip(gcol, pcol))
    gs = sorted(gcol, key=lambda v: _sort_key(v, d))
    ps = sorted(pcol, key=lambda v: _sort_key(v, d))
    return all(_val_eq(a, b, d) for a, b in zip(gs, ps))


def compare(gold: ExecResult, pred: ExecResult, ordered: bool = False) -> Comparison:
    if not pred.ok:
        return Comparison(False, "exec_error", {"error_kind": pred.error_kind, "error": pred.error})
    if pred.truncated:
        return Comparison(False, "truncated")
    g_rows = [tuple(_norm(v) for v in r) for r in gold.rows]
    p_rows = [tuple(_norm(v) for v in r) for r in pred.rows]
    if len(p_rows) == 0 and len(g_rows) > 0:
        return Comparison(False, "empty_result", {"gold_rows": len(g_rows)})
    if len(g_rows) != len(p_rows):
        return Comparison(False, "row_count", {"gold_rows": len(g_rows), "pred_rows": len(p_rows)})
    if not g_rows:
        return Comparison(True, "match")

    gk, pk = len(g_rows[0]), len(p_rows[0])
    # per-gold-column precision
    # Fractional columns (Decimal/float in the engine's output) are held to at least 2 decimals even when the
    # gold value happens to end in zeros (0.10 must not accept 0.12); integer columns are exact counts.
    dec = []
    for j in range(gk):
        ds = [_decimals(float(v)) for v in _col(g_rows, j) if _is_num(v)]
        fractional = any(isinstance(r[j], (float, decimal.Decimal)) for r in gold.rows)
        dec.append(max(max(ds, default=0), 2) if fractional else (max(ds) if ds else 0))

    # Candidate prediction columns for every gold column (value multisets must agree).
    cands: list[list[int]] = []
    for j in range(gk):
        gcol = _col(g_rows, j)
        cands.append([k for k in range(pk) if _cols_match(gcol, _col(p_rows, k), dec[j], ordered)])
        if not cands[-1]:
            detail = {"unmatched_gold_column": gold.columns[j] if j < len(gold.columns) else j}
            if gk == 1 and len(g_rows) == 1:
                gv, pv = g_rows[0][0], p_rows[0][0]
                if _is_num(gv) and _is_num(pv) and float(gv) != 0:
                    detail.update(gold_value=gv, pred_value=pv, ratio=float(pv) / float(gv))
            return Comparison(False, "column_values", detail)

    def row_eq(a, b):
        return all(_val_eq(x, y, dec[j]) for j, (x, y) in enumerate(zip(a, b)))

    def rkey(r):
        return tuple(_sort_key(v, dec[j]) for j, v in enumerate(r))

    def rounded(r):
        return tuple(round(float(v), dec[j]) if _is_num(v) else v for j, v in enumerate(r))

    gs_sorted = sorted(g_rows, key=rkey)
    g_counter = Counter(map(rounded, g_rows))

    def rows_match(mapping) -> bool:
        proj = [tuple(r[m] for m in mapping) for r in p_rows]
        if ordered:
            return all(row_eq(a, b) for a, b in zip(g_rows, proj))
        if Counter(map(rounded, proj)) == g_counter:
            return True
        return all(row_eq(a, b) for a, b in zip(gs_sorted, sorted(proj, key=rkey)))

    # Column value-multisets agreeing is necessary but not sufficient: two prediction columns can hold
    # identical values in different row alignments (constants, repeated measures). Search the assignments.
    budget = [2000]

    def search(j: int, used: tuple[int, ...]) -> bool:
        if j == gk:
            budget[0] -= 1
            return rows_match(used)
        for k in cands[j]:
            if k not in used and budget[0] > 0 and search(j + 1, used + (k,)):
                return True
        return False

    if search(0, ()):
        return Comparison(True, "match")
    return Comparison(False, "order" if ordered else "row_values")
