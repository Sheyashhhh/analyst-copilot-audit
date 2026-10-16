from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
DB_PATH = ROOT / "data" / "warehouse.duckdb"
SCHEMA_SQL = ROOT / "sql" / "schema.sql"
QUESTIONS_DIR = ROOT / "questions"
SEMANTIC_YAML = ROOT / "semantic" / "semantic_layer.yaml"
RESULTS_DIR = ROOT / "results"
CACHE_DIR = RESULTS_DIR / "cache"
RUNS_DIR = RESULTS_DIR / "runs"
IMG_DIR = ROOT / "docs" / "img"
