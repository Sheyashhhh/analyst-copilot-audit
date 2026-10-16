"""One-command setup and environment check; pure Python so it behaves the same on Windows, macOS and Linux."""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from .paths import DB_PATH, RAW_DIR, ROOT
from .warehouse import REQUIRED_FILES

RAW_BASE = "https://raw.githubusercontent.com/vikashGiri27/SQL-E-Commerce-Data-Analysis/main/Database/"
GIT_MIRROR = "https://github.com/vikashGiri27/SQL-E-Commerce-Data-Analysis.git"


def _fetch(name: str) -> None:
    dest = RAW_DIR / name
    tmp = dest.with_suffix(".part")
    req = urllib.request.Request(RAW_BASE + name, headers={"User-Agent": "copilot-audit-setup"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    tmp.replace(dest)


def download_data(force: bool = False) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    todo = [f for f in REQUIRED_FILES if force or not (RAW_DIR / f).exists()]
    if not todo:
        print("raw data already present")
        return
    try:
        for i, name in enumerate(todo, 1):
            print(f"  [{i}/{len(todo)}] downloading {name}")
            _fetch(name)
        return
    except (urllib.error.URLError, OSError) as e:
        print(f"  direct download failed ({e}); trying git clone")
    if not shutil.which("git"):
        raise SystemExit(
            "Could not download the data and git is not installed.\n"
            "Manual option: download the Olist dataset from https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce\n"
            f"and unzip these files into {RAW_DIR}:\n  " + "\n  ".join(REQUIRED_FILES))
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["git", "clone", "--depth", "1", "-q", GIT_MIRROR, tmp], check=True)
        for name in todo:
            shutil.copy(Path(tmp) / "Database" / name, RAW_DIR / name)


def setup() -> None:
    from . import report, trap_impact, warehouse
    print("1/3 data");      download_data()
    print("2/3 warehouse"); warehouse.build()
    print("3/3 trap measurements and figures")
    o = trap_impact.measure()
    report.figures_trap_impact()
    a = o["classifier_agreement"]
    print(f"\nReady. {o['n_questions']} questions, {o['n_trap_queries']} trap queries all change the answer: "
          f"{o['all_traps_change_the_answer']}; classifier agreement {a['agree']}/{a['total']}.")
    print("Next: `python -m copilot_audit doctor`, then `python -m pytest -q`, then add your API key (see README) and run `python -m copilot_audit smoke`.")


def doctor() -> int:
    ok = True

    def line(good: bool, msg: str, fix: str = "") -> None:
        nonlocal ok
        ok &= good
        print(("  OK    " if good else "  FIX   ") + msg + (f"\n          -> {fix}" if not good and fix else ""))

    print("Environment check")
    line(sys.version_info >= (3, 10), f"Python {sys.version.split()[0]} (need 3.10+)", "install Python 3.10 or newer from python.org")
    for mod in ["duckdb", "pandas", "numpy", "yaml", "sqlglot", "matplotlib", "anthropic", "pytest"]:
        try:
            importlib.import_module(mod)
            line(True, f"package {mod}")
        except ImportError:
            line(False, f"package {mod} missing", "python -m pip install -r requirements.txt")
    have = [f for f in REQUIRED_FILES if (RAW_DIR / f).exists()]
    line(len(have) == len(REQUIRED_FILES), f"raw data {len(have)}/{len(REQUIRED_FILES)} files", "python -m copilot_audit setup")
    line(DB_PATH.exists(), "warehouse built", "python -m copilot_audit setup")
    try:
        probe = ROOT / "results" / ".write_test"
        probe.parent.mkdir(exist_ok=True)
        probe.write_text("x", encoding="utf-8"); probe.unlink()
        line(True, "results/ is writable")
    except OSError:
        line(False, "results/ is not writable", "move the project out of a protected folder")
    key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    print(("  OK    " if key else "  --    ") + "ANTHROPIC_API_KEY " + ("found" if key else
          "not set (only needed for `smoke` / `eval`; everything else runs without it)"))
    print("\nAll required checks passed." if ok else "\nFix the items marked FIX, then run this again.")
    return 0 if ok else 1
