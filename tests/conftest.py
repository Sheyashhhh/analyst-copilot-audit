import pytest

from copilot_audit.executor import Executor
from copilot_audit.paths import DB_PATH
from copilot_audit.questions import load_questions


@pytest.fixture(scope="session")
def executor():
    if not DB_PATH.exists():
        pytest.skip("warehouse not built: run `make build`")
    with Executor() as ex:
        yield ex


@pytest.fixture(scope="session")
def questions():
    return load_questions()


@pytest.fixture(scope="session")
def gold(executor, questions):
    return {q.id: executor.run(q.gold_sql) for q in questions}
