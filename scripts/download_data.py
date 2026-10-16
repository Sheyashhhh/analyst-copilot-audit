"""Fetch the Olist CSVs into data/raw (kept for backwards compatibility; `python -m copilot_audit setup` does this too)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from copilot_audit.setup import download_data  # noqa: E402

if __name__ == "__main__":
    download_data()
