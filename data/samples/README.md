# Sample corpus

Every document in this directory is **synthetic**: an invented company, invented segments, and
invented figures, written by [`scripts/gen_synthetic_corpus.py`](../../scripts/gen_synthetic_corpus.py).
Nothing here is a real filing, a real metric, or investment advice. The files exist so
`regulon ingest data/samples` has something realistic to parse, chunk, redact, and cite.

Regulon's data hygiene rule (see [AGENTS.md](../../AGENTS.md)) permits exactly two data sources:
public-domain SEC EDGAR filings, and synthetic documents labeled `SYNTHETIC` in filename, front
matter, and docs. This directory holds the second kind.

## Why the bundled corpus is synthetic rather than real filing excerpts

Shipping generated documents instead of excerpts from real filings keeps the demo runnable offline
with no licensing question and no network dependency: clone the repo, run the ingest command, and
everything works with no account, no API key, and no download. Real filings are one command away
when you want them — see [Fetching real filings](#fetching-real-filings) below.

## What is in each document

| File | Fictional issuer | Ticker | Form | Fiscal year |
|---|---|---|---|---|
| `SYNTHETIC_MERIDIAN-FREIGHT_10-K_FY2022.md` | Meridian Freight Systems | `ZZMFS.TEST` | 10-K | 2022 |
| `SYNTHETIC_HALCYON-GRID_10-K_FY2023.md` | Halcyon Grid Utilities | `ZZHGU.TEST` | 10-K | 2023 |
| `SYNTHETIC_VANTOR-INSTRUMENTS_10-K_FY2024.md` | Vantor Clinical Instruments | `ZZVCI.TEST` | 10-K | 2024 |
| `SYNTHETIC_MERIDIAN-FREIGHT_10-K_FY2025.md` | Meridian Freight Systems | `ZZMFS.TEST` | 10-K | 2025 |

Tickers end in `.TEST` so they cannot be read as listed symbols. Each document is labeled three
times over:

1. **Filename** — every file starts with `SYNTHETIC_`.
2. **Front matter** — `synthetic: true` plus a `disclaimer:` line, with `title`, `company`,
   `ticker`, `form`, `fiscal_year`, `generator`, and `seed` for provenance.
3. **First body line** — a banner stating that the document is synthetic, that the numbers are
   invented sample data for a company that does not exist, and that nothing in it is investment
   advice.

Each document carries the sections a real annual report carries — business overview, selected
financial data, segment results, management discussion, risk factors, liquidity, forward-looking
statements, and an investor relations contact — with `##` headings the section-aware chunker splits
on. Figures are drawn so the tables add up: segment revenue sums to total revenue, and the
comparative column moves by a plausible amount rather than jumping.

The contact section carries deliberate redaction fodder: addresses on the reserved `example.com`
domain and telephone numbers in the `555-01xx` block set aside for fiction. Ingest-time redaction
replaces them, so the sample corpus exercises that path without anyone's real details.

## Regenerating

These files are generated artifacts. Do not hand-edit them — change the generator or its content
module and regenerate:

```bash
python scripts/gen_synthetic_corpus.py
```

That writes the four documents above with the committed defaults
(`--out-dir data/samples --seed 20260101 --count 4`). Generation is deterministic: the same seed and
count always produce byte-identical files, so regenerating an unchanged corpus leaves an empty diff.
Other options:

```bash
python scripts/gen_synthetic_corpus.py --count 8            # more documents, more issuers and years
python scripts/gen_synthetic_corpus.py --seed 7             # a different invented corpus
python scripts/gen_synthetic_corpus.py --out-dir /tmp/corpus  # write somewhere else
```

Existing `SYNTHETIC_*.md` files in the output directory are replaced on each run, so a smaller
`--count` leaves no stale documents behind. This README is left alone.

The fictional text lives in
[`scripts/synthetic_corpus_content.py`](../../scripts/synthetic_corpus_content.py); the figures,
templating, and file writing live in the generator itself.

## Fetching real filings

Real SEC EDGAR filings are public domain and can be pulled on demand with
[`scripts/fetch_edgar_sample.py`](../../scripts/fetch_edgar_sample.py). EDGAR asks callers to
identify themselves, so set a descriptive user agent first:

```bash
export REGULON_INGESTION__EDGAR__USER_AGENT="your-project (https://github.com/your-handle)"
python scripts/fetch_edgar_sample.py --ticker <TICKER> --form 10-K
```

Run that script with `--help` for the rest of its options. Fetched filings land in `data/edgar/` by
default and are not committed: they are real documents from a public source, so the `SYNTHETIC_`
prefix and the banner do not apply to them. Point `regulon ingest` at either directory, or at both.
