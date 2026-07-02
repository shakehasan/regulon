# Regulon Roadmap

Regulon is built in ordered milestones with explicit acceptance criteria (full spec in
[PLAN.md](PLAN.md) §8). A milestone is "Done" only when its acceptance criteria pass with
verification output. Each milestone is one branch and one PR.

## Milestones to v0.1.0

- [ ] **M0 — Scaffold & repo governance.** Layout, tooling, CI with coverage gate and safety scan,
      governance docs, ADR-001/002. *(in progress)*
- [ ] **M1 — Ingestion & knowledge base.** EDGAR fetch, bundled + synthetic corpora, chunking with
      metadata, redaction-on-ingest, SQLite store, `regulon ingest`. ADR-003.
- [ ] **M2 — Hybrid retrieval.** Dense + BM25 + RRF + cross-encoder rerank + grading + cited evidence
      bundles; pgvector profile; retrieval eval gates. ADR-004.
- [ ] **M3 — Model gateway + real inference.** Provider adapters (ollama default, deterministic for CI),
      model registry, token/cost accounting, `regulon ask`. ADR-005.
- [ ] **M4 — Agents & orchestration.** LangGraph supervisor + 5 specialists, typed state, budgets,
      bounded critic loop, HITL checkpoint, `regulon research`. ADR-006.
- [ ] **M5 — Routing subsystem.** Rules, semantic, cost-aware, policy, fallback chains, semantic cache;
      `RouteDecision` records; routing eval gates. ADR-007.
- [ ] **M6 — Governance control plane.** RBAC, policy engine, redaction, hash-chained audit log,
      approval queue (REST + CLI), MCP server, threat model. ADR-008.
- [ ] **M7 — Evaluation program.** Golden datasets, LLM-judge, CI hard gates, committed real-run
      reports, eval methodology doc. ADR-009.
- [ ] **M8 — Observability & ops.** OTel spans, trace viewer, Prometheus/Grafana, Docker profiles,
      k8s manifests, load test report. ADR-010.
- [ ] **M9 — RL routing optimizer.** Feedback store, reward function, LinUCB + epsilon-greedy offline
      trainer, versioned preference artifact, RL report. ADR-011.
- [ ] **M10 — Dashboard + launch polish.** Next.js dashboard, full README, demo script, v0.1.0 release.

## Post-1.0 candidates

Collected during the build; will be filed as issues and prioritized after v0.1.0.

- Additional document loaders and data connectors (beyond EDGAR + synthetic).
- Multi-tenant knowledge bases.
- Pluggable judge models for evaluation.
- Streaming dashboard updates (server-sent events).
