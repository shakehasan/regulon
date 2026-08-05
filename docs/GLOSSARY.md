# Glossary

Plain-language definitions for every term of art used in [PLAN.md](../PLAN.md) and
[README.md](../README.md), in the order a newcomer would meet them: retrieval, agents,
routing, governance, then evaluation. Each entry links to where the concept lives in this
repo once it exists; entries for unbuilt milestones say so.

## Retrieval & RAG

**RAG (Retrieval-Augmented Generation)** — instead of asking a language model to answer from
memory alone, you first retrieve relevant documents and give them to the model as context. This
is what makes citations possible: the model is grounded in text you can point to.

**Agentic RAG** — RAG where retrieval isn't a single fixed step but something agents can decide
to do, redo with a different query, or skip — driven by a plan rather than a script.

**Dense retrieval** — search by meaning: both the query and every document chunk are converted
into vectors (embeddings) by a model, and the closest vectors by distance are returned. Finds
"revenue growth" when you search "sales increase," even with no shared words.

**Sparse retrieval / BM25** — classic keyword search, scoring on term overlap and frequency.
Finds exact terms (ticker symbols, line-item names) that dense retrieval can blur past.

**Hybrid retrieval** — running dense and sparse retrieval together and combining their results,
because each catches cases the other misses.

**RRF (Reciprocal Rank Fusion)** — the specific way Regulon combines the dense and sparse result
lists: each document's rank in each list contributes `1 / (k + rank)` to a combined score. Simple,
requires no score calibration between the two very different scoring scales.

**Cross-encoder reranking** — RRF gives you a merged shortlist quickly but coarsely. A
cross-encoder then reads each (query, candidate) pair *together* — more expensive per pair, far
more accurate — and reorders the shortlist. Fast-then-precise, applied to a small candidate set.

**Groundedness / faithfulness** — whether a generated claim is actually supported by the
retrieved evidence, as opposed to sounding plausible. Regulon scores this and revises or escalates
answers that fall short, rather than returning them anyway.

**Evidence bundle** — the package of retrieved chunks handed to the writer agent, each with an
exact source span and a stable citation ID (`[S1]`, `[S2]`, ...) so every claim can point back to
its origin.

**pgvector** — a PostgreSQL extension for storing and searching embedding vectors. Regulon
defaults to SQLite+numpy for dense retrieval and offers pgvector as an optional Docker profile for
larger corpora — same interface either way.

## Agents & orchestration

**LangGraph** — a library for building applications as a graph of nodes (steps) and edges
(transitions between them), where state flows through explicitly rather than being managed by
ad hoc control flow. Regulon's agents are LangGraph nodes over one shared, typed state object.

**Supervisor pattern** — one agent (the `supervisor`) plans a task, delegates pieces of it to
specialist agents, and aggregates their results — rather than every agent talking to every other
agent freely. Keeps the control flow inspectable and bounded.

**Specialist agent** — an agent scoped to one job (retrieval, numeric analysis, drafting,
critique, compliance) rather than one agent trying to do everything. See
[the agent workforce table](../README.md#the-agent-workforce).

**Bounded critic loop** — the `critic` agent can send a draft back for one revision if it finds
uncited or unsupported claims — capped at one loop, so quality-checking can't turn into an
infinite cycle.

**Typed state** — the data passed between agent nodes is a Pydantic model with a fixed schema,
not a free-form dictionary — so a node can't silently expect a field another node never sets.

## Routing

**Model gateway** — the layer between agents and language models. Agents ask for a capability
("summarize with citations"); the gateway picks which model actually serves the call.

**RouteDecision** — a structured record emitted every time the router picks a model or a
pipeline path: candidates considered, scores, the one chosen, why, and its estimated cost. Makes
routing explainable after the fact instead of a black box.

**Semantic cache** — a cache keyed by embedding similarity rather than exact string match, so a
question worded slightly differently from a previous one can still reuse that answer.

**Contextual bandit / LinUCB** — an online-learning approach for repeatedly choosing between
options (here, routing arms) under uncertainty, balancing "pick what's worked before" against
"try something to learn more." LinUCB is a specific, well-studied algorithm for this. Regulon runs
it *offline* on logged data, not live in production traffic.

**Epsilon-greedy** — a simpler bandit baseline: pick the best-known option most of the time, and
a random one a small fraction (`epsilon`) of the time, to keep exploring. Used as a sanity-check
baseline against LinUCB.

**Reward function** — the formula that scores a routing decision after the fact, combining eval
quality, human approval, cost, and latency — what the RL optimizer is trying to maximize.

## Governance

**RBAC (Role-Based Access Control)** — permissions attached to roles (`analyst`, `reviewer`,
`admin`) rather than individual users, so "who can approve a brief" is one rule, not one exception
per person.

**HITL (Human-in-the-Loop)** — a workflow step where a human must review and act before the
process continues. In Regulon, no brief becomes `final` without a `reviewer` approving it.

**Policy engine** — a set of declarative rules (in YAML, not code) that can force stricter
handling — a higher groundedness bar, mandatory human review — for sensitive topics.

**Redaction** — automatically detecting and masking personal data (emails, phone numbers,
SSN-like patterns) in text before it's stored or shown.

**Audit chain / hash chain** — an append-only log where each entry embeds the hash of the entry
before it. Change or delete an old entry and every hash after it stops matching — tampering
becomes detectable, which is why this is called *tamper-evident*, not *tamper-proof*.

**MCP (Model Context Protocol)** — an open standard for exposing tools and data to AI
assistants. Regulon exposes its own operations (ingest, research, review, approve) as MCP tools,
so any MCP-compatible client can drive it, not just Regulon's own CLI or dashboard.

## Evaluation

**Golden dataset** — a hand-curated, version-controlled set of examples with known-correct
answers, used to check whether the system still gets them right after a change.

**Recall@k / MRR / nDCG** — three ways of scoring a ranked list of retrieved results against a
known-correct answer: *recall@k* asks whether the right document is anywhere in the top *k*;
*MRR* (Mean Reciprocal Rank) rewards it being near the top, not just present; *nDCG* (Normalized
Discounted Cumulative Gain) further accounts for a graded, not just binary, notion of relevance.

**RAGAS** — an open-source library of metrics purpose-built for RAG systems (faithfulness,
answer relevancy, context precision/recall), used here on a local judge model.

**LLM-as-judge** — using a language model to score another model's output against a rubric,
because some qualities (does this claim follow from this evidence?) resist exact string matching.

**G-Eval** — a specific LLM-as-judge method: give the judge a rubric and ask it to reason
step-by-step (chain-of-thought) before emitting a score. Implemented in-repo, on the local model.

**Deterministic provider** — a seeded, fixture-based stand-in for a real model, used only in
hermetic tests and CI so results are exact and reproducible — never used in demos or reports.

**Adversarial suite** — a set of deliberately hostile inputs (prompt injection, attempts to leak
system instructions, PII exfiltration attempts) used to test whether guardrails actually hold.

## Observability

**OpenTelemetry (OTel)** — an open standard and toolkit for recording what a system did and how
long each step took, as structured traces, without tying the tracking format to a specific vendor.

**Span** — one recorded unit of work within a trace — e.g., "the retriever queried the index" —
with a start time, duration, and metadata. A run is a tree of nested spans.

**Cost meter** — per-call token counts multiplied by a model's price entry in the registry
(free/local models simply price at 0), rolled up into a per-run total.

## Process

**ADR (Architecture Decision Record)** — a short document capturing one significant decision:
the context that forced it, what was chosen, what else was considered, and the consequences
accepted. Regulon's live under [`docs/adr/`](adr/).

**Milestone** — one ordered, self-contained slice of PLAN.md's build plan (M0 through M10), each
with explicit acceptance criteria that must pass before the next one starts.
