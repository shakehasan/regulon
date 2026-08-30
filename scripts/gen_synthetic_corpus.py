#!/usr/bin/env python3
"""Generate the bundled synthetic corpus of annual-report-style sample documents.

Quorum's data hygiene rule (AGENTS.md, "Public safety") permits exactly two data sources:
public-domain SEC EDGAR filings, fetched on demand, and synthetic documents labeled ``SYNTHETIC``
in filename, front matter, and docs. This script writes the second kind, so the demo, the tests,
and the tutorials always have something to ingest with no network call and no licensing question.

Everything in the output is invented. The fictional text lives in ``synthetic_corpus_content.py``;
this module draws the figures, fills the templates, and writes the files.

Usage::

    python scripts/gen_synthetic_corpus.py [--out-dir data/samples] [--seed N] [--count N]

Output is deterministic: the same ``--seed`` and ``--count`` always produce byte-identical files, so
regenerating the committed corpus shows up as an empty diff. Each document is seeded from the corpus
seed plus its own company and fiscal year, so its content does not depend on how many other
documents were requested. The corpus-shape constants below are pinned in code, not in
``config/quorum.yaml``, because the generated files are committed: reproducing them must depend on
this script alone and not on a config revision.
"""

from __future__ import annotations

import argparse
import random
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

from synthetic_corpus_content import (
    BANNER,
    DISCLAIMER,
    FRONT_MATTER,
    GENERATOR_PATH,
    HEADWINDS,
    PLACEHOLDER_DOMAIN,
    PROFILES,
    SECTIONS,
    STREET_NAMES,
    TITLE,
    CompanyProfile,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "samples"
DEFAULT_SEED = 20260101
DEFAULT_COUNT = 4

FILENAME_PREFIX = "SYNTHETIC_"
FILENAME_GLOB = f"{FILENAME_PREFIX}*.md"
FORM_TYPE = "10-K"
BASE_FISCAL_YEAR = 2022
FISCAL_YEAR_SPAN = 4
MAX_DOCUMENTS = len(PROFILES) * FISCAL_YEAR_SPAN
WRAP_WIDTH = 100
# Blocks starting with one of these are emitted as written: tables, list items, subheadings.
VERBATIM_PREFIXES = ("|", "-", "#", ">")

# Ranges the invented figures are drawn from. They shape the sample corpus, which is a committed
# fixture, rather than Quorum's runtime behavior, which is why they live here and not in config/.
REVENUE_RANGE_MUSD = (620.0, 4200.0)
SEGMENT_WEIGHT_RANGE = (1.0, 3.2)
SEGMENT_GROWTH_RANGE = (-0.09, 0.24)
GROSS_MARGIN_RANGE = (0.26, 0.52)
OPERATING_CONVERSION_RANGE = (0.28, 0.55)
FCF_CONVERSION_RANGE = (0.35, 0.72)
# Prior-year ratios drift from the current year rather than being drawn independently, so the
# comparative columns move by a point or two instead of jumping implausibly.
RATIO_DRIFT_RANGE = (-0.035, 0.035)
MIN_RATIO = 0.12
CAPEX_RATIO_RANGE = (0.03, 0.11)
CASH_RATIO_RANGE = (0.05, 0.20)
REVOLVER_RATIO_RANGE = (0.10, 0.30)
BACKLOG_RATIO_RANGE = (0.40, 1.30)
EMPLOYEES_PER_MUSD_RANGE = (2.0, 5.0)
EMPLOYEE_GROWTH_RANGE = (-0.03, 0.09)
LEVERAGE_RANGE = (0.8, 3.2)
CONTRACTED_SHARE_RANGE = (45.0, 80.0)
LOCATION_COUNT_RANGE = (12, 90)
ACCOUNT_COUNT_RANGE = (400, 9000)
STREET_NUMBER_RANGE = (100, 990)
# Reserved fictional telephone extensions: 555-0100 through 555-0199.
PHONE_EXTENSION_RANGE = (100, 200)


@dataclass(frozen=True)
class Figures:
    """Invented amounts for one document.

    Segment revenues are drawn first and the totals derive from them, so every table in a rendered
    document adds up and the year-on-year columns agree with each other.
    """

    segments: tuple[float, ...]
    prior_segments: tuple[float, ...]
    revenue: float
    prior_revenue: float
    gross: float
    prior_gross: float
    operating: float
    prior_operating: float
    fcf: float
    prior_fcf: float
    capex: float
    cash: float
    revolver: float
    leverage: float
    backlog: float
    contracted_pct: float
    employees: int
    prior_employees: int
    locations: int
    accounts: int


def _money(value: float) -> str:
    """Format an amount in millions for prose or a table cell."""
    return f"{value:,.1f}"


def _change(current: float, prior: float) -> str:
    """Format a signed percentage change between two amounts."""
    return f"{(current / prior - 1.0) * 100.0:+.1f}%"


def _share(part: float, whole: float) -> float:
    """Return one amount as a percentage of another."""
    return part / whole * 100.0


def _drift(rng: random.Random, ratio: float) -> float:
    """Return a prior-year ratio near ``ratio``, never dropping to an implausible level."""
    return max(MIN_RATIO, ratio + rng.uniform(*RATIO_DRIFT_RANGE))


def _table(header: tuple[str, ...], rows: tuple[tuple[str, ...], ...]) -> str:
    """Render a Markdown pipe table."""
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def _rows(spec: tuple[tuple[str, str, float, float], ...]) -> tuple[tuple[str, ...], ...]:
    """Format ``(label, kind, current, prior)`` rows for :func:`_table`.

    Kind ``money`` renders one decimal with a percentage change, ``percent`` one decimal with a
    change in points, and ``count`` whole numbers with a percentage change.
    """
    rendered: list[tuple[str, ...]] = []
    for label, kind, now, before in spec:
        if kind == "percent":
            rendered.append((label, f"{now:.1f}", f"{before:.1f}", f"{now - before:+.1f} pts"))
        elif kind == "count":
            rendered.append((label, f"{now:,.0f}", f"{before:,.0f}", _change(now, before)))
        else:
            rendered.append((label, _money(now), _money(before), _change(now, before)))
    return tuple(rendered)


def draw_figures(rng: random.Random, profile: CompanyProfile) -> Figures:
    """Draw one document's invented figures from a seeded random source.

    The profile fixes how many segment lines are drawn; the segments then determine every total,
    so the fiscal year and its comparative stay mutually consistent.
    """
    weights = [rng.uniform(*SEGMENT_WEIGHT_RANGE) for _ in profile.segments]
    target = rng.uniform(*REVENUE_RANGE_MUSD)
    segments = tuple(round(target * weight / sum(weights), 1) for weight in weights)
    prior_segments = tuple(round(value / (1.0 + rng.uniform(*SEGMENT_GROWTH_RANGE)), 1) for value in segments)
    revenue = round(sum(segments), 1)
    prior_revenue = round(sum(prior_segments), 1)
    gross_margin = rng.uniform(*GROSS_MARGIN_RANGE)
    operating_conversion = rng.uniform(*OPERATING_CONVERSION_RANGE)
    fcf_conversion = rng.uniform(*FCF_CONVERSION_RANGE)
    gross = round(revenue * gross_margin, 1)
    prior_gross = round(prior_revenue * _drift(rng, gross_margin), 1)
    operating = round(gross * operating_conversion, 1)
    prior_operating = round(prior_gross * _drift(rng, operating_conversion), 1)
    employees = int(revenue * rng.uniform(*EMPLOYEES_PER_MUSD_RANGE))
    return Figures(
        segments=segments,
        prior_segments=prior_segments,
        revenue=revenue,
        prior_revenue=prior_revenue,
        gross=gross,
        prior_gross=prior_gross,
        operating=operating,
        prior_operating=prior_operating,
        fcf=round(operating * fcf_conversion, 1),
        prior_fcf=round(prior_operating * _drift(rng, fcf_conversion), 1),
        capex=round(revenue * rng.uniform(*CAPEX_RATIO_RANGE), 1),
        cash=round(revenue * rng.uniform(*CASH_RATIO_RANGE), 1),
        revolver=round(revenue * rng.uniform(*REVOLVER_RATIO_RANGE), 1),
        leverage=round(rng.uniform(*LEVERAGE_RANGE), 1),
        backlog=round(revenue * rng.uniform(*BACKLOG_RATIO_RANGE), 1),
        contracted_pct=round(rng.uniform(*CONTRACTED_SHARE_RANGE)),
        employees=employees,
        prior_employees=int(employees / (1.0 + rng.uniform(*EMPLOYEE_GROWTH_RANGE))),
        locations=rng.randrange(*LOCATION_COUNT_RANGE),
        accounts=rng.randrange(*ACCOUNT_COUNT_RANGE),
    )


def _financial_table(figures: Figures, fiscal_year: int) -> str:
    """Render the selected financial data table."""
    spec = (
        ("Revenue ($M)", "money", figures.revenue, figures.prior_revenue),
        ("Gross profit ($M)", "money", figures.gross, figures.prior_gross),
        (
            "Gross margin (%)",
            "percent",
            _share(figures.gross, figures.revenue),
            _share(figures.prior_gross, figures.prior_revenue),
        ),
        ("Operating income ($M)", "money", figures.operating, figures.prior_operating),
        (
            "Operating margin (%)",
            "percent",
            _share(figures.operating, figures.revenue),
            _share(figures.prior_operating, figures.prior_revenue),
        ),
        ("Free cash flow ($M)", "money", figures.fcf, figures.prior_fcf),
        ("Employees (FTE)", "count", figures.employees, figures.prior_employees),
    )
    header = ("Metric", f"FY{fiscal_year}", f"FY{fiscal_year - 1}", "Change")
    return _table(header, _rows(spec))


def _segment_table(profile: CompanyProfile, figures: Figures, fiscal_year: int) -> str:
    """Render the per-segment revenue table, including a total line."""
    revenue, prior_revenue = figures.revenue, figures.prior_revenue
    lines = tuple(
        (name, _money(now), f"{_share(now, revenue):.1f}", _money(before), _change(now, before))
        for name, now, before in zip(profile.segments, figures.segments, figures.prior_segments, strict=True)
    )
    total = ("Total", _money(revenue), "100.0", _money(prior_revenue), _change(revenue, prior_revenue))
    header = ("Segment", f"FY{fiscal_year} ($M)", "Share (%)", f"FY{fiscal_year - 1} ($M)", "Change")
    return _table(header, (*lines, total))


def _document_values(
    profile: CompanyProfile, fiscal_year: int, seed: int, figures: Figures, rng: random.Random
) -> dict[str, str]:
    """Build every substitution the front matter and section templates need, pre-formatted."""
    lead_segment, lead_revenue = max(zip(profile.segments, figures.segments, strict=True), key=lambda pair: pair[1])
    gross_margin = _share(figures.gross, figures.revenue)
    prior_gross_margin = _share(figures.prior_gross, figures.prior_revenue)
    return {
        "company": profile.name,
        "ticker": profile.ticker,
        "industry": profile.industry,
        "headquarters": profile.headquarters,
        "form": FORM_TYPE,
        "fiscal_year": str(fiscal_year),
        "prior_year": str(fiscal_year - 1),
        "seed": str(seed),
        "disclaimer": DISCLAIMER,
        "generator": GENERATOR_PATH,
        "segment_count": str(len(profile.segments)),
        "segment_list": ", ".join(profile.segments),
        "accounts": f"{figures.accounts:,}",
        "locations": str(figures.locations),
        "employees": f"{figures.employees:,}",
        "prior_employees": f"{figures.prior_employees:,}",
        "contracted_pct": f"{figures.contracted_pct:.0f}",
        "revenue": _money(figures.revenue),
        "prior_revenue": _money(figures.prior_revenue),
        "revenue_change": _change(figures.revenue, figures.prior_revenue),
        "gross_margin": f"{gross_margin:.1f}",
        "prior_gross_margin": f"{prior_gross_margin:.1f}",
        "gross_margin_points": f"{gross_margin - prior_gross_margin:+.1f} points",
        "operating_margin": f"{_share(figures.operating, figures.revenue):.1f}",
        "operating_cash_flow": _money(figures.fcf + figures.capex),
        "fcf": _money(figures.fcf),
        "capex": _money(figures.capex),
        "capex_pct": f"{_share(figures.capex, figures.revenue):.1f}",
        "backlog": _money(figures.backlog),
        "backlog_pct": f"{_share(figures.backlog, figures.revenue):.0f}",
        "cash": _money(figures.cash),
        "revolver": _money(figures.revolver),
        "headroom": _money(figures.cash + figures.revolver),
        "leverage": f"{figures.leverage:.1f}",
        "lead_segment": lead_segment,
        "lead_revenue": _money(lead_revenue),
        "lead_share": f"{_share(lead_revenue, figures.revenue):.1f}",
        "driver": rng.choice(profile.growth_drivers),
        "headwind": rng.choice(HEADWINDS),
        "financial_table": _financial_table(figures, fiscal_year),
        "segment_table": _segment_table(profile, figures, fiscal_year),
        "company_risks": profile.risks,
        "street": f"{rng.randrange(*STREET_NUMBER_RANGE)} {rng.choice(STREET_NAMES)}",
        "email": f"investor.relations@{PLACEHOLDER_DOMAIN}",
        "media_email": f"media.desk@{PLACEHOLDER_DOMAIN}",
        "phone": f"+1 (555) 555-{rng.randrange(*PHONE_EXTENSION_RANGE):04d}",
    }


def _render_template(template: str, values: dict[str, str]) -> str:
    """Fill a template and re-wrap its prose, emitting verbatim blocks unchanged."""
    blocks = [block for block in template.format(**values).strip().split("\n\n") if block]
    return "\n\n".join(
        block
        if block.startswith(VERBATIM_PREFIXES)
        else textwrap.fill(block, width=WRAP_WIDTH, break_long_words=False, break_on_hyphens=False)
        for block in blocks
    )


def render_document(profile: CompanyProfile, fiscal_year: int, seed: int) -> str:
    """Render one complete synthetic filing as Markdown.

    The result is front matter labeling the document synthetic, the banner as the first body line,
    a title, then one ``##`` section per entry in ``SECTIONS``. It ends with a single newline. The
    corpus ``seed`` is combined with the company and fiscal year to seed this document alone.
    """
    rng = random.Random(f"{seed}:{profile.slug}:{fiscal_year}")
    values = _document_values(profile, fiscal_year, seed, draw_figures(rng, profile), rng)
    blocks = [FRONT_MATTER.format(**values), BANNER.format(**values), TITLE.format(**values)]
    blocks.extend(f"## {heading}\n\n{_render_template(template, values)}" for heading, template in SECTIONS)
    return "\n\n".join(blocks) + "\n"


def document_filename(profile: CompanyProfile, fiscal_year: int) -> str:
    """Return the SYNTHETIC-prefixed filename for one company and fiscal year."""
    return f"{FILENAME_PREFIX}{profile.slug}_{FORM_TYPE}_FY{fiscal_year}.md"


def corpus_plan(count: int) -> tuple[tuple[CompanyProfile, int], ...]:
    """Return one ``(profile, fiscal_year)`` pair per document, in generation order.

    Company and fiscal year advance on cycles of different length, so consecutive documents differ
    in both and every pair up to :data:`MAX_DOCUMENTS` is distinct.

    Raises:
        ValueError: If ``count`` is outside ``1..MAX_DOCUMENTS``.
    """
    if not 1 <= count <= MAX_DOCUMENTS:
        raise ValueError(f"count must be between 1 and {MAX_DOCUMENTS} (one document per company and fiscal year)")
    return tuple((PROFILES[i % len(PROFILES)], BASE_FISCAL_YEAR + i % FISCAL_YEAR_SPAN) for i in range(count))


def generate_corpus(out_dir: Path, seed: int = DEFAULT_SEED, count: int = DEFAULT_COUNT) -> list[Path]:
    """Write the corpus into ``out_dir``, creating it if missing, and return the written paths.

    Existing ``SYNTHETIC_*.md`` files are removed first, so a smaller ``count`` never leaves stale
    documents behind; every other file, such as the directory README, is left alone.

    Raises:
        ValueError: If ``count`` is outside ``1..MAX_DOCUMENTS``.
    """
    plan = corpus_plan(count)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in sorted(out_dir.glob(FILENAME_GLOB)):
        stale.unlink()

    written: list[Path] = []
    for profile, fiscal_year in plan:
        path = out_dir / document_filename(profile, fiscal_year)
        path.write_text(render_document(profile, fiscal_year, seed), encoding="utf-8", newline="\n")
        written.append(path)
    return written


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description="Generate the bundled SYNTHETIC sample corpus.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="Directory to write documents into")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Seed fixing the generated content")
    parser.add_argument(
        "--count", type=int, default=DEFAULT_COUNT, help=f"Number of documents to generate (1..{MAX_DOCUMENTS})"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint. Returns the process exit code."""
    args = build_parser().parse_args(argv)
    out_dir: Path = args.out_dir
    seed: int = args.seed
    count: int = args.count

    try:
        written = generate_corpus(out_dir=out_dir, seed=seed, count=count)
    except ValueError as exc:
        print(f"gen_synthetic_corpus: {exc}", file=sys.stderr)
        return 2

    for path in written:
        print(f"gen_synthetic_corpus: wrote {path}")
    print(f"gen_synthetic_corpus: {len(written)} synthetic document(s) in {out_dir} (seed {seed})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
