"""Tests for scripts/gen_synthetic_corpus.py.

The generated corpus is committed, so these tests pin the properties the repo depends on:
determinism, the three-way SYNTHETIC labeling, redactable contact placeholders, and the absence of
anything the public-safety scanner would reject. The SSN-shaped pattern is written as a regex, never
as a literal digit sequence, so this file stays clean under that scanner.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from quorum.core.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import gen_synthetic_corpus as gen  # noqa: E402

SSN_SHAPED = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
FICTIONAL_PHONE = re.compile(r"\+1 \(555\) 555-01\d{2}")
BANNER_PREFIX = "> **SYNTHETIC DOCUMENT"


def corpus_text(out_dir: Path) -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8") for path in sorted(out_dir.glob("*.md"))}


def body_lines(text: str) -> list[str]:
    """Return the document lines after the closing front-matter delimiter."""
    assert text.startswith("---\n")
    return text.split("\n---\n", 1)[1].splitlines()


def front_matter(text: str) -> str:
    return text.split("\n---\n", 1)[0]


@pytest.fixture(scope="module")
def default_corpus(tmp_path_factory) -> dict[str, str]:
    out_dir = tmp_path_factory.mktemp("corpus")
    gen.generate_corpus(out_dir)
    return corpus_text(out_dir)


def test_default_corpus_has_the_documented_shape(default_corpus):
    assert len(default_corpus) == gen.DEFAULT_COUNT
    assert all(name.startswith(gen.FILENAME_PREFIX) and name.endswith(".md") for name in default_corpus)


@pytest.mark.parametrize("count", [1, 3, gen.MAX_DOCUMENTS])
def test_count_is_respected_and_filenames_stay_unique(tmp_path, count):
    written = gen.generate_corpus(tmp_path, count=count)
    assert len(written) == count
    assert len({path.name for path in written}) == count


def test_same_seed_produces_identical_bytes(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    gen.generate_corpus(first, seed=101, count=3)
    gen.generate_corpus(second, seed=101, count=3)
    assert corpus_text(first) == corpus_text(second)


def test_determinism_survives_a_fresh_interpreter(tmp_path):
    """Ids drawn from a string-seeded Random must not depend on process hash randomization."""
    runs = []
    for salt in ("0", "1"):
        out_dir = tmp_path / f"run{salt}"
        env = {**os.environ, "PYTHONHASHSEED": salt}
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "gen_synthetic_corpus.py"), "--out-dir", str(out_dir), "--count", "2"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        runs.append(corpus_text(out_dir))
    assert runs[0] == runs[1]


def test_different_seeds_produce_different_content(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    gen.generate_corpus(first, seed=101, count=3)
    gen.generate_corpus(second, seed=202, count=3)
    left, right = corpus_text(first), corpus_text(second)
    assert left.keys() == right.keys()
    assert all(left[name] != right[name] for name in left)


def test_front_matter_labels_every_document_synthetic(default_corpus):
    for name, text in default_corpus.items():
        header = front_matter(text)
        assert header.startswith("---\n"), name
        assert "\nsynthetic: true" in header, name
        assert "\ndisclaimer: " in header, name
        for field in ("title", "company", "ticker", "form", "fiscal_year", "generator", "seed"):
            assert f"\n{field}: " in header, f"{name} is missing {field}"


def test_banner_is_the_first_body_line(default_corpus):
    for name, text in default_corpus.items():
        first_line = next(line for line in body_lines(text) if line.strip())
        assert first_line.startswith(BANNER_PREFIX), name
        assert "NOT REAL FINANCIAL DATA" in first_line, name
        assert "invented sample data" in first_line, name
        assert "nothing here is investment advice" in first_line, name


def test_documents_carry_chunkable_sections(default_corpus, monkeypatch):
    monkeypatch.setenv("QUORUM_CONFIG_FILE", str(REPO_ROOT / "config" / "quorum.yaml"))
    max_chars = Settings().ingestion.chunking.max_chars
    for name, text in default_corpus.items():
        headings = [line for line in text.splitlines() if line.startswith("## ")]
        assert "## Risk Factors" in headings, name
        assert "## Management Discussion and Analysis" in headings, name
        assert len(headings) >= 6, name
        assert len(text) > 3 * max_chars, f"{name} is too short to produce several chunks"


def test_every_template_placeholder_was_substituted(default_corpus):
    for name, text in default_corpus.items():
        assert "{" not in text and "}" not in text, f"{name} still contains an unfilled placeholder"


def test_contact_details_are_redaction_fodder(default_corpus):
    for name, text in default_corpus.items():
        emails = EMAIL.findall(text)
        assert emails, f"{name} has no address for the redactor to find"
        assert all(address.endswith("@" + "example.com") for address in emails), name
        assert FICTIONAL_PHONE.search(text), name


def test_corpus_contains_no_ssn_shaped_numbers(tmp_path):
    gen.generate_corpus(tmp_path, count=gen.MAX_DOCUMENTS)
    for name, text in corpus_text(tmp_path).items():
        assert not SSN_SHAPED.search(text), f"{name} contains an SSN-shaped number"


def test_committed_corpus_matches_the_generator(tmp_path):
    """The files under data/samples must be exactly what the default invocation writes."""
    gen.generate_corpus(tmp_path)
    committed = {path.name: path.read_text(encoding="utf-8") for path in gen.DEFAULT_OUT_DIR.glob(gen.FILENAME_GLOB)}
    assert committed == corpus_text(tmp_path)


def test_documents_are_clean_markdown(default_corpus):
    for name, text in default_corpus.items():
        assert text.endswith("\n") and not text.endswith("\n\n"), name
        assert not any(line != line.rstrip() for line in text.splitlines()), f"{name} has trailing whitespace"


def test_corpus_spans_several_companies_and_fiscal_years(default_corpus):
    headers = [front_matter(text) for text in default_corpus.values()]
    tickers = {re.search(r'ticker: "([^"]+)"', header).group(1) for header in headers}
    years = {re.search(r"fiscal_year: (\d+)", header).group(1) for header in headers}
    assert len(tickers) >= 2
    assert len(years) >= 3
    assert all(ticker.endswith(".TEST") for ticker in tickers)


def test_regeneration_replaces_stale_documents_but_keeps_other_files(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("kept\n", encoding="utf-8")
    gen.generate_corpus(tmp_path, count=gen.MAX_DOCUMENTS)
    gen.generate_corpus(tmp_path, count=2)
    assert len(list(tmp_path.glob(gen.FILENAME_GLOB))) == 2
    assert readme.read_text(encoding="utf-8") == "kept\n"


@pytest.mark.parametrize("count", [0, -1, gen.MAX_DOCUMENTS + 1])
def test_out_of_range_count_is_rejected(tmp_path, count):
    with pytest.raises(ValueError, match="count must be between"):
        gen.generate_corpus(tmp_path, count=count)


def test_cli_writes_the_corpus(tmp_path, capsys):
    assert gen.main(["--out-dir", str(tmp_path), "--count", "2", "--seed", "5"]) == 0
    assert len(list(tmp_path.glob(gen.FILENAME_GLOB))) == 2
    assert "2 synthetic document(s)" in capsys.readouterr().out


def test_cli_reports_an_invalid_count(tmp_path, capsys):
    assert gen.main(["--out-dir", str(tmp_path), "--count", "0"]) == 2
    assert "count must be between" in capsys.readouterr().err
    assert not list(tmp_path.glob(gen.FILENAME_GLOB))
