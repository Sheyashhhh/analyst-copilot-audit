"""Sandboxed, read-only SQL execution against the warehouse.

Defence in depth:
  1. The database is opened ``read_only=True``.
  2. External file access is disabled and the configuration is locked.
  3. Only a single SELECT / set-operation statement is accepted (parsed with sqlglot).
  4. Every query runs under a wall-clock timeout (``connection.interrupt``) and a row cap.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import sqlglot
from sqlglot import exp

from .paths import DB_PATH

MAX_ROWS = 10_000
DEFAULT_TIMEOUT_S = 30.0

_ALLOWED_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery)
_FORBIDDEN_FUNCS = {"read_csv", "read_csv_auto", "read_parquet", "read_json", "read_json_auto", "read_text",
                    "glob", "sniff_csv", "parquet_scan", "csv_scan"}


@dataclass
class ExecResult:
    status: str                      # ok | error | timeout | blocked
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    error: str | None = None
    error_kind: str | None = None    # syntax | schema | runtime | timeout | unsafe
    elapsed_ms: float = 0.0
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def validate_read_only(sql: str) -> str | None:
    """Return None if the statement is an acceptable single SELECT, otherwise a reason string."""
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except sqlglot.errors.ParseError as e:
        return f"parse: {str(e).splitlines()[0]}"
    if len(statements) != 1:
        return f"expected exactly one statement, got {len(statements)}"
    stmt = statements[0]
    if not isinstance(stmt, _ALLOWED_ROOTS):
        return f"only SELECT statements are allowed (got {type(stmt).__name__})"
    for fn in stmt.find_all(exp.Anonymous, exp.Func):
        name = (fn.name or "").lower() if hasattr(fn, "name") else ""
        if name in _FORBIDDEN_FUNCS:
            return f"function {name}() is not allowed"
    return None


class Executor:
    def __init__(self, db_path: Path = DB_PATH, timeout_s: float = DEFAULT_TIMEOUT_S, max_rows: int = MAX_ROWS):
        self.db_path = Path(db_path)
        self.timeout_s = timeout_s
        self.max_rows = max_rows
        self._con = duckdb.connect(str(self.db_path), read_only=True)
        # DuckDB shares one instance per file within a process, so a second Executor sees the
        # configuration already locked by the first. That is the state we want, so tolerate it.
        try:
            self._con.execute("SET enable_external_access = false")
            self._con.execute("SET lock_configuration = true")
        except duckdb.InvalidInputException as e:
            if "locked" not in str(e):
                raise

    def close(self) -> None:
        self._con.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def run(self, sql: str, timeout_s: float | None = None) -> ExecResult:
        reason = validate_read_only(sql)
        if reason and not reason.startswith("parse:"):
            return ExecResult(status="blocked", error=reason, error_kind="unsafe")
        # Parse failures fall through so DuckDB reports its own (more useful) syntax error.
        cur = self._con.cursor()
        timer = threading.Timer(timeout_s or self.timeout_s, cur.interrupt)
        t0 = time.perf_counter()
        timer.start()
        try:
            rel = cur.execute(sql)
            cols = [d[0] for d in rel.description]
            rows = rel.fetchmany(self.max_rows + 1)
            truncated = len(rows) > self.max_rows
            return ExecResult("ok", cols, [tuple(r) for r in rows[: self.max_rows]],
                              elapsed_ms=(time.perf_counter() - t0) * 1000, truncated=truncated)
        except duckdb.InterruptException:
            return ExecResult("timeout", error="query exceeded time limit", error_kind="timeout",
                              elapsed_ms=(time.perf_counter() - t0) * 1000)
        except duckdb.ParserException as e:
            return self._err(e, "syntax", t0)
        except (duckdb.BinderException, duckdb.CatalogException) as e:
            return self._err(e, "schema", t0)
        except duckdb.Error as e:
            return self._err(e, "runtime", t0)
        finally:
            timer.cancel()
            cur.close()

    @staticmethod
    def _err(e: Exception, kind: str, t0: float) -> ExecResult:
        msg = str(e).strip().splitlines()[0][:300]
        return ExecResult("error", error=msg, error_kind=kind, elapsed_ms=(time.perf_counter() - t0) * 1000)
