# Regulon developer entrypoints.
# Every target works on Linux, macOS, and Windows (Git Bash / GNU make).

.DEFAULT_GOAL := help

ifeq ($(OS),Windows_NT)
SYS_PY   := python
VENV_BIN := .venv/Scripts
else
SYS_PY   := python3
VENV_BIN := .venv/bin
endif

PY := $(VENV_BIN)/python

.PHONY: help setup lint format type test eval eval-real demo safety clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

setup: ## Create venv and install regulon with dev dependencies
	$(SYS_PY) -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev]"
	$(PY) -m pre_commit install

lint: ## Run ruff checks and formatting verification
	$(PY) -m ruff check src tests scripts
	$(PY) -m ruff format --check src tests scripts

format: ## Auto-fix lint and formatting issues
	$(PY) -m ruff check --fix src tests scripts
	$(PY) -m ruff format src tests scripts

type: ## Run mypy (strict on src/ and scripts/)
	$(PY) -m mypy

test: ## Run test suite with coverage gate (>= 80%)
	$(PY) -m pytest --cov --cov-report=term-missing --cov-fail-under=80

safety: ## Scan the repo for public-safety violations
	$(PY) scripts/public_safety_scan.py

eval: ## Hermetic eval suites with hard gates (arrives with M2; full program in M7)
	@echo "make eval: eval suites land in M2 (retrieval) and M7 (full program). Nothing to run yet."

eval-real: ## Real-model eval producing committed reports (arrives in M7)
	@echo "make eval-real: real-model evaluation lands in M7. Nothing to run yet."

demo: ## End-to-end demo with a real local model via Ollama (arrives in M4)
	@echo "make demo: the research demo lands in M4. Nothing to run yet."

clean: ## Remove build artifacts and caches
	rm -rf build dist *.egg-info .mypy_cache .ruff_cache .pytest_cache .coverage htmlcov
