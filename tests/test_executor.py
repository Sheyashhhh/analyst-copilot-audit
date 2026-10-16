import pytest


@pytest.mark.parametrize("sql", ["DROP TABLE fact_orders", "DELETE FROM fact_orders", "INSERT INTO dim_seller VALUES (1,'a','b','c','d')",
                                 "CREATE TABLE x AS SELECT 1", "COPY fact_orders TO '/tmp/x.csv'", "SELECT 1; SELECT 2"])
def test_writes_and_multistatement_blocked(executor, sql):
    assert executor.run(sql).status == "blocked"


def test_external_files_denied(executor):
    r = executor.run("SELECT * FROM read_csv('/etc/passwd')")
    assert not r.ok


def test_error_kinds(executor):
    assert executor.run("SELECT nope FROM fact_orders").error_kind == "schema"
    assert executor.run("SELECT * FROM no_such_table").error_kind == "schema"
    assert executor.run("selec 1").error_kind == "syntax"


def test_timeout(executor):
    r = executor.run("SELECT count(*) FROM range(10000000000) a, range(100000) b", timeout_s=1)
    assert r.status == "timeout"


def test_database_is_read_only(executor):
    assert executor.run("SELECT count(*) FROM fact_orders").rows[0][0] == 99441
