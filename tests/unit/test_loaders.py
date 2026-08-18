"""Tests for document loading and normalization.

Every fixture is written into ``tmp_path`` - the suite never touches ``data/`` and never opens a
network connection. Sample content is synthetic and carries no personal data.
"""

from pathlib import Path

import pytest

from regulon.ingestion import DocumentMetadata, DocumentParseError, SourceKind, UnsupportedSourceError
from regulon.ingestion.loaders import detect_kind, iter_source_files, load_document

# --- helpers ----------------------------------------------------------------------------


def write(tmp_path: Path, name: str, content: str, *, newline: str = "\n") -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.replace("\n", newline).encode("utf-8"))
    return path


def build_pdf(pages: list[list[str]]) -> bytes:
    """Assemble a minimal, valid single-font PDF whose pages hold the given lines of text."""
    kids = " ".join(f"{5 + 2 * index} 0 R" for index in range(len(pages)))
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for lines in pages:
        operators = ["BT", "/F1 12 Tf", "72 720 Td", "24 TL"]
        for index, line in enumerate(lines):
            if index:
                operators.append("T*")
            operators.append("({}) Tj".format(line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")))
        stream = "\n".join([*operators, "ET"]).encode("ascii")
        objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >>"
            b" /Contents " + str(len(objects)).encode() + b" 0 R >>"
        )
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode()
    return bytes(out)


def assert_sections_tile_the_text(document) -> None:
    """Every section slice must match its offsets, and the sections must tile the whole text."""
    assert document.sections, "a document always has at least one section"
    assert document.sections[0].start_char == 0
    assert document.sections[-1].end_char == len(document.text)
    for index, section in enumerate(document.sections):
        assert section.order == index
        assert document.text[section.start_char : section.end_char] == section.text
        if index:
            assert section.start_char == document.sections[index - 1].end_char


# --- detect_kind ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("filing.txt", SourceKind.TEXT),
        ("filing.md", SourceKind.MARKDOWN),
        ("filing.markdown", SourceKind.MARKDOWN),
        ("filing.html", SourceKind.HTML),
        ("filing.htm", SourceKind.HTML),
        ("filing.pdf", SourceKind.PDF),
        ("FILING.MD", SourceKind.MARKDOWN),
        ("FILING.HTML", SourceKind.HTML),
    ],
)
def test_detect_kind_maps_every_supported_extension(name, expected):
    assert detect_kind(Path(name)) is expected


@pytest.mark.parametrize("name", ["filing.docx", "filing.csv", "filing", "archive.tar.gz"])
def test_detect_kind_rejects_unsupported_extensions(name):
    with pytest.raises(UnsupportedSourceError, match="unsupported extension"):
        detect_kind(Path(name))


# --- iter_source_files ------------------------------------------------------------------


def test_iter_source_files_is_sorted_and_filters_by_extension(tmp_path):
    for name in ["z.txt", "a.md", "nested/b.html", "nested/deep/c.pdf", "notes.docx", "data.csv"]:
        write(tmp_path, name, "x")
    found = [path.relative_to(tmp_path).as_posix() for path in iter_source_files(tmp_path)]
    assert found == ["a.md", "nested/b.html", "nested/deep/c.pdf", "z.txt"]


def test_iter_source_files_is_stable_across_calls(tmp_path):
    for name in ["b.md", "a.md", "c/d.txt"]:
        write(tmp_path, name, "x")
    assert iter_source_files(tmp_path) == iter_source_files(tmp_path)


def test_iter_source_files_skips_hidden_files_and_directories(tmp_path):
    write(tmp_path, ".hidden.md", "x")
    write(tmp_path, ".cache/inner.md", "x")
    write(tmp_path, "visible.md", "x")
    assert [path.name for path in iter_source_files(tmp_path)] == ["visible.md"]


def test_iter_source_files_accepts_a_single_file_and_missing_root(tmp_path):
    supported = write(tmp_path, "a.md", "x")
    unsupported = write(tmp_path, "a.docx", "x")
    assert iter_source_files(supported) == [supported]
    assert iter_source_files(unsupported) == []
    assert iter_source_files(tmp_path / "does-not-exist") == []


# --- normalization ----------------------------------------------------------------------


def test_crlf_input_is_normalized_to_line_feeds(tmp_path):
    path = write(tmp_path, "filing.txt", "First line\nSecond line\n", newline="\r\n")
    document = load_document(path)
    assert "\r" not in document.text
    assert document.text == "First line\nSecond line"


def test_lone_carriage_returns_are_normalized(tmp_path):
    path = tmp_path / "filing.txt"
    path.write_bytes(b"First line\rSecond line")
    assert load_document(path).text == "First line\nSecond line"


def test_trailing_whitespace_and_blank_line_runs_are_collapsed(tmp_path):
    path = write(tmp_path, "filing.txt", "First line   \t\n\n\n\n\n\nSecond line\n\n\n")
    document = load_document(path)
    assert document.text == "First line\n\n\nSecond line"


def test_two_blank_lines_are_preserved(tmp_path):
    path = write(tmp_path, "filing.txt", "First line\n\n\nSecond line")
    assert load_document(path).text == "First line\n\n\nSecond line"


def test_byte_order_mark_is_stripped(tmp_path):
    path = tmp_path / "filing.txt"
    path.write_bytes(b"\xef\xbb\xbfFirst line")
    assert load_document(path).text == "First line"


def test_non_utf8_bytes_raise_document_parse_error(tmp_path):
    path = tmp_path / "filing.txt"
    path.write_bytes(b"\xff\xfe\x00broken")
    with pytest.raises(DocumentParseError, match="not valid UTF-8"):
        load_document(path)


def test_missing_file_raises_document_parse_error(tmp_path):
    with pytest.raises(DocumentParseError, match="cannot read source file"):
        load_document(tmp_path / "absent.txt")


# --- plain text sections ----------------------------------------------------------------


PLAIN_TEXT = """ITEM 1. BUSINESS

The issuer operates two segments.

Item 1A. Risk Factors

Markets move quickly and margins compress.
Concentration is the primary risk.
"""


def test_plain_text_splits_on_upper_case_and_short_heading_lines(tmp_path):
    document = load_document(write(tmp_path, "filing.txt", PLAIN_TEXT))
    assert document.kind is SourceKind.TEXT
    assert [section.heading for section in document.sections] == ["ITEM 1. BUSINESS", "Item 1A. Risk Factors"]
    assert_sections_tile_the_text(document)
    assert document.sections[0].text.startswith("ITEM 1. BUSINESS")
    assert "Concentration is the primary risk." in document.sections[1].text


def test_plain_text_offsets_match_the_document_text_exactly(tmp_path):
    document = load_document(write(tmp_path, "filing.txt", PLAIN_TEXT))
    second = document.sections[1]
    assert second.start_char == document.text.index("Item 1A. Risk Factors")
    assert document.text[second.start_char : second.end_char] == second.text


def test_prose_without_headings_falls_back_to_one_section(tmp_path):
    body = "Markets move quickly and margins compress in the reporting period.\n\nConcentration is the risk."
    document = load_document(write(tmp_path, "filing.txt", body))
    assert len(document.sections) == 1
    assert document.sections[0].heading is None
    assert document.sections[0].text == document.text
    assert_sections_tile_the_text(document)


def test_sentence_like_short_lines_are_not_headings(tmp_path):
    document = load_document(write(tmp_path, "filing.txt", "Markets move.\n\nMargins compress.\n"))
    assert [section.heading for section in document.sections] == [None]


def test_text_before_the_first_heading_becomes_a_headless_section(tmp_path):
    body = "This preamble precedes the first heading of the filing.\n\nRISK FACTORS\n\nMarkets move.\n"
    document = load_document(write(tmp_path, "filing.txt", body))
    assert [section.heading for section in document.sections] == [None, "RISK FACTORS"]
    assert document.sections[0].start_char == 0
    assert_sections_tile_the_text(document)


def test_empty_file_yields_a_single_empty_section(tmp_path):
    document = load_document(write(tmp_path, "filing.txt", ""))
    assert document.text == ""
    assert len(document.sections) == 1
    assert document.sections[0].start_char == document.sections[0].end_char == 0


# --- markdown ---------------------------------------------------------------------------


MARKDOWN = """# SYNTHETIC Annual Report

Prepared for study purposes only.

## Item 1. Business

The issuer operates two segments.

### Segment detail ###

Segment revenue grew.
"""


def test_markdown_splits_on_atx_headings(tmp_path):
    document = load_document(write(tmp_path, "filing.md", MARKDOWN))
    assert document.kind is SourceKind.MARKDOWN
    assert [section.heading for section in document.sections] == [
        "SYNTHETIC Annual Report",
        "Item 1. Business",
        "Segment detail",
    ]
    assert_sections_tile_the_text(document)
    assert document.sections[1].text.startswith("## Item 1. Business")


def test_markdown_ignores_hash_lines_inside_fenced_code(tmp_path):
    body = "# Real Heading\n\n```python\n# not a heading\n```\n\nBody text.\n"
    document = load_document(write(tmp_path, "filing.md", body))
    assert [section.heading for section in document.sections] == ["Real Heading"]
    assert "# not a heading" in document.text


def test_markdown_ignores_hash_without_a_space(tmp_path):
    document = load_document(write(tmp_path, "filing.md", "#hashtag is not a heading\n\nBody.\n"))
    assert [section.heading for section in document.sections] == [None]


def test_markdown_offsets_match_the_document_text_exactly(tmp_path):
    document = load_document(write(tmp_path, "filing.md", MARKDOWN))
    for section in document.sections:
        assert document.text[section.start_char : section.end_char] == section.text
    assert document.sections[2].start_char == document.text.index("### Segment detail")


# --- front matter -----------------------------------------------------------------------


FRONT_MATTER_DOC = """---
title: SYNTHETIC Annual Report
ticker: ACME
form: 10-K
fiscal_year: 2024
synthetic: true
cik: "0000000001"
---

# Item 1. Business

The issuer operates two segments.
"""


def test_front_matter_populates_metadata_and_is_stripped_from_the_body(tmp_path):
    document = load_document(write(tmp_path, "filing.md", FRONT_MATTER_DOC))
    assert document.text.startswith("# Item 1. Business")
    assert "title:" not in document.text
    assert document.metadata.title == "SYNTHETIC Annual Report"
    assert document.metadata.ticker == "ACME"
    assert document.metadata.form == "10-K"
    assert document.metadata.fiscal_year == 2024
    assert document.metadata.is_synthetic is True
    assert document.metadata.extra == {"cik": "0000000001"}
    assert_sections_tile_the_text(document)


def test_front_matter_accepts_the_is_synthetic_spelling(tmp_path):
    document = load_document(write(tmp_path, "filing.md", "---\nis_synthetic: true\n---\n\nBody text.\n"))
    assert document.metadata.is_synthetic is True


def test_explicit_metadata_wins_over_front_matter(tmp_path):
    path = write(tmp_path, "filing.md", FRONT_MATTER_DOC)
    override = DocumentMetadata(source_path="data/synthetic/SYNTHETIC-acme-10k.md", ticker="ZZZ")
    document = load_document(path, metadata=override)
    assert document.metadata.source_path == "data/synthetic/SYNTHETIC-acme-10k.md"
    assert document.metadata.ticker == "ZZZ"
    # Fields the caller did not set still come from the front matter.
    assert document.metadata.title == "SYNTHETIC Annual Report"
    assert document.metadata.fiscal_year == 2024


def test_source_path_defaults_to_the_posix_form_of_the_path(tmp_path):
    path = write(tmp_path, "nested/filing.txt", "Body text.")
    assert load_document(path).metadata.source_path == path.as_posix()


def test_front_matter_keys_without_values_are_ignored(tmp_path):
    body = "---\ntitle:\nnote:\nticker: ACME\n---\n\nBody text.\n"
    document = load_document(write(tmp_path, "filing.md", body))
    assert document.metadata.title is None
    assert document.metadata.ticker == "ACME"
    assert document.metadata.extra == {"note": ""}


def test_comment_only_front_matter_is_stripped_without_metadata(tmp_path):
    document = load_document(write(tmp_path, "filing.md", "---\n# nothing to declare\n---\n\nBody text.\n"))
    assert document.text == "Body text."
    assert document.metadata.title is None


def test_invalid_front_matter_yaml_raises_document_parse_error(tmp_path):
    path = write(tmp_path, "filing.md", "---\ntitle: [unclosed\n---\n\nBody.\n")
    with pytest.raises(DocumentParseError, match="front matter is not valid YAML"):
        load_document(path)


def test_invalid_front_matter_value_raises_document_parse_error(tmp_path):
    path = write(tmp_path, "filing.md", "---\nfiscal_year: last year\n---\n\nBody.\n")
    with pytest.raises(DocumentParseError, match="invalid document metadata"):
        load_document(path)


def test_a_leading_thematic_break_is_not_treated_as_front_matter(tmp_path):
    body = "---\n\nMarkets move quickly and margins compress.\n\n---\n\nMore body text here.\n"
    document = load_document(write(tmp_path, "filing.md", body))
    assert document.text.startswith("---")
    assert document.metadata.title is None


def test_front_matter_only_applies_to_markdown(tmp_path):
    document = load_document(write(tmp_path, "filing.txt", "---\ntitle: Ignored\n---\n\nBody text.\n"))
    assert document.metadata.title is None
    assert document.text.startswith("---")


# --- html -------------------------------------------------------------------------------


HTML_DOC = """<html>
  <head>
    <title>SYNTHETIC Filing &mdash; Acme</title>
    <style>body { color: #101010; }</style>
    <script>var injected = "<h1>not a heading</h1>";</script>
  </head>
  <body>
    <h1>Item 1. Business</h1>
    <p>Revenue &amp; margin grew.&nbsp;Segments: two.</p>
    <h2>Item 1A. Risk Factors</h2>
    <p>Markets move.</p>
  </body>
</html>
"""


def test_html_extracts_text_and_splits_on_headings(tmp_path):
    document = load_document(write(tmp_path, "filing.html", HTML_DOC))
    assert document.kind is SourceKind.HTML
    assert [section.heading for section in document.sections] == ["Item 1. Business", "Item 1A. Risk Factors"]
    assert_sections_tile_the_text(document)
    assert document.sections[1].start_char == document.text.index("Item 1A. Risk Factors")


def test_html_decodes_entities(tmp_path):
    document = load_document(write(tmp_path, "filing.html", HTML_DOC))
    assert "Revenue & margin grew. Segments: two." in document.text
    assert "&amp;" not in document.text
    assert "\xa0" not in document.text
    assert document.metadata.title == "SYNTHETIC Filing — Acme"


def test_html_drops_script_and_style_content(tmp_path):
    document = load_document(write(tmp_path, "filing.html", HTML_DOC))
    assert "injected" not in document.text
    assert "not a heading" not in document.text
    assert "#101010" not in document.text
    assert [section.heading for section in document.sections] == ["Item 1. Business", "Item 1A. Risk Factors"]


def test_html_block_elements_become_line_breaks(tmp_path):
    body = "<div>First block</div><p>Second block</p><ul><li>One</li><li>Two</li></ul>"
    document = load_document(write(tmp_path, "filing.htm", body))
    assert document.text.split("\n") == ["First block", "Second block", "One", "Two"]


def test_html_collapses_insignificant_whitespace(tmp_path):
    document = load_document(write(tmp_path, "filing.html", "<p>Revenue\n     grew\t\tsharply.</p>"))
    assert document.text == "Revenue grew sharply."


def test_html_preserves_preformatted_blocks(tmp_path):
    document = load_document(write(tmp_path, "filing.html", "<pre>Segment    A\nSegment    B</pre>"))
    assert document.text == "Segment    A\nSegment    B"


def test_html_ignores_empty_headings(tmp_path):
    document = load_document(write(tmp_path, "filing.html", "<h2></h2><p>Body text.</p>"))
    assert [section.heading for section in document.sections] == [None]
    assert document.text == "Body text."


def test_html_explicit_metadata_wins_over_the_document_title(tmp_path):
    path = write(tmp_path, "filing.html", HTML_DOC)
    document = load_document(path, metadata=DocumentMetadata(source_path=path.as_posix(), title="Given Title"))
    assert document.metadata.title == "Given Title"


# --- pdf --------------------------------------------------------------------------------


def test_pdf_text_is_extracted_and_sectioned(tmp_path):
    path = tmp_path / "filing.pdf"
    path.write_bytes(build_pdf([["RISK FACTORS", " ", "Markets move quickly."]]))
    document = load_document(path)
    assert document.kind is SourceKind.PDF
    assert "Markets move quickly." in document.text
    assert [section.heading for section in document.sections] == ["RISK FACTORS"]
    assert_sections_tile_the_text(document)


def test_pdf_pages_are_joined_in_order(tmp_path):
    path = tmp_path / "filing.pdf"
    path.write_bytes(build_pdf([["Page one body."], ["Page two body."]]))
    document = load_document(path)
    assert document.text.index("Page one body.") < document.text.index("Page two body.")


def test_corrupt_pdf_raises_document_parse_error(tmp_path):
    path = tmp_path / "filing.pdf"
    path.write_bytes(b"this is not a PDF at all")
    with pytest.raises(DocumentParseError, match="cannot parse PDF"):
        load_document(path)


def test_missing_pdf_raises_document_parse_error(tmp_path):
    with pytest.raises(DocumentParseError, match="cannot parse PDF"):
        load_document(tmp_path / "absent.pdf")


def test_pdf_without_extractable_text_raises_document_parse_error(tmp_path):
    path = tmp_path / "scanned.pdf"
    path.write_bytes(build_pdf([[]]))
    with pytest.raises(DocumentParseError, match="no extractable text"):
        load_document(path)


# --- unsupported sources and determinism ------------------------------------------------


def test_load_document_rejects_an_unsupported_extension(tmp_path):
    path = write(tmp_path, "filing.docx", "Body text.")
    with pytest.raises(UnsupportedSourceError):
        load_document(path)


def test_loading_is_deterministic(tmp_path):
    path = write(tmp_path, "filing.md", MARKDOWN)
    first, second = load_document(path), load_document(path)
    assert first == second
    assert first.document_id == second.document_id
    assert first.document_id.startswith("doc_")


def test_document_id_tracks_content_and_source_path(tmp_path):
    base = load_document(write(tmp_path, "a.md", MARKDOWN))
    same_text_other_path = load_document(write(tmp_path, "b.md", MARKDOWN))
    other_text = load_document(write(tmp_path, "c.md", MARKDOWN + "\nOne more line.\n"))
    assert base.document_id != same_text_other_path.document_id
    assert base.document_id != other_text.document_id


def test_document_id_ignores_line_ending_style(tmp_path):
    unix = load_document(write(tmp_path, "unix/filing.txt", PLAIN_TEXT))
    windows = load_document(write(tmp_path, "windows/filing.txt", PLAIN_TEXT, newline="\r\n"))
    assert unix.text == windows.text
    metadata = DocumentMetadata(source_path="data/synthetic/filing.txt")
    assert (
        load_document(tmp_path / "unix/filing.txt", metadata=metadata).document_id
        == load_document(tmp_path / "windows/filing.txt", metadata=metadata).document_id
    )
