# ADR-009: Evaluation stack — RAGAS, G-Eval, and a local run store

- **Status:** Accepted (thresholds calibrated against real baselines in M7)
- **Date:** 2026-07-07

## Context

Evaluation is a headline pillar of Regulon (PLAN.md §4.7): releases are gated by measured quality,
not judgement calls. That requires answering three separate questions, which are often conflated:

1. **Is the retrieval good?** — a ranking problem, measurable with classical IR metrics against
   labeled query→chunk goldens. No model judgement needed.
2. **Is the generated brief faithful, relevant, and properly cited?** — needs semantic judgement.
   Deterministic string matching cannot tell whether a claim is entailed by its evidence.
3. **Did quality move between two commits, and why?** — needs run-level history and dataset
   versioning, not just a single score.

Two constraints cut across all of them. Non-Negotiable #3: the runtime requires zero paid
services, zero API keys, zero accounts. Non-Negotiable #4: never fabricate metrics. Together they
mean every headline number must be reproducible, by a stranger, offline, for free. Regulon exists
as a learning resource — a metric someone cannot regenerate on their own machine teaches them
nothing.

## Decision

Adopt a three-layer evaluation stack. Every layer runs locally and costs nothing.

### 1. RAGAS — RAG-specific metrics (Apache-2.0)

RAGAS computes `faithfulness`, `answer_relevancy`, `context_precision`, and `context_recall`. It
is configured to use **our local judge model and local embeddings**, so the metrics need no
account and cost nothing. RAGAS covers the well-studied RAG failure modes with an implementation
the community already recognizes, which makes our numbers comparable to other projects rather
than bespoke.

### 2. G-Eval — rubric judging for what RAGAS does not cover (in-repo)

RAGAS does not score the things Regulon's governance claims depend on: whether every claim carries
a usable citation, whether the cited span actually supports it, whether extracted figures are
arithmetically right, and whether compliance framing survived. We implement a G-Eval-style judge
(rubric prompt + chain-of-thought + a 1–5 score per dimension) in
`src/regulon/evals/judges/geval.py`, run with a fixed seed at temperature 0 against the local
model. G-Eval is a published method, not a product — implementing it directly keeps the stack
free and the scoring auditable.

Rubrics and weights are declared in [`config/evals.yaml`](../../config/evals.yaml): citation
support, evidence sufficiency, numeric accuracy, hallucination-free, compliance tone. The weighted
composite feeds both the CI gate and the RL routing reward (M9).

Because an LLM judge is itself a measuring instrument, we spot-check it: a held-out slice is
human-labeled and judge/human agreement (Cohen's kappa) is reported in
`docs/eval_methodology.md`. Agreement is **reported, not gated**, until M7 establishes a baseline —
gating on an uncalibrated instrument would be theater.

### 3. Local run store — experiment tracking without a service

Every eval run appends a record (git SHA, config hash, dataset version, all metrics, machine spec,
timestamp) to `reports/eval_runs.jsonl`. Two CLI commands read it: `regulon eval compare A B`
prints a metric-delta table between any two runs, and `regulon eval history` renders a trend over
time. Human labels for judge calibration come from the approval queue's feedback store (M6), which
already exists for governance reasons.

This is a deliberate build-not-buy: hosted experiment-tracking platforms offer a better UI, but
all of them require an account and most meter usage. Depending on one would make Regulon's
headline numbers unreproducible for anyone who has not signed up, and would put a cost between a
learner and the project. A JSONL file plus two commands covers the actual requirement —
"did this commit regress?" — at zero cost, with no vendor in the loop.

### Native suites

Retrieval (recall@k, MRR, nDCG), routing accuracy and cost-efficiency, the 30+ attack adversarial
guardrail suite, and end-to-end golden-brief assertions remain first-party implementations —
they are cheap, deterministic, and specific to this system's contracts.

### Two tiers

`make eval` runs hermetically on the `deterministic` provider in CI with **hard gates that fail the
build**. `make eval-real` runs the real local model and writes committed reports carrying a
timestamp, config hash, and machine spec.

## Alternatives considered

1. **RAGAS only.** Simplest, one dependency. Rejected: no coverage of citation support, numeric
   accuracy, or compliance framing — precisely the properties a governance platform must defend.
2. **A single bespoke LLM judge for everything.** Full control, no dependency. Rejected: throws
   away a well-known metric vocabulary, and "trust our custom scorer" is a weaker claim than
   reporting a standard metric alongside a rubric judge.
3. **A hosted experiment-tracking platform.** Best UI for diffing runs across commits. Rejected:
   every option requires an account and most meter usage beyond a free tier. That breaks the
   clone-and-run promise, makes committed numbers unverifiable for non-subscribers, and introduces
   a cost to learning from this repo. The local run store covers the requirement for free.
4. **A self-hosted OSS tracking server.** Free and open, but adds a service to run, a database to
   manage, and setup friction before a learner can see a single metric. Rejected as
   disproportionate: the requirement is a metric diff between two commits, which a JSONL file and
   two commands satisfy.
5. **Gate on judge/human agreement immediately.** Rejected: with no baseline, the threshold would
   be invented — which Non-Negotiable #4 forbids. Report first, gate in M7.

## Consequences

- Two judge implementations (RAGAS-driven and G-Eval) share one local judge model and one seed;
  changing the judge model changes both, so it is pinned in config and its identity is recorded in
  every report's config hash.
- LLM-judged metrics carry variance that classical IR metrics do not. Mitigations: temperature 0,
  fixed seeds, versioned datasets, and gates set with headroom rather than at the observed mean.
- The run store is a plain append-only JSONL file: trivially diffable and greppable, but with no
  UI beyond the CLI table and the dashboard's Evals page (M10). Accepted — the audience is
  engineers reading a repo, not analysts browsing a console.
- `reports/eval_runs.jsonl` grows monotonically; `retain_runs` bounds it, and rotation is a
  documented maintenance step rather than an automatic deletion.
- Local judging costs wall-clock time on the maintainer's machine; CI stays fast because the
  hermetic tier uses the `deterministic` provider.
- Thresholds in `config/evals.yaml` are provisional until M7 calibrates them against real
  baselines; the calibration run and its numbers are committed with that milestone.
