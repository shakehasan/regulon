# AGENTS.md — Engineering Conventions

Conventions for everyone (and every coding agent) contributing to Regulon. The full project
specification is [PLAN.md](PLAN.md); this file is the day-to-day rulebook. When a detail is
unspecified, choose the simplest option consistent with these conventions and record it as an ADR.

## Typing and style

- Typed everything. `mypy` runs strict on `src/` and `scripts/`; keep it green.
- Pydantic v2 models at all module boundaries — no bare dicts across module lines.
- `ruff` clean (check + format), line length 120.
- Docstrings on public functions, Google style.
- Small modules: keep files under 400 lines; split before you exceed it.

## Testing

- Coverage ≥ 80% on `src/regulon`; the gate is enforced in `make test` and CI.
- Unit tests use the `deterministic` provider — never a network or a real model.
- At least one integration test per milestone (marker: `integration`).
- Adversarial tests for guardrails (marker: `adversarial`).
- Determinism: fix seeds in tests and the eval harness; version eval datasets.

## Configuration

- All thresholds, model names, weights, and budgets live in `config/` YAML with env overrides
  (`REGULON_` prefix). No magic numbers in code.

## Errors

- Use the custom exception hierarchy rooted at `regulon.core.errors.RegulonError`.
- Graph nodes fail closed: guardrail uncertainty means escalate, never pass.

## Commits, branches, PRs

- Conventional Commits: `feat:`, `fix:`, `docs:`, `test:`, `chore:`, `refactor:`.
- One milestone = one branch = one PR with a descriptive body. Never a single giant commit.
- Update `CHANGELOG.md` under `[Unreleased]` for user-visible changes.

## ADRs

- Every architecturally significant choice gets `docs/adr/NNN-title.md` with sections:
  Context / Decision / Alternatives considered / Consequences.

## Reports and metrics

- Files in `reports/` are generated artifacts from real runs. Each carries a generation timestamp,
  config hash, and machine spec. Regenerate rather than hand-edit. **Never fabricate a number.**

## Public safety (non-negotiable)

- No employer, client, or internal-system names; no proprietary data; no real personal data.
- Data sources are limited to public SEC EDGAR filings and synthetic documents labeled `SYNTHETIC`.
- Banned self-praise vocabulary in docs (enforced by the safety scan): show capability through
  architecture, numbers, and reproducible commands instead.
- `make safety` must pass; CI runs it on every push.
