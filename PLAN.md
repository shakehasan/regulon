# REGULON — Project Specification & Master Build Plan

This document is the complete engineering specification for Regulon: scope, architecture, quality bar, and an ordered milestone plan with acceptance criteria. Build strictly milestone by milestone. Do not skip acceptance criteria. Do not take shortcuts that violate the Non-Negotiables. When a detail is unspecified, choose the simplest option consistent with the Engineering Conventions and record it as an ADR.

---

## 1. Project Identity

- **Repo name:** `regulon`
- **Tagline (repo description):** "Governed multi-agent RAG platform: LangGraph agent orchestration, hybrid retrieval with cross-encoder reranking, adaptive multi-mode routing, RL-tuned route optimization, HITL approvals, RBAC, audit trails, and evaluation-gated CI. Local-first, open source."
- **One-liner:** Regulon is an open-source reference platform for running multi-agent LLM systems the way regulated industries need them run — every answer grounded and cited, every decision routed and traced, every risky action approved by a human, every release gated by evals.
- **Flagship reference app:** *Research Desk* — a multi-agent investment research workflow that ingests public SEC filings and produces citation-backed research briefs, with human approval required before a brief is finalized.
- **License:** MIT. Copyright (c) 2026 **Shake MD Tareq Hasan**. Use this exact full name in `LICENSE`, `pyproject.toml` authors, package metadata, and the README author line.
- **Author:** Shake MD Tareq Hasan (GitHub: shakehasan).
- **Naming note:** "Regulon" (a biology term: a set of genes governed as one unit) = a set of agents governed by one control plane. If the owner prefers a different name, it is a single find-replace; alternates considered: `aegis-agents`, `governet`, `praxa`.

## 2. Non-Negotiables (violating any of these fails the build)

1. **Public-safe.** The repository must never contain: any employer name, any bank or financial-institution client name, any internal system name, any proprietary data, any real customer or colleague name, any statement that this software is or was used in production at any specific firm. Position everything as an original open-source reference implementation.
2. **Data hygiene.** Only two data sources are permitted: (a) public-domain SEC EDGAR filings fetched via the public API or bundled as excerpts, and (b) synthetic documents that are clearly labeled `SYNTHETIC` in filename, frontmatter, and docs. A CI script (`scripts/public_safety_scan.py`) must scan the repo for a configurable denylist of sensitive patterns (person emails, employer-ish phrases like "my company", internal-system-sounding tokens, API keys, real PII) and fail the build on hits.
3. **Fully open source.** Every dependency is OSS. Default runtime requires zero paid services, zero API keys, zero accounts.
4. **Real inference by default in demos.** The demo path (`make demo`) must run a real local LLM via Ollama — never a canned/mock response. A deterministic provider exists ONLY for hermetic CI tests and is clearly named `deterministic` everywhere. All committed eval reports, latency tables, and cost tables must come from real runs. **Never fabricate metrics.** If a number cannot be produced by running the code, do not write the number.
5. **Not advice.** README and app output carry a disclaimer: research/education tool; not investment advice.
6. **No self-praise language.** Banned words in all docs: "world-class", "production-grade", "state-of-the-art", "not a toy", "stands out". Show capability through architecture, numbers, and reproducible commands. Neutral, specific, engineer-to-engineer tone.

## 3. Experience Goals (the bar for anyone who lands on this repo)

- **Engineer who clones it:** `make setup && make demo` → a real multi-agent run with a real model, citations, traces, and an approval step in under 10 minutes; readable typed code; tests, lint, types, and eval gates in CI; committed eval report with real numbers.
- **Maintainer's record:** ADRs documenting tradeoffs, semver releases with CHANGELOG, a public roadmap, issue/PR discipline, a postmortem — evidence of sustained ownership over time, not a code dump.
- **First-time reader:** the README top explains the project in 60 seconds — one paragraph, one architecture diagram, one demo GIF, badges, quickstart. Topics surfaced naturally: LangGraph, multi-agent, agentic RAG, hybrid retrieval, pgvector, cross-encoder reranking, MCP, FastAPI, RBAC, HITL, RAGAS, OpenTelemetry, Docker, Kubernetes, reinforcement learning.

## 4. Product Specification

### 4.1 Core user flow (Research Desk)
1. **Ingest:** user ingests one or more SEC filings (bundled samples or fetched from EDGAR by ticker/form) plus optional synthetic docs into a knowledge base.
2. **Ask:** user submits a research task, e.g., "Summarize revenue drivers and stated risk factors for the bundled 10-K; compare to prior year."
3. **Orchestrate:** a LangGraph supervisor plans the task, routes sub-tasks to specialist agents, each grounded in retrieved, cited evidence.
4. **Guard:** input guardrails (prompt-injection, PII) and output guardrails (groundedness threshold, citation-required, restricted-topic policy) run as graph nodes.
5. **Approve (HITL):** the finished brief lands in an approval queue with status `pending_review`. A `reviewer` role approves or rejects with a reason. Only approved briefs are marked `final`. Every decision is recorded as feedback.
6. **Audit:** every model call, route decision, retrieval, guardrail verdict, approval, and config hash is written to a tamper-evident audit log (hash-chained JSONL).
7. **Learn:** approval/rejection + eval scores become reward signals; an offline RL trainer updates routing preferences (Section 4.5).

### 4.2 Agents (LangGraph supervisor pattern)
- `supervisor` — plans, decomposes, routes, aggregates, enforces loop/step budgets.
- `retriever` — query rewriting + hybrid retrieval + reranking; returns evidence bundles with source spans.
- `analyst` — numeric/tabular reasoning over retrieved evidence (revenue deltas, segment comparisons); tool-calls a safe calculator and table extractor.
- `writer` — drafts the brief strictly from evidence bundles; inline citation markers `[S1]`, `[S2]`.
- `critic` — checks claim-evidence alignment, flags uncited claims, triggers one bounded revision loop.
- `compliance` — runs policy engine + redaction on the final draft; can force HITL escalation.
State is a typed Pydantic model; every node emits structured events; graph is compiled with explicit conditional edges; recursion/step limits enforced.

### 4.3 RAG pipeline (must include ALL of these)
Ingestion (HTML/PDF/text/MD → normalized sections) → semantic-aware chunking with overlap and metadata (ticker, form, fiscal year, section) → dual index:
- **Dense:** `sentence-transformers` embeddings (`BAAI/bge-small-en-v1.5` or current equivalent), stored in SQLite+numpy by default, **Postgres + pgvector** under the `pgvector` Docker profile (same interface, swappable store).
- **Sparse:** BM25 (`rank-bm25`).
Query path: query rewrite (multi-query) → parallel dense + sparse retrieval → **Reciprocal Rank Fusion** → **cross-encoder reranking** (`cross-encoder/ms-marco-MiniLM-L-6-v2` or current equivalent) → relevance grading → evidence bundle with exact source spans and stable citation IDs. Groundedness verifier scores final answers against bundles; below-threshold answers are revised or escalated, never silently returned.

### 4.4 Multi-mode routing (a headline pillar — make it visibly excellent)
A `routing/` subsystem with layered strategies, each independently testable:
1. **Rule routing** — deterministic intent → agent/model table (YAML-configured).
2. **Semantic routing** — embedding similarity to route exemplars when rules miss.
3. **Cost/latency-aware model routing** — model gateway holds a registry of models with capability tags, context limits, measured latency, and $/1K-token cost (0 for local); router picks cheapest model satisfying task requirements; per-request and per-run budget caps.
4. **Policy routing** — sensitive-topic tasks forced to stricter pipelines (higher groundedness threshold + mandatory HITL).
5. **Fallback chains** — timeout/error/low-confidence cascades to next candidate; all hops recorded.
6. **Semantic cache** — embedding-similarity cache for repeated queries with hit/miss metrics and measured cost savings.
Model gateway providers: `ollama` (default), `openai`, `anthropic`, `bedrock`, `azure-openai` (env-key optional), `deterministic` (CI only). Every route decision emits a `RouteDecision` record: candidates, scores, chosen arm, reason, cost estimate.

### 4.5 RL-tuned routing (differentiator — implement carefully, scope tightly)
- **Feedback store:** every run logs (task features, route decision, eval scores, human approve/reject, latency, cost).
- **Reward:** `R = w1·eval_composite + w2·human_feedback − w3·normalized_cost − w4·normalized_latency` (weights in config).
- **Learner:** contextual bandit (LinUCB, plus epsilon-greedy baseline) over routing arms; offline training from the feedback log; produces a versioned routing-preference artifact the router can load behind a flag (`routing.rl_enabled`).
- **Safety:** RL adjusts *preferences among safe candidates only*; policy routing and guardrails always override.
- **Evidence:** `make rl-train` + `make rl-report` generate `reports/rl_routing_report.md` with learning curves and a before/after routing-accuracy and cost comparison on the golden set — real runs only.

### 4.6 Governance control plane
- **RBAC:** roles `analyst`, `reviewer`, `admin`; enforced by FastAPI dependencies on every mutating endpoint; simple local token auth (no paid IdP); role capability matrix documented.
- **Policy engine:** YAML policies evaluated pre- and post-generation (blocked topics, mandatory-citation, sensitivity escalation rules, disclosure footer). Include example policies inspired by common financial-compliance patterns (generic; no firm references).
- **Redaction:** deterministic PII redactor (emails, phones, SSN-like patterns) on ingest and output; redaction events audited.
- **Audit log:** append-only JSONL where each record includes the previous record's hash (tamper-evident chain) + `regulon audit verify` CLI command.
- **HITL approval queue:** SQLite-backed queue + REST endpoints + CLI (`regulon review list|approve|reject`); optional generic webhook notifier (Slack-compatible payload shape, no vendor lock); optional Celery+Redis worker profile for async processing.
- **MCP server:** expose `ingest`, `research`, `retrieve`, `review_list`, `approve` as MCP tools so any MCP client can drive Regulon; document a Claude Desktop config example.

### 4.7 Evaluation (a headline pillar — this must be unusually strong)
`evals/` with versioned golden datasets (synthetic + bundled-filing Q&A written for this repo):
- **Retrieval:** recall@k, MRR, nDCG on labeled query→chunk goldens.
- **Generation:** faithfulness/groundedness, citation precision/recall, answer relevance — RAGAS where applicable + a local LLM-as-judge implementation (G-Eval-style rubric prompts, judge = local model) with judge-agreement spot-check documented.
- **Routing:** routing accuracy vs labeled expected routes; cost-efficiency metric.
- **Guardrails:** adversarial suite (30+ prompt-injection/leak/PII attacks) with block-rate report.
- **End-to-end:** golden briefs with structural + citation assertions.
Two tiers: `make eval` (hermetic, deterministic provider, runs in CI, **hard thresholds fail the build**) and `make eval-real` (real local model; writes `reports/eval_report.md` + `reports/latency_cost.md`; committed to the repo; README links them). CI also validates that committed reports match the current eval schema.

### 4.8 Observability & ops
- OpenTelemetry spans across graph nodes, retrieval, gateway calls; JSONL trace export + optional OTLP endpoint; `regulon trace view <run_id>` renders a local HTML timeline.
- Prometheus `/metrics` (request counts, latencies, token usage, cache hit rate, guardrail blocks, approval throughput); Grafana dashboard JSON committed under `ops/grafana/`.
- **Cost meter:** per-call token accounting × model price registry → per-run cost summary in API response and reports.
- Docker: multi-stage `Dockerfile`; `docker-compose.yml` with profiles `core`, `pgvector`, `queue` (Celery+Redis), `observability` (Prometheus+Grafana).
- Kubernetes: plain manifests + kustomize overlay under `ops/k8s/` (deployment, service, configmap, secret template, HPA example) — documented as reference, CI-validated with `kubeconform`.
- Load test: Locust scenario + `make bench` writing `reports/load_test.md` (real numbers from a local run).

### 4.9 Dashboard (compact, high-polish)
Next.js + TypeScript app under `apps/dashboard/`: pages for Runs (status, cost, latency), Run Detail (agent timeline, evidence, citations), Approvals (approve/reject with reason), Evals (latest report render), Traces (link to HTML viewer). Talks only to the FastAPI API. Keep it small and clean; it exists for the demo video and screenshots, not as a second product.

## 5. Tech Stack (pinned decisions)

Python 3.11+ · Pydantic v2 · FastAPI + Uvicorn · Typer CLI · LangGraph (+ LangChain core where useful) · sentence-transformers (bge-small embeddings, ms-marco cross-encoder) · rank-bm25 · SQLite default / Postgres+pgvector profile · Ollama default model `qwen2.5:7b-instruct` with `llama3.2:3b` documented as the low-RAM alternative (verify current best small instruct models at build time and pin in one config constant) · RAGAS · OpenTelemetry SDK · Prometheus client · Celery+Redis (optional profile) · MCP Python SDK · pytest + coverage · ruff + mypy (strict on `src/`) · pre-commit · Next.js/TypeScript dashboard · Docker/kustomize/Locust.

## 6. Repository Layout

```
regulon/
├── AGENTS.md                  # engineering conventions for contributors and coding agents (generated in M0 from §7)
├── README.md                  # per §9 spec
├── LICENSE  CHANGELOG.md  CONTRIBUTING.md  SECURITY.md  CODE_OF_CONDUCT.md  ROADMAP.md
├── .github/ (workflows: ci.yml, safety.yml, release.yml; ISSUE_TEMPLATE; PULL_REQUEST_TEMPLATE.md)
├── pyproject.toml  Makefile  .env.example  .pre-commit-config.yaml
├── src/regulon/
│   ├── core/          # config, ids, events, errors, hashing, clock
│   ├── ingestion/     # loaders, edgar client, chunkers, redaction
│   ├── retrieval/     # stores (sqlite|pgvector), bm25, fusion, reranker, grader
│   ├── agents/        # supervisor + specialists, typed state, prompts/
│   ├── orchestration/ # graph build, checkpoints, budgets, hitl nodes
│   ├── routing/       # rules, semantic, cost, policy, fallback, cache, rl/
│   ├── gateway/       # provider adapters, model registry, cost accounting
│   ├── governance/    # rbac, policies, audit chain, approval queue, notifier
│   ├── evals/         # suites, judges, datasets/, gates
│   ├── observability/ # otel, metrics, trace viewer
│   ├── api/           # FastAPI routers, schemas, auth deps
│   ├── mcp/           # MCP server
│   └── cli/           # typer app
├── apps/dashboard/    # Next.js
├── data/samples/      # public-domain filing excerpts + SYNTHETIC_ docs
├── docs/ (architecture.md, adr/, runbooks/, threat_model.md, eval_methodology.md, demo_script.md)
├── ops/ (grafana/, k8s/, locust/)
├── reports/           # committed real-run artifacts (eval, latency_cost, load_test, rl_routing)
├── scripts/ (public_safety_scan.py, gen_synthetic_corpus.py, fetch_edgar_sample.py)
└── tests/ (unit/, integration/, adversarial/)
```

## 7. Engineering Conventions (also becomes AGENTS.md in M0)

- Typed everything; `mypy` strict on `src/`; Pydantic models at all boundaries; no bare dicts across module lines.
- `ruff` clean; line length 120; docstrings on public functions; small modules (<400 lines).
- Tests: coverage ≥ 80% on `src/regulon`; unit tests use the `deterministic` provider; at least one integration test per milestone; adversarial tests for guardrails.
- Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`, `refactor:`); one milestone = one branch = one PR with a descriptive body; never a single giant commit.
- Every architecturally significant choice → ADR in `docs/adr/NNN-title.md` (context / decision / alternatives / consequences).
- All thresholds, model names, weights, budgets live in `config/` YAML + env overrides — no magic numbers in code.
- Errors: custom exception hierarchy; graph nodes fail closed (guardrail uncertainty ⇒ escalate, never pass).
- Determinism: seeds fixed in tests and eval harness; eval datasets versioned.
- Reports in `reports/` are generated artifacts from real runs; each carries a generation timestamp, config hash, and machine spec; regenerate rather than hand-edit. Never fabricate.

## 8. Milestones (execute in order; do not start N+1 until N's acceptance passes)

**M0 — Scaffold & governance-of-the-repo.** Repo layout, pyproject, Makefile (`setup lint type test eval demo`), pre-commit, CI (ruff+mypy+pytest+coverage gate+safety scan), safety scan script, LICENSE, CHANGELOG (Keep-a-Changelog), CONTRIBUTING, SECURITY, CoC, ROADMAP seeded with the milestone list, issue/PR templates, AGENTS.md, ADR-001 (why Regulon / scope) + ADR-002 (local-first, real-inference-by-default).
*Accept:* fresh clone → `make setup && make lint type test` green; CI green; safety scan runs in CI.

**M1 — Ingestion & knowledge base.** EDGAR fetch script, bundled sample filings + synthetic corpus generator, normalization, chunking with metadata, redaction-on-ingest, SQLite store, `regulon ingest` CLI.
*Accept:* `regulon ingest data/samples` reports chunk counts; unit tests for chunker/redactor; ADR-003 (chunking strategy).

**M2 — Hybrid retrieval.** Dense + BM25 + RRF + cross-encoder rerank + relevance grading + evidence bundles with citations; pgvector store behind the same interface + compose profile; retrieval eval suite with recall@k/MRR/nDCG.
*Accept:* `regulon retrieve "<query>"` returns cited evidence; `make eval` retrieval gates pass; ADR-004 (hybrid fusion & reranking choices).

**M3 — Model gateway + real inference.** Provider adapters (ollama/openai/anthropic/bedrock/azure/deterministic), model registry with cost/latency metadata, token & cost accounting, structured-output helper, health checks.
*Accept:* with Ollama running, `regulon ask "<q>"` streams a real grounded answer with citations and prints cost/latency; hermetic tests pass without Ollama; ADR-005 (gateway design).

**M4 — Agents & orchestration.** LangGraph supervisor + 5 specialists, typed state, budgets, bounded critic loop, HITL checkpoint node, structured run events, `regulon research "<task>"` producing a draft brief into the approval queue.
*Accept:* end-to-end real run yields a cited brief in `pending_review`; integration test with deterministic provider covers the full graph; ADR-006 (supervisor pattern & state design).

**M5 — Routing subsystem.** Rules, semantic router, cost-aware model routing with budget caps, policy routing, fallback chains, semantic cache; `RouteDecision` records in traces; routing eval suite.
*Accept:* routing accuracy gate passes; cache demo shows measured hit-rate + cost savings; ADR-007 (routing architecture).

**M6 — Governance control plane.** RBAC + token auth, policy engine, output redaction, hash-chained audit log + `audit verify`, approval REST+CLI, webhook notifier, optional Celery profile, MCP server, FastAPI app tying it together, threat_model.md.
*Accept:* role-based access enforced in API tests; audit chain verifies; approve/reject flow works end-to-end incl. via MCP; adversarial guardrail suite ≥ target block rate; ADR-008 (audit & HITL design).

**M7 — Evaluation program.** Full suites per §4.7, golden datasets, LLM-judge, CI hard gates, `make eval-real` producing committed `reports/eval_report.md` + `reports/latency_cost.md`, `docs/eval_methodology.md`.
*Accept:* CI fails if any gate regresses; committed reports exist with real numbers + config hash; README links them; ADR-009 (eval gates & thresholds).

**M8 — Observability & ops.** OTel spans, trace HTML viewer, Prometheus metrics, Grafana dashboard JSON, Dockerfile + compose profiles, k8s manifests (kubeconform in CI), Locust + `make bench` → `reports/load_test.md`.
*Accept:* `docker compose --profile observability up` shows live dashboard; bench report committed from a real run; ADR-010 (observability model).

**M9 — RL routing optimizer.** Feedback store, reward function, LinUCB + epsilon-greedy, offline trainer, versioned preference artifact, `rl_enabled` flag, `reports/rl_routing_report.md` with real learning curves and before/after comparison.
*Accept:* report shows measurable routing improvement on the golden set with RL on vs off; safety override tests pass; ADR-011 (RL scope & safety bounds).

**M10 — Dashboard + launch polish.** Next.js dashboard (Runs/Run Detail/Approvals/Evals/Traces), README per §9 with architecture diagram + demo GIF placeholder + real screenshots, `docs/demo_script.md` (timed 2-minute walkthrough for recording), ROADMAP updated with post-1.0 items, release `v0.1.0` notes prepared.
*Accept:* dashboard drives a full research→approve cycle against the real API; README passes the 60-second test; all Definition-of-Done checks below pass.

## 9. README Specification (rich, full-layout — the README is the storefront and must outclass every other repo on this profile)

The README is a complete document with every section below, in this order. Richness comes from structure, original visuals, tables, diagrams, and real numbers — never from adjectives (Non-Negotiable #6 applies).

1. **Hero banner:** full-width original `docs/assets/hero.svg`, generated for this repo (consistent visual identity: dark background, single accent color, platform name + tagline).
2. **Centered badge row:** CI status, coverage, MIT license, Python 3.11+, agent count, "LangGraph orchestration", "100% local · $0 default runtime", tests passing.
3. **Author line:** `Author: Shake MD Tareq Hasan · GitHub @shakehasan` directly under the badges.
4. **Positioning paragraph** + three factual "what is different here" bullets: governed control plane (RBAC · policies · audit chain · HITL), RL-tuned routing from human + eval feedback, committed real-run evidence reports.
5. **Demo GIF** (recorded per `docs/demo_script.md`) immediately after the intro.
6. **Full Table of Contents** (linked).
7. **Why This Exists** — governing many agents reliably is a systems problem; framing in 2 short paragraphs.
8. **Highlights** — 12–14 specific one-line capabilities.
9. **Architecture** — Mermaid system diagram + layer/responsibility table.
10. **The Agent Workforce** — table: agent, role, inputs, outputs, guardrails applied.
11. **Routing Modes** — table of all 6 strategies + one real `RouteDecision` JSON example.
12. **RAG Pipeline** — flow diagram (ingest → chunk → dual index → RRF → cross-encoder rerank → grade → cite → ground-check) + component table.
13. **Governance & HITL** — RBAC capability matrix, one YAML policy example, audit hash-chain explanation, approval-flow diagram.
14. **RL-Tuned Routing** — reward formula, learning-curve image exported from the real report, before/after table.
15. **Evaluation** — gates table (metric, threshold, suite) + headline real numbers linking into `reports/`.
16. **Quickstart** — 5 commands (`git clone → make setup → ollama pull <model> → make demo → open dashboard`), expected-output snippet, troubleshooting note.
17. **Dashboard** — 2–3 real screenshots (runs, approval queue, run detail).
18. **Configuration** — env vars + YAML reference table.
19. **API Reference** — endpoint table (method, path, role required, purpose) + **MCP usage** with a Claude Desktop config snippet.
20. **Observability** — trace-viewer screenshot, Grafana screenshot, cost-meter example output.
21. **Deployment** — Docker compose profiles table + Kubernetes notes.
22. **Evidence** — table linking every committed report (eval, latency/cost, load test, RL routing) with its headline number and generation date.
23. **Project Layout** — annotated tree.
24. **Roadmap** — summary linking to `ROADMAP.md` and open Issues.
25. **Contributing · Security · Code of Conduct** — links.
26. **FAQ** — 6–8 genuine questions (Why local-first? Why is a deterministic provider in CI? How do I add an agent/provider/policy? Can this use cloud models?).
27. **Disclaimer + License** — not investment advice; MIT with full author name.

All images original/generated for this repo. Every number traces to a committed real-run report. Zero employer references.

## 10. Definition of Done (final gate — verify every line)

- [ ] Fresh machine: `make setup && make demo` produces a real-model, cited, approved brief in ≤ 10 min.
- [ ] `make lint type test` green; coverage ≥ 80%; CI green incl. eval gates + safety scan + kubeconform.
- [ ] `reports/` contains real eval, latency/cost, load, and RL reports with config hashes.
- [ ] Zero occurrences of employer/client names or banned self-praise terms (safety scan enforces).
- [ ] ≥ 11 ADRs, CHANGELOG current, ROADMAP public, v0.1.0 tagged with release notes.
- [ ] README implements §9 completely — hero banner, badge row, author line (Shake MD Tareq Hasan), demo GIF, full TOC, all 27 sections, evidence table with real numbers — and passes the 60-second test; demo script ready to record.
- [ ] MCP server usable from a standard MCP client per docs.

## 11. Execution Workflow

1. Create the empty GitHub repo `regulon`, clone it, and add this file at the root as `PLAN.md`.
2. Work one milestone at a time: read this plan fully, execute only the current milestone, follow §7 conventions strictly, use a branch and PR with conventional commits, and stop when that milestone's acceptance criteria pass with verification output.
3. Review every milestone before merging: run the acceptance commands, read the diffs, fix what needs fixing.
4. Merge at a steady cadence — 2–3 milestones per week over 4–6 weeks, never in one burst. Track ROADMAP items as GitHub Issues and close them via PRs.
5. Metrics discipline: every number in `reports/` must come from re-running the generating command; nothing hand-written.
6. After M10: record the demo video from `docs/demo_script.md`, add the GIF to the README, tag `v0.1.0` with release notes.
