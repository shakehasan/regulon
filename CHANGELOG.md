# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Evaluation stack design: **RAGAS** (standard RAG metrics) + an in-repo **G-Eval** rubric judge,
  both running on the local judge model, plus an in-repo **local run store** (`eval_runs.jsonl` +
  `regulon eval compare|history`) for cross-commit comparison. Every layer is free and offline —
  no hosted service, no account, no API key.
- `config/evals.yaml`: judge settings, dataset version, and every CI gate threshold declared as
  config (targets, not measured results), calibrated against real baselines in M7.
- ADR-009 recording the evaluation stack decision and why experiment tracking is built rather than
  bought.
- Unit tests guarding the eval config: gate ranges, G-Eval rubric weights summing to 1.0,
  deterministic judge settings, local-only run store, and a check that the config references no
  API key, token, endpoint, or account — the zero-cost promise enforced mechanically.
- Safety-scan rule rejecting commercial vendor names, so the repo stays vendor-neutral and free
  by construction rather than by vigilance.

### Changed

- README expanded: badges, table of contents, positioning, highlights, routing/governance/evaluation
  sections, quickstart, configuration reference, annotated project layout, FAQ.
- Architecture diagram: added a cross-cutting observability & eval-gates plane (OpenTelemetry ·
  Prometheus · Grafana — all open source).
- AGENTS.md rewritten as a full engineering handbook (repository map, commands, style, testing,
  git workflow, ADR template, honesty rules, common pitfalls).
- PLAN.md and ADR-001 reworded to state the community mission explicitly.

### Added

- M0 scaffold: repository layout, `pyproject.toml`, Makefile (`setup lint format type test eval demo safety`),
  pre-commit hooks, CI workflows (lint, types, tests with coverage gate, public-safety scan), release workflow.
- `scripts/public_safety_scan.py`: configurable denylist scanner that fails the build on sensitive patterns
  (emails, employer-ish phrases, secret-shaped tokens, SSN-like PII, banned self-praise terms).
- Core package skeleton: typed errors, ID generation, hashing helpers (audit-chain primitives), injectable
  clock, and YAML+env configuration with a stable config hash.
- Governance docs: LICENSE (MIT), CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, ROADMAP, AGENTS.md,
  issue/PR templates.
- ADR-001 (why Regulon / scope) and ADR-002 (local-first, real inference by default).
