# Regulon

Regulon is an open-source reference platform for running multi-agent LLM systems the way regulated
industries need them run — every answer grounded and cited, every decision routed and traced, every
risky action approved by a human, every release gated by evals.

**Author:** Shake MD Tareq Hasan · GitHub [@shakehasan](https://github.com/shakehasan)

> **Status: under construction.** Regulon is being built milestone by milestone against a public
> specification ([PLAN.md](PLAN.md)). The table below tracks progress; the full README described in
> the plan lands with M10.

## What this will be

- A LangGraph supervisor orchestrating specialist agents (retriever, analyst, writer, critic, compliance)
  over a hybrid-retrieval RAG pipeline with cross-encoder reranking and mandatory citations.
- A governance control plane: RBAC, YAML policy engine, PII redaction, a hash-chained audit log, and a
  human-in-the-loop approval queue.
- Multi-mode routing (rules, semantic, cost-aware, policy, fallback, semantic cache) with an offline
  RL optimizer tuned by human and eval feedback.
- An evaluation program with hermetic CI gates and committed real-run reports.
- Local-first: the default runtime needs zero paid services, zero API keys, zero accounts. Demos run a
  real local model via Ollama.

The flagship reference app is **Research Desk** — a multi-agent investment research workflow over public
SEC EDGAR filings that produces citation-backed briefs, with human approval required before a brief is
finalized.

## Milestones

| Milestone | Scope | Status |
|---|---|---|
| M0 | Scaffold & repo governance | In progress |
| M1 | Ingestion & knowledge base | Planned |
| M2 | Hybrid retrieval | Planned |
| M3 | Model gateway + real inference | Planned |
| M4 | Agents & orchestration | Planned |
| M5 | Routing subsystem | Planned |
| M6 | Governance control plane | Planned |
| M7 | Evaluation program | Planned |
| M8 | Observability & ops | Planned |
| M9 | RL routing optimizer | Planned |
| M10 | Dashboard + launch polish | Planned |

See [ROADMAP.md](ROADMAP.md) for detail.

## Development

```bash
make setup   # venv + editable install + pre-commit hooks
make lint    # ruff check + format verification
make type    # mypy (strict on src/)
make test    # pytest with coverage gate (>= 80%)
make safety  # public-safety scan
```

Engineering conventions live in [AGENTS.md](AGENTS.md). Architecturally significant decisions are
recorded as ADRs under [docs/adr/](docs/adr/).

## Disclaimer

Regulon is a research and education tool. Nothing it produces is investment advice.

## License

[MIT](LICENSE) © 2026 Shake MD Tareq Hasan
