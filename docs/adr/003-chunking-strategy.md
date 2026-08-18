# ADR-003: Section-aware chunking with character budgets

- **Status:** Accepted (parameters calibrated against retrieval baselines in M2)
- **Date:** 2026-08-05

## Context

Chunking decides what a retriever can ever return. A fact split across two chunks is retrievable
from neither; a chunk that welds together two unrelated sections dilutes its own embedding and
returns noise. Every retrieval metric M2 will report is bounded by a decision made here, one
milestone earlier.

Four forces shape it:

1. **The citation contract.** Regulon's headline promise is that every claim in a brief points at
   an evidence span a reader can open in the source. That makes a chunk not just an indexing unit
   but a *quotable* one — it has to be an exact, replayable slice of the stored document, and it
   has to be coherent enough that a human reading it alone can tell whether it supports the claim.
2. **The corpus is filings.** 10-K-shaped documents carry explicit heading structure (Business
   Overview, Risk Factors, MD&A, Liquidity and Capital Resources). The segmentation a chunker
   would otherwise have to infer is already written into the document, and the loaders
   ([`loaders.py`](../../src/regulon/ingestion/loaders.py)) already recover it as a `Section` map
   that tiles the text exactly.
3. **Nothing downstream exists yet.** The embedding model, BM25 index, fusion, and reranker all
   land in M2. The chunker must be committed *before* there is anything to measure it against.
4. **Source documents carry personal data.** Filings end in investor-relations contact blocks;
   anything fetched from EDGAR may carry more. Whatever the store holds is what retrieval returns,
   what a model is shown, and what a citation renders back to a reader.

Force 3 is the uncomfortable one and it is stated plainly here: **no retrieval benchmark has been
run against this corpus, so every parameter below is a starting point justified by document
structure and arithmetic, not a tuned result.** Writing a number here that no command produced
would violate the honesty rule (AGENTS.md, "Reports and metrics"). The numbers that justify or
replace them arrive with M2's recall@k / MRR / nDCG suite.

## Decision

Chunk **section-first, then pack to a character budget**, preserving exact offsets, with redaction
applied before chunking. Implemented in
[`chunking.py`](../../src/regulon/ingestion/chunking.py).

### 1. Sections bound chunks

Each section is packed independently, so no chunk ever mixes two sections. A section shorter than
`min_chars` merges *forward* into its successor rather than becoming a stub chunk; a short trailing
section, having no successor, merges backward. Text no section claims — preamble before the first
heading, gaps, documents with no headings at all — becomes its own unit with
`section_heading = None` rather than being dropped.

The reason to prefer this over splitting on length alone is that the document's author already did
the semantic segmentation. A heading is a human-authored assertion that what follows is one topic.
Discarding that signal in favour of a character counter throws away structure for free.

Each chunk carries its section heading in `ChunkMetadata.section_heading`, denormalized so that
retrieval filtering and citation rendering work from a chunk alone, without a join back to the
document.

### 2. Parameters — starting points, declared in config

All three live in [`config/regulon.yaml`](../../config/regulon.yaml) under `ingestion.chunking`
and are read through `Settings`; none is hardcoded.

| Setting | Value | Why this value, today |
|---|---|---|
| `max_chars` | 1200 | Roughly 200–300 English tokens — comfortably inside the input window of the small embedding models M2 targets, so a chunk is never silently truncated by the encoder. Large enough to hold a complete argument (a risk factor, a liquidity discussion); small enough that one topic dominates the embedding rather than being averaged across several. |
| `overlap_chars` | 150 | 12.5% of the window. Wide enough to carry a sentence or two across a break so a fact straddling one stays retrievable from either side; small enough that duplicated text does not meaningfully inflate the index or let one passage occupy several top-*k* slots. |
| `min_chars` | 200 | The floor below which a chunk is more likely to be a heading, a table caption, or a stub than a retrievable claim. Short fragments embed poorly — too little context to place them — and clutter a ranked list. Reused as the fill floor when choosing a break point, rather than introducing a second literal. |

The reasoning above is structural, not empirical. Each value is a hypothesis M2 tests.

### 3. Character budgets, not token budgets

Budgets are counted in characters. The alternative — counting tokens with the tokenizer of the
model that will consume the chunk — is more precise about what a model actually sees, and it is
rejected at this layer for three reasons:

- **Model-agnostic ingest.** A token budget binds the stored corpus to one tokenizer. Changing the
  embedding model, or using different models for embedding and generation (which Regulon does),
  would mean the stored chunks were sized for a model that no longer reads them, or a re-ingest of
  the whole corpus on a model swap.
- **No tokenizer dependency at ingest time.** Ingestion runs before the gateway exists (M3) and
  must stay free of model weights and downloads. `regulon ingest` should work on a machine with no
  model server, which is also what keeps ingestion tests hermetic.
- **Determinism.** A character count is stable forever. A token count changes with a tokenizer
  version, which would silently change chunk boundaries and therefore every chunk id.

**The tradeoff accepted:** characters map to tokens at a ratio that varies with content. Dense
numeric tables and ticker symbols tokenize far less efficiently than prose, so a 1200-character
table chunk carries more tokens than a 1200-character narrative chunk. `max_chars` is therefore a
*bound with slack*, chosen so the worst realistic ratio still fits the encoder window rather than
so the average case fills it. That wastes some capacity on prose-heavy chunks. If M2 shows the
waste is material, the fix is a configurable characters-per-token estimate — not moving the
tokenizer into ingest.

### 4. Exact `[start_char, end_char)` offsets are preserved

For every chunk, `document.text[chunk.start_char:chunk.end_char] == chunk.text`. The chunker never
rewrites, normalizes, or strips text — it only *selects offsets*. Whitespace trimming is done by
moving the offsets inward, never by stripping the string.

This is the invariant the citation layer stands on. An evidence span (M2) and a `[S1]` citation in
a brief (M4) are only checkable if a reader, a critic agent, and an eval harness can each replay
the exact span against the stored document and get the same characters back. Offsets that are
"approximately right" cannot be verified, and an unverifiable citation is worth no more than an
uncited claim. It is also what lets a groundedness check quote evidence without storing a second
copy of it.

Two supporting properties fall out of the same discipline: every non-whitespace character of the
document is covered by at least one chunk (nothing is silently dropped), and ids are deterministic
content hashes, so the same document ingested twice on two machines produces identical chunk ids.

### 5. Redaction runs before chunking and before storage

The stage order is `parse → normalize → redact → chunk → store`, fixed by
[`pipeline.py`](../../src/regulon/ingestion/pipeline.py) — the one place the stages meet — and
implemented by [`redaction.py`](../../src/regulon/ingestion/redaction.py).

Redaction is first because **the store is the trust boundary.** Everything after it — the dense
index, the BM25 index, evidence bundles, model prompts, cached answers, rendered citations, eval
fixtures — is downstream of stored text, and each is another place personal data would have to be
scrubbed independently if it entered the store. Redacting once, before anything is persisted, means
no later stage has to be trusted to do it again, and no `.sqlite3` file a contributor might attach
to an issue contains data it should not.

Redacting before *chunking* specifically matters because a PII span can straddle a chunk boundary.
Post-chunk redaction would see two half-patterns, match neither, and store both halves.

The redactor is precision-biased on purpose: a bare run of digits is never read as a phone number,
because a false positive silently corrupts a financial figure in a corpus whose whole purpose is
answering questions about figures. Detection covers email, phone, and the SSN shape
`NNN-NN-NNNN`. Redaction events are recorded with offsets into the pre-redaction text, so an audit
trail can say *where* something was removed and of what kind, without storing what it was.

### 6. Determinism throughout

Same document plus same settings always yields the same chunk list — same boundaries, same
ordering, same ids. No clock, no randomness, no set iteration in any decision path. Re-ingesting a
corpus is a no-op rather than a duplication, eval datasets stay valid across runs, and a retrieval
regression between two commits can be attributed to a change rather than to chunker jitter.

## Alternatives considered

1. **Fixed-size splitting** (every *N* characters, no structural awareness). Simplest possible
   implementation and perfectly uniform chunk sizes, which suits batched embedding. Rejected: it
   cuts mid-sentence and mid-table by construction, and it merges the tail of one section with the
   head of the next — producing chunks whose embedding represents a topic boundary rather than a
   topic. It also produces unquotable citations, which is disqualifying given §4. Uniform sizes
   are an implementation convenience; coherent evidence is the requirement.

2. **Sentence-window retrieval** (index single sentences, return the surrounding window at query
   time). Strong precision — the embedded unit is exactly the claim being matched — and popular for
   good reason. Rejected for now on cost and coupling: it multiplies index size by roughly the
   sentence count, makes every retrieval a two-step fetch, and makes the *citable* unit differ from
   the *indexed* unit, which complicates the offset contract the whole governance story depends on.
   It remains the most credible upgrade path if M2 shows chunk-level precision is the binding
   constraint, and it composes with this design rather than replacing it: sentences are a
   sub-partition of an existing chunk's span, so the offsets stay valid.

3. **Recursive character splitting** (try paragraph separators, then sentence, then word, then
   hard). Rejected as the *top-level* strategy, adopted as the *inner* one. As a top-level strategy
   it treats a heading as just another separator with no special meaning, so a long section and the
   short section after it can still end up in one chunk. Inside a section, though, its break
   preference is exactly right, and that is what the packer does: paragraph break, else sentence
   break, else word boundary, else — only for a single word longer than the window — a hard split.
   A break is rejected if it would leave a chunk below `min_chars`, falling through to a weaker but
   better-filled one. So this alternative did not lose so much as get demoted one level.

4. **Layout-aware or model-based chunking** (a document-layout model, or an LLM asked to propose
   semantic boundaries). Highest ceiling on quality, especially for the tables and multi-column
   layouts that filings are full of. Rejected on all three of the project's binding constraints:
   it adds a model dependency to ingest (breaking the no-tokenizer/no-weights rule in §3 and
   slowing every ingest), it is non-deterministic unless heavily pinned (breaking §6, and an
   LLM-proposed boundary can shift between runs of the same document), and a hosted layout service
   would breach the zero-cost guarantee outright. A local layout model is revisitable post-1.0,
   but it would have to earn its cost against a measured baseline that does not exist yet.

5. **Token-count budgets with a pinned tokenizer.** Precise about what the encoder actually
   receives, and eliminates the characters-per-token slack accepted in §3. Rejected: it binds the
   stored corpus to one model's tokenizer, forces a full re-ingest on a model change, and makes
   chunk boundaries — hence chunk ids — a function of a dependency version. The precision gained is
   real but small; the coupling is permanent.

6. **Redact at query time or at render time instead of at ingest.** Keeps the store faithful to the
   source, which has genuine appeal for an audit corpus, and allows a policy change to apply
   retroactively without re-ingesting. Rejected: it makes every consumer of the store a place PII
   can escape, and there are many (two indexes, evidence bundles, prompts, caches, exports, eval
   fixtures, a committed database file). Fail-closed argues for scrubbing once at the narrowest
   point. The cost — the store no longer holds byte-identical source text — is accepted and
   recorded below.

## Consequences

- **Every parameter here is unvalidated.** No recall@k, MRR, or nDCG number exists for this corpus,
  and none is claimed. M2 is where these values are first measured, and the calibration run is
  committed with that milestone. Until then `max_chars: 1200 / overlap_chars: 150 / min_chars: 200`
  should be read as a defensible default, not a finding.

- **M2 may force a revisit, and these are the likely triggers.** Low recall with high precision
  would mean chunks are too large (facts diluted) → lower `max_chars`, or move to alternative 2.
  Good recall with facts landing split across neighbours → raise `overlap_chars`. Ranked lists
  dominated by near-duplicate neighbours → lower it. Systematically poor retrieval on tabular
  sections → the character budget is mis-serving tables specifically, and per-section-kind budgets
  or table-aware handling becomes the next ADR. Any of these is a config change plus a re-ingest,
  not a rewrite — which is the main reason the parameters are config and the strategy is not.

- **Re-chunking changes chunk ids.** Ids hash the chunk text, so any parameter change invalidates
  every id in the store. This is handled rather than avoided: the store replaces the previous
  occupant of a `(document_id, chunk_index)` slot on re-ingest. But it means chunk ids cannot be
  used as stable external references across a settings change — citations must be replayable from
  offsets, which §4 guarantees, rather than from ids alone.

- **The store no longer holds byte-identical source text, and every offset it holds is an offset
  into the redacted text.** Redaction rewrites before storage, and a placeholder is rarely the same
  length as the span it replaces, so the loader's `Section` map is stale the moment redaction runs.
  The pipeline remaps section offsets through the redaction events before chunking, and recomputes
  `document_id` from the redacted text, so the §4 invariant holds against what was actually stored.
  The consequence to internalize: chunk offsets are **not** offsets into the original file, and any
  tool reconciling the two must go through the redaction events. This is the price of §5, and it is
  cheaper than trusting every downstream consumer to scrub.

- **Chunk sizes are uneven, and that is intended.** Because sections bound chunks, a 250-character
  risk subsection is its own chunk while a long MD&A section fills several near `max_chars`. On the
  bundled synthetic corpus most sections already fit inside one window, so the packer's overlap path
  rarely engages there; overlap will only do visible work on longer real filings. Batching for
  embedding therefore sees variable-length inputs, and any future assumption of uniform chunk size
  would be wrong.

- **The strategy depends on the loaders recovering headings.** Markdown ATX headings and HTML
  `h1`–`h6` give a reliable section map; plain text falls back to a heuristic and PDFs to whatever
  text extraction yields. A document with no recoverable headings degrades to a single unit packed
  by length — which is exactly alternative 1, for that document. That degradation is graceful and
  bounded, but it means chunk quality varies with source format, and filings that arrive as
  poorly-structured PDFs will be the weakest case in M2's numbers.

- **Overlap duplicates text in the store and in both indexes.** At 12.5% the storage cost is
  accepted; the retrieval cost — near-duplicate neighbours competing for the same top-*k* slots — is
  the more interesting one and is a thing M2 should look for explicitly.
