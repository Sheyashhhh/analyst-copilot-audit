from decimal import Decimal

from copilot_audit.compare import compare
from copilot_audit.executor import ExecResult


def R(cols, rows):
    return ExecResult("ok", cols, [tuple(r) for r in rows])


def test_ignores_column_names_and_order():
    g = R(["a", "b"], [(1, "x"), (2, "y")])
    p = R(["label", "n"], [("y", 2), ("x", 1)])
    assert compare(g, p).match


def test_extra_columns_tolerated():
    assert compare(R(["n"], [(5,)]), R(["n", "extra"], [(5, "z")])).match


def test_row_order_matters_only_when_ordered():
    g = R(["n"], [(1,), (2,)])
    p = R(["n"], [(2,), (1,)])
    assert compare(g, p, ordered=False).match
    assert not compare(g, p, ordered=True).match


def test_numeric_precision_follows_gold():
    assert compare(R(["v"], [(4.0863,)]), R(["v"], [(4.08631234,)])).match
    assert not compare(R(["v"], [(4.0863,)]), R(["v"], [(4.0864,)])).match
    assert not compare(R(["v"], [(12.56,)]), R(["v"], [(12.5,)])).match


def test_decimal_int_float_interchangeable():
    assert compare(R(["v"], [(Decimal("10.50"),)]), R(["v"], [(10.5,)])).match
    assert compare(R(["v"], [(3,)]), R(["v"], [(3.0,)])).match


def test_scalar_ratio_reported_on_mismatch():
    c = compare(R(["v"], [(100,)]), R(["v"], [(250,)]))
    assert not c.match and c.reason == "column_values" and abs(c.detail["ratio"] - 2.5) < 1e-9


def test_row_count_and_empty():
    assert compare(R(["v"], [(1,), (2,)]), R(["v"], [(1,)])).reason == "row_count"
    assert compare(R(["v"], [(1,)]), R(["v"], [])).reason == "empty_result"


def test_errors_never_match():
    bad = ExecResult("error", error="x", error_kind="schema")
    assert compare(R(["v"], [(1,)]), bad).reason == "exec_error"


def test_nulls_and_strings():
    assert compare(R(["a"], [(None,), ("x",)]), R(["a"], [("x",), (None,)])).match
    assert not compare(R(["a"], [("x",)]), R(["a"], [("X",)])).match


# ---- adversarial cases: multiplicity, constants, ambiguity, NaN, empties, ties ----
def test_duplicate_row_counts_are_not_ignored():
    """Same distinct rows, different multiplicity: an aggregation/fan-out error must not be certified."""
    assert not compare(R(["k"], [("a",), ("a",), ("b",)]), R(["k"], [("a",), ("b",), ("b",)])).match
    assert not compare(R(["k"], [("a",), ("b",)]), R(["k"], [("a",), ("a",), ("b",)])).match
    assert compare(R(["k"], [("a",), ("a",), ("b",)]), R(["k"], [("b",), ("a",), ("a",)])).match


def test_constant_and_identical_columns_do_not_confuse_the_mapping():
    g = R(["x", "y", "z"], [(1, 7, "a"), (2, 7, "b")])
    assert compare(g, R(["p", "q", "r"], [(7, "a", 1), (7, "b", 2)])).match           # shuffled columns, constant col
    assert not compare(g, R(["p", "q", "r"], [(7, "b", 1), (7, "a", 2)])).match       # values right, row alignment wrong


def test_two_prediction_columns_with_identical_values_resolve_by_row_alignment():
    g = R(["a", "b"], [(1, 2), (2, 1)])
    p = R(["u", "v", "w"], [(1, 1, 2), (2, 2, 1)])    # u and v hold the same multiset as gold's a and b
    assert compare(g, p).match


def test_both_empty_results_match_but_empty_vs_nonempty_does_not():
    assert compare(R(["n"], []), R(["n"], [])).match
    assert not compare(R(["n"], []), R(["n"], [(0,)])).match


def test_nan_and_null_are_distinct_and_self_equal():
    nan = float("nan")
    assert compare(R(["v"], [(nan,)]), R(["v"], [(nan,)])).match
    assert not compare(R(["v"], [(None,)]), R(["v"], [(nan,)])).match
    assert not compare(R(["v"], [(1.0,)]), R(["v"], [(nan,)])).match


def test_decimal_precision_boundaries():
    assert compare(R(["v"], [(Decimal("0.10"),)]), R(["v"], [(0.1,)])).match
    assert not compare(R(["v"], [(Decimal("0.10"),)]), R(["v"], [(0.12,)])).match
    assert compare(R(["v"], [(1000000.01,)]), R(["v"], [(1000000.0100000001,)])).match
    assert not compare(R(["v"], [(1000000.01,)]), R(["v"], [(1000000.02,)])).match


def test_ordered_comparison_is_strict_about_tie_order():
    """Documented behaviour: ties are the question's job (every ordered gold states its tie-break)."""
    g = R(["k", "n"], [("a", 5), ("b", 5)])
    assert not compare(g, R(["k", "n"], [("b", 5), ("a", 5)]), ordered=True).match
    assert compare(g, R(["k", "n"], [("b", 5), ("a", 5)]), ordered=False).match


def test_ordered_sequences_with_repeated_values():
    g = R(["n"], [(3,), (3,), (1,)])
    assert compare(g, R(["n"], [(3,), (3,), (1,)]), ordered=True).match
    assert not compare(g, R(["n"], [(3,), (1,), (3,)]), ordered=True).match


def test_fewer_columns_than_gold_never_matches():
    assert not compare(R(["a", "b"], [(1, 2)]), R(["a"], [(1,)])).match


def test_types_string_vs_number_never_equal():
    assert not compare(R(["v"], [(1,)]), R(["v"], [("1",)])).match
