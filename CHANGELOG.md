# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
