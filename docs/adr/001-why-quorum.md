# ADR-001: Why Quorum exists, and what is in scope

- **Status:** Accepted
- **Date:** 2026-07-02

## Context

Multi-agent LLM frameworks make it easy to wire agents together, but they mostly stop at
orchestration. Teams in regulated settings (finance, healthcare, legal) need a different bar:
answers grounded in cited evidence, deterministic routing decisions that can be explained after
the fact, human sign-off before anything is finalized, access control on every mutating action,
tamper-evident audit trails, and release gates driven by evaluation — not vibes. Public examples
that treat these as first-class, integrated concerns (rather than bolt-ons) are scarce.

There is also a knowledge-sharing goal: this repository should show the GitHub community, end to
end and reproducibly on a laptop, how such a system is engineered — including its tradeoffs —
using only public-domain and synthetic data, with no business or proprietary references of any
kind.

## Decision

Build **Quorum**, an original open-source reference platform where the governance control plane
is the product, not an afterthought:

- A LangGraph supervisor orchestrating specialist agents over a hybrid-retrieval RAG pipeline
  with mandatory citations and groundedness verification.
- A control plane: RBAC, YAML policy engine, PII redaction, hash-chained audit log, and a
  human-in-the-loop approval queue that gates every finished brief.
- Layered routing (rules → semantic → cost-aware → policy → fallback → cache) with an offline
  RL optimizer that tunes preferences among safe candidates only.
- An evaluation program with hermetic CI gates and committed reports produced by real runs.
- One flagship app, **Research Desk**, over exactly two data sources: public-domain SEC EDGAR
  filings and clearly labeled synthetic documents.

The name: a *quorum* is the number of members whose presence makes a deliberative body's decisions
valid. Many agents deliberate here, but a result only counts once the control plane's conditions
are met — evidence cited, policy satisfied, guardrails passed, a human reviewer's approval
recorded. Without those, the system has produced an opinion, not a decision.

### Out of scope (deliberately)

- Production identity providers, secret managers, or paid observability backends — local-first
  equivalents instead, with extension points documented.
- Fine-tuning or serving custom models; the RL component tunes *routing preferences*, not weights.
- General-purpose data connectors beyond EDGAR + synthetic corpora (post-1.0 candidates).
- Multi-tenancy and horizontal scale-out beyond a reference Kubernetes manifest.

## Alternatives considered

1. **A thin demo app on an existing framework.** Fastest to ship, but demonstrates none of the
   control-plane engineering that is the point of the project.
2. **Contributing governance features to an existing OSS framework.** Valuable but subject to
   upstream roadmaps and reviews; does not yield a coherent, opinionated reference architecture.
3. **A written architecture guide without code.** No reproducible evidence; violates the
   project's "show numbers, not adjectives" principle.

## Consequences

- Scope is large; it is managed by strictly ordered milestones (PLAN.md §8), each with acceptance
  criteria, one branch, and one PR.
- Governance features cost latency and complexity in the hot path (guardrails, audit writes,
  approval gates); this is accepted and measured rather than hidden — reports carry real numbers.
- Two data sources keep the repo public-safe by construction, at the cost of domain breadth.
