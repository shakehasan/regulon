# AGENTS.md — Engineering Handbook

Conventions for everyone — human contributors and coding agents — working on Regulon.
The complete specification is [PLAN.md](PLAN.md); this file is the day-to-day rulebook.
When a detail is unspecified, choose the simplest option consistent with these conventions
and record it as an ADR.

## What this project is

Regulon is a solo-built, community-oriented open-source platform for **governed multi-agent RAG**:
LangGraph orchestration, hybrid retrieval with citations, layered routing, RBAC, hash-chained
audit trails, human-in-the-loop approval, and evaluation-gated CI. Default runtime is 100% local,
$0, no API keys. It exists so the GitHub community can run, study, and reuse a complete example
of multi-agent systems built to a regulated-industry bar.

## Repository map

| Path | What lives there | Notes |
|---|---|---|
| `PLAN.md` | Full build spec, milestones M0–M10 with acceptance criteria | Read the current milestone before coding |
| `config/` | **All** tunables: runtime YAML, safety denylist | Never hardcode a threshold, model name, weight, or budget |
| `src/regulon/core/` | Config, ids, events, errors, hashing, clock | Shared primitives; keep dependency-free of other subsystems |
| `src/regulon/<subsystem>/` | One directory per layer (see README architecture table) | Pydantic models at every boundary |
| `scripts/` | `public_safety_scan.py`, data tooling | Stdlib + PyYAML only for the scanner (runs in bare CI) |
| `tests/` | `unit/`, `integration/`, `adversarial/` | Markers: `integration`, `adversarial` |
| `docs/adr/` | Architecture decision records | `NNN-title.md`, one per significant choice |
| `docs/assets/` | Original diagrams (dark bg `#0B1020`, teal accent `#4FE3C1`) | Keep the visual identity consistent |
| `reports/` | Committed real-run artifacts | Generated only — never hand-edited |

## Commands

```bash
make setup     # venv + editable install + pre-commit hooks
make lint      # ruff check + format verification (line length 120)
make format    # auto-fix lint + formatting
make type      # mypy strict on src/ and scripts/
make test      # pytest with coverage gate >= 80%
make safety    # public-safety denylist scan
make eval      # hermetic eval gates (from M2)
make demo      # real-model demo via Ollama (from M4)
```

On Windows, run these from Git Bash; the Makefile handles the `.venv/Scripts` path itself.
Every gate above must be green before a PR is opened — CI runs the same set.

## Code style

- **Typed everything.** `mypy` is strict on `src/` and `scripts/`; keep it green. No `Any`
  escapes without a comment explaining why.
- **Pydantic v2 models at all module boundaries** — no bare dicts crossing module lines.
- **Small modules:** under 400 lines; split before you exceed it. Small functions over clever ones.
- **Docstrings** on public functions, Google style. State behavior and raised exceptions, not
  implementation history.
- **Errors:** everything Regulon raises derives from `regulon.core.errors.RegulonError`. Add
  subsystem subclasses in that subsystem. Graph nodes **fail closed**: guardrail uncertainty means
  escalate, never pass.
- **Configuration discipline:** thresholds, model names, weights, and budgets live in `config/`
  YAML with `REGULON_` env overrides. If you are typing a literal number that tunes behavior,
  stop and move it to config.
- **Determinism:** fix seeds in tests and the eval harness; version eval datasets; use the
  injectable `Clock` (`core/clock.py`) instead of calling `datetime.now()` in logic that gets
  tested or replayed.

## Testing

- Coverage ≥ 80% on `src/regulon` — enforced by `make test` and CI. Don't chase 100%; do cover
  every public function and every failure path that matters.
- Unit tests use the `deterministic` provider — never a network call, never a real model.
- At least one `integration`-marked test per milestone proving the milestone's acceptance path.
- Guardrail work requires `adversarial`-marked attack tests.
- If a test needs a denylisted string (to test the safety scanner), **build it by concatenation**
  so the repo itself stays clean — the scanner scans `tests/` too.

## Git workflow

- **One milestone = one branch = one PR.** Branch names: `<type>/mN-short-name`
  (e.g. `feat/m1-ingestion`). Never a single giant commit — commit in logical, reviewable units.
- [Conventional Commits](https://www.conventionalcommits.org/): `feat:` `fix:` `docs:` `test:`
  `chore:` `refactor:`.
- PR bodies follow the template: link the milestone, list changes, paste real verification output.
- Update `CHANGELOG.md` under `[Unreleased]` (Keep a Changelog) for user-visible changes.
- Do not start milestone N+1 until N's acceptance criteria pass with verification output.

## ADRs

Every architecturally significant choice gets `docs/adr/NNN-title.md`:

```markdown
# ADR-NNN: Title
- **Status:** Accepted | Superseded by ADR-MMM
- **Date:** YYYY-MM-DD
## Context      — the forces at play, why a decision is needed
## Decision     — what we chose, concretely
## Alternatives considered — each with why it lost
## Consequences — costs accepted, risks, follow-ups
```

"Significant" = affects more than one subsystem, is expensive to reverse, or pins a dependency,
schema, or threshold philosophy.

## Reports and metrics — the honesty rule

- Files in `reports/` are generated artifacts from **real runs**: each carries a generation
  timestamp, config hash (`Settings.config_hash()`), and machine spec.
- Regenerate rather than hand-edit. If a number cannot be produced by re-running the generating
  command, it does not get written — anywhere, including the README.
- Demo paths (`make demo`) always use a real local model. The `deterministic` provider exists
  only for hermetic CI and is named `deterministic` everywhere it appears.

## Public safety — non-negotiable

- No employer, client, or internal-system names. No proprietary data. No real personal data.
- Exactly two data sources: public-domain SEC EDGAR filings, and synthetic documents labeled
  `SYNTHETIC` in filename, frontmatter, and docs.
- Banned self-praise vocabulary in all docs (enforced by the scanner) — show capability through
  architecture, numbers, and reproducible commands, in a neutral engineer-to-engineer tone.
- `make safety` must pass; CI runs it on every push and weekly. Never weaken the denylist to make
  a change pass — fix the content. Exclusions require an ADR.

## Common pitfalls

- Editing a workflow or hook to get around a failing gate instead of fixing the cause.
- Adding a dependency that isn't OSS or that the default (local, $0) runtime would require.
- Returning a low-groundedness answer instead of revising/escalating — guardrails fail closed.
- Writing "example" metrics into docs — the honesty rule has no example exception.
- Calling `datetime.now()` directly in logic under test instead of taking a `Clock`.
- Letting a module grow past 400 lines because splitting felt like churn. Split it.
