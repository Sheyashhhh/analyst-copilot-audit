"""Tiny .env loader (no dependency) so Windows users never need shell-specific `export` syntax."""
from __future__ import annotations

import os

from .paths import ROOT


def load_dotenv() -> None:
    p = ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if v and not v.endswith("...") and k not in os.environ:
            os.environ[k] = v
