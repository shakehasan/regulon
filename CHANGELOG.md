# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `quorum ingest <path>` (`src/quorum/cli/`): builds a SQLite knowledge base from a file or a
  directory and reports documents ingested, chunks created, and redactions applied — the M1
  acceptance path. `--db` chooses the database, `--json` prints the report model instead of a
  summary, and unparseable files are reported as skipped rather than failing the run.
- Ingestion pipeline (`src/quorum/ingestion/pipeline.py`) fixing the stage order
  `load → redact → chunk → store`. No un-redacted text reaches the knowledge base, not even
  transiently. Because a placeholder is rarely the length of the text it replaces, the pipeline
  remaps section offsets through the redaction events and recomputes the document id from the
  redacted text, so every stored offset indexes the text the store actually holds.
- Ingestion contract (`src/quorum/ingestion/models.py`): frozen Pydantic models exchanged by every
  stage — `NormalizedDocument`, `Section`, `Chunk`, `ChunkMetadata`, `RedactionEvent`,
  `IngestReport` — plus deterministic content-hash id builders, so the same document ingested twice
  on two machines yields identical ids.
- Document loaders (`src/quorum/ingestion/loaders.py`) for text, Markdown, HTML, and PDF. Each
  returns normalized text with a `Section` map that tiles the document exactly, Markdown YAML front
  matter parsed into filing metadata, and HTML parsed with the standard library only.
- Deterministic PII redaction (`src/quorum/ingestion/redaction.py`) for email, phone, and
  SSN-shaped strings, applied before anything is stored. Precision-biased on purpose — a bare run
  of digits is never read as a phone number, so financial figures are not corrupted — with every
  replacement recorded as an auditable offset span rather than by storing what was removed.
- Section-aware chunker (`src/quorum/ingestion/chunking.py`): headings bound chunks, short sections
  merge forward, and text is packed to a character budget breaking at paragraph, then sentence, then
  word boundaries. Guarantees `document.text[chunk.start_char:chunk.end_char] == chunk.text` for
  every chunk, which is what makes a citation replayable and checkable.
- SQLite chunk store (`src/quorum/ingestion/store.py` + `store_sql.py`) behind a `ChunkStore`
  protocol: one transaction per batch, foreign keys enforced, idempotent re-ingest, and a schema
  version that refuses to misread a newer database.
- SEC EDGAR client (`src/quorum/ingestion/edgar.py`) and `scripts/fetch_edgar_sample.py`: ticker →
  CIK lookup, filing listing, and filing fetch over the standard library, with the configured
  User-Agent and a courtesy rate limit. Network access is injectable, so no test ever makes a
  request.
- `scripts/gen_synthetic_corpus.py` and the generated `data/samples/` corpus: fictional companies
  and invented figures, `SYNTHETIC_`-prefixed and labeled in front matter, byte-identical for a
  fixed seed. Regenerate rather than hand-edit — a test fails if the committed corpus drifts.
- `ingestion` settings in `config/quorum.yaml`: chunking bounds, redaction placeholder, and EDGAR
  client settings. No chunk size, threshold, or timeout is hardcoded.
- ADR-003 recording the chunking strategy: why section-aware rather than fixed-size splitting, why
  character budgets rather than token budgets, why exact offsets and ingest-time redaction — and a
  plain statement that the chosen parameters are starting points no retrieval benchmark has
  validated yet.
- `.github/CODEOWNERS` so every change requires review before merge.
- Real, computed coverage badge: `scripts/gen_coverage_badge.py` + `make badge-coverage`
  regenerate `.github/badges/coverage.json` from an actual `coverage report` after `make test` —
  never a hand-typed number, same rule as everything in `reports/`.
- `make ci` target that chains `lint type test safety` in the same order CI runs them, so the
  full gate can be reproduced locally in one command.
- New CI job `pre-commit (full repo)`: runs every hook in `.pre-commit-config.yaml` against the
  entire tracked tree on every push and PR, not just files a contributor happened to stage.

- `docs/GLOSSARY.md`: plain-language definitions for every term of art used in this repo
  (retrieval, agents, routing, governance, evaluation, observability, process), linked from the
  README — this project is meant to be learned from, not just run.
- `.editorconfig` for consistent indentation, charset, and line endings across editors.
- `.github/dependabot.yml`: automated weekly update PRs for Python dependencies and GitHub
  Actions versions (native GitHub feature, no third-party service, no cost).
- `CITATION.cff` so the project can be cited or linked back to in coursework, tutorials, or
  derivative projects — never required, since the MIT license already permits free reuse.
- Evaluation stack design: **RAGAS** (standard RAG metrics) + an in-repo **G-Eval** rubric judge,
  both running on the local judge model, plus an in-repo **local run store** (`eval_runs.jsonl` +
  `quorum eval compare|history`) for cross-commit comparison. Every layer is free and offline —
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
- M0 scaffold: repository layout, `pyproject.toml`, Makefile (`setup lint format type test eval demo safety`),
  pre-commit hooks, CI workflows (lint, types, tests with coverage gate, public-safety scan), release workflow.
- `scripts/public_safety_scan.py`: configurable denylist scanner that fails the build on sensitive patterns
  (emails, employer-ish phrases, secret-shaped tokens, SSN-like PII, banned self-praise terms).
- Core package skeleton: typed errors, ID generation, hashing helpers (audit-chain primitives), injectable
  clock, and YAML+env configuration with a stable config hash.
- Governance docs: LICENSE (MIT), CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, ROADMAP, AGENTS.md,
  issue/PR templates.
- ADR-001 (why Quorum / scope) and ADR-002 (local-first, real inference by default).

### Changed

- README expanded: badges, table of contents, positioning, highlights, routing/governance/evaluation
  sections, quickstart, configuration reference, annotated project layout, FAQ.
- Architecture diagram: added a cross-cutting observability & eval-gates plane (OpenTelemetry ·
  Prometheus · Grafana — all open source).
- AGENTS.md rewritten as a full engineering handbook (repository map, commands, style, testing,
  git workflow, ADR template, honesty rules, common pitfalls).
- PLAN.md and ADR-001 reworded to state the community mission explicitly.
