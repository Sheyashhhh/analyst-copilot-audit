PY ?= python

.PHONY: help setup data build test traps selftest smoke eval report all clean

help:
	@echo "make setup     install dependencies"
	@echo "make data      download the Olist CSVs into data/raw"
	@echo "make build     build the DuckDB star schema (with traps)"
	@echo "make test      run the test suite (80 tests)"
	@echo "make traps     measure trap impact, classifier agreement and guardrail behaviour (no LLM, no API key)"
	@echo "make selftest  exercise the harness with scripted providers"
	@echo "make smoke     10-question end-to-end LLM run (needs ANTHROPIC_API_KEY)"
	@echo "make eval      full evaluation: 80 questions x C0..C3 (needs ANTHROPIC_API_KEY)"
	@echo "make report    render results/REPORT.md and figures from model runs"

setup:
	$(PY) -m pip install -r requirements.txt

data:
	$(PY) scripts/download_data.py

build:
	$(PY) -m copilot_audit build

test:
	$(PY) -m pytest -q

traps:
	$(PY) -m copilot_audit traps

selftest:
	$(PY) -m copilot_audit selftest
	$(PY) -m copilot_audit report selftest-oracle selftest-traps

smoke:
	$(PY) -m copilot_audit run --limit 10 --conditions C0 C2 --tag smoke

eval:
	$(PY) -m copilot_audit run --conditions C0 C1 C2 C3 --trials 1

report:
	$(PY) -m copilot_audit report

all: data build test traps selftest

clean:
	rm -rf data/warehouse.duckdb results/cache .pytest_cache
