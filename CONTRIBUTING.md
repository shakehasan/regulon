# Contributing to Regulon

Thanks for your interest in contributing. Regulon is built milestone by milestone against a public
specification ([PLAN.md](PLAN.md)); contributions should fit the current milestone or the
[roadmap](ROADMAP.md).

## Development setup

Requirements: Python 3.11+, GNU make, git. (Ollama is needed from M3 onward for real-inference paths.)

```bash
git clone https://github.com/shakehasan/regulon.git
cd regulon
make setup      # venv + editable install + pre-commit hooks
make lint type test
```

## Quality bar

Every PR must pass locally before review:

- `make lint` — ruff check and formatting, line length 120.
- `make type` — mypy strict on `src/` and `scripts/`.
- `make test` — pytest with coverage ≥ 80% on `src/regulon`.
- `make safety` — the public-safety scan (see below).

Conventions are collected in [AGENTS.md](AGENTS.md). Highlights:

- Typed everything; Pydantic models at module boundaries; no bare dicts across module lines.
- Small modules (< 400 lines); docstrings on public functions.
- Thresholds, model names, weights, and budgets live in `config/` YAML — no magic numbers in code.
- Architecturally significant choices get an ADR in `docs/adr/NNN-title.md`.

## Commits and PRs

- [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`, `test:`,
  `chore:`, `refactor:`.
- One logical change per PR with a descriptive body; link the issue it closes.
- Update `CHANGELOG.md` (Keep a Changelog format) under `[Unreleased]` for user-visible changes.

## Public-safety rules (non-negotiable)

This repository is public-safe by construction:

- No employer, client, or internal-system names. No proprietary data. No real personal data.
- Only two data sources: public-domain SEC EDGAR filings and synthetic documents clearly labeled
  `SYNTHETIC` in filename, frontmatter, and docs.
- Never fabricate metrics. Numbers in `reports/` must come from re-running the generating command.
- `scripts/public_safety_scan.py` enforces a denylist in CI; do not weaken it to make a PR pass —
  fix the content instead.

## Reporting issues

Use the issue templates. For security problems, follow [SECURITY.md](SECURITY.md) instead of opening
a public issue.
