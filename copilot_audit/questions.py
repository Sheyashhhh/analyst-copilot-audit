from __future__ import annotations

import glob
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .paths import QUESTIONS_DIR

FAMILIES = ["control", "soft_delete", "status_case", "fanout", "date_boundary", "timezone", "definition", "entity_grain"]
DIFFICULTIES = {"easy", "medium", "hard"}


MONEY_RE = re.compile(r"\b(price|freight_value|payment_value|order_payment_total)\b", re.I)


def risk_weight(gold_sql: str, metric: str | None) -> int:
    """Business-impact weight of a wrong answer: 3 for money/registered-metric questions, 1 otherwise.
    A deliberately simple, inspectable heuristic; change it here if your cost of error differs."""
    return 3 if metric or MONEY_RE.search(gold_sql) else 1


@dataclass
class Question:
    id: str
    family: str
    difficulty: str
    question: str
    gold_sql: str
    ordered: bool = False
    metric: str | None = None
    grain: str | None = None
    naive_sql: str | None = None
    expected_failure: str | None = None
    alternates: list[str] = field(default_factory=list)

    @property
    def is_trap(self) -> bool:
        return self.family != "control"

    @property
    def weight(self) -> int:
        return risk_weight(self.gold_sql, self.metric)

    @property
    def dataset(self) -> str:
        return "holdout" if self.id.startswith("H") else "dev"


def load_questions(directory: Path = QUESTIONS_DIR) -> list[Question]:
    alts = yaml.safe_load((Path(directory) / "alternates.yaml").read_text(encoding="utf-8")) or {}
    out: list[Question] = []
    for f in sorted(glob.glob(str(Path(directory) / "0*.yaml"))):
        for d in yaml.safe_load(Path(f).read_text(encoding="utf-8")):
            q = Question(id=d["id"], family=d["family"], difficulty=d["difficulty"], question=d["question"],
                         gold_sql=d["gold_sql"].strip(), ordered=d.get("ordered", False), metric=d.get("metric"),
                         grain=d.get("grain"), naive_sql=(d.get("naive_sql") or "").strip() or None,
                         expected_failure=d.get("expected_failure"),
                         alternates=[a.strip() for a in alts.get(d["id"], [])])
            out.append(q)
    return out


@dataclass
class HoldoutQuestion:
    id: str
    family: str
    difficulty: str
    question: str
    gold_sql: str
    ordered: bool = False
    metric: str | None = None
    grain: str | None = None
    wrong: list[dict] = field(default_factory=list)       # {sql, expected_failure, evasive}
    correct: list[str] = field(default_factory=list)      # correct SQL written in unusual styles


def load_holdout(directory: Path = QUESTIONS_DIR) -> list[HoldoutQuestion]:
    raw = yaml.safe_load((Path(directory) / "holdout.yaml").read_text(encoding="utf-8"))
    return [HoldoutQuestion(id=d["id"], family=d["family"], difficulty=d["difficulty"], question=d["question"],
                            gold_sql=d["gold_sql"].strip(), ordered=d.get("ordered", False), metric=d.get("metric"),
                            grain=d.get("grain"),
                            wrong=[{**w, "sql": w["sql"].strip()} for w in d.get("wrong", [])],
                            correct=[c.strip() for c in d.get("correct", [])]) for d in raw]


def holdout_as_questions() -> list[Question]:
    """Holdout questions in evaluable form (no trap query; the correct variants act as alternates)."""
    return [Question(id=h.id, family=h.family, difficulty=h.difficulty, question=h.question, gold_sql=h.gold_sql,
                     ordered=h.ordered, metric=h.metric, grain=h.grain, alternates=h.correct) for h in load_holdout()]
