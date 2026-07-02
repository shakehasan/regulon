# ADR-002: Local-first runtime, real inference by default

- **Status:** Accepted
- **Date:** 2026-07-02

## Context

Two failure modes are common in public LLM-platform repos:

1. The demo needs paid API keys, so most visitors never run it.
2. The demo runs "canned" responses, so what visitors see is theater — and any published metrics
   are unverifiable.

Regulon's credibility rests on reproducibility: anyone should be able to clone the repo and watch
a real multi-agent run with real inference, and every published number must be regenerable by a
command.

## Decision

- **Local-first:** the default runtime requires zero paid services, zero API keys, zero accounts.
  Storage defaults to SQLite (+numpy for vectors); Postgres+pgvector, Celery+Redis, and
  Prometheus+Grafana are optional Docker profiles.
- **Real inference by default:** `make demo` runs a real local LLM via Ollama (default model
  pinned in one config constant; a low-RAM alternative is documented). Demo output is never
  mocked.
- **`deterministic` provider for CI only:** a seeded, fixture-driven provider exists solely so
  unit/integration tests and hermetic eval gates run without a model server. It is named
  `deterministic` everywhere it appears; it is never the default and never used in demos or
  committed reports.
- **No fabricated metrics:** every number in `reports/` comes from re-running the generating
  command and carries a generation timestamp, config hash, and machine spec. If a number cannot
  be produced by running the code, it is not written down.
- Cloud providers (`openai`, `anthropic`, `bedrock`, `azure-openai`) are optional adapters that
  activate only when the corresponding environment key is present.

## Alternatives considered

1. **Cloud-model default with a free-tier note.** Better model quality, but breaks
   "clone-and-run" for anyone without keys and couples demos to third-party pricing.
2. **Mock/canned demo path.** Zero-setup, but it is exactly the theater this project exists to
   avoid, and it would make Non-Negotiable #4 unenforceable.
3. **Real-model tests in CI.** Maximally honest but slow, flaky, and infeasible on hosted
   runners; instead CI is hermetic (deterministic provider) while `make eval-real` produces the
   committed real-run reports.

## Consequences

- Demo quality is bounded by small local models; acceptable for a reference platform, and the
  gateway makes stronger models a config change.
- CI cannot catch real-model regressions; mitigated by committed `eval-real` reports regenerated
  per release and diffed in review.
- Two provider paths (real + deterministic) must stay behavior-compatible behind one interface;
  the gateway ADR (M3) defines that contract.
- Contributors need Ollama for demo work but not for tests, lint, types, or hermetic evals.
