"""Tests for the semantic-aware chunker.

The offset invariant is the load-bearing one: citations replay ``document.text[start:end]``, so
every test that produces chunks also asserts exactness. Sample text is synthetic filler built
from a fixed word list; it contains no personal data and no real filing content.
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise

import pytest

from regulon.core.config import ChunkingSettings
from regulon.ingestion.chunking import Chunker
from regulon.ingestion.models import (
    Chunk,
    DocumentMetadata,
    NormalizedDocument,
    Section,
    SourceKind,
    make_document_id,
)

WORDS = ("revenue", "segment", "filing", "reviews", "policy", "capital", "risk", "markets")

SOURCE_PATH = "data/synthetic/SYNTHETIC-acme-10k.md"


def filler(char_count: int) -> str:
    """Return deterministic space-separated filler of at least ``char_count`` characters."""
    parts: list[str] = []
    total = 0
    while total < char_count:
        word = WORDS[len(parts) % len(WORDS)]
        parts.append(word)
        total += len(word) + 1
    return " ".join(parts)


def sentences(count: int) -> str:
    """Return ``count`` filler sentences separated by a single space, each ending in a period."""
    return " ".join(f"{filler(40)}." for _ in range(count))


def make_document(parts: Sequence[tuple[str | None, str]]) -> NormalizedDocument:
    """Build a document whose sections tile the text exactly, in the given order."""
    text = "".join(body for _, body in parts)
    sections: list[Section] = []
    cursor = 0
    for order, (heading, body) in enumerate(parts):
        sections.append(
            Section(order=order, heading=heading, text=body, start_char=cursor, end_char=cursor + len(body))
        )
        cursor += len(body)
    metadata = DocumentMetadata(
        source_path=SOURCE_PATH,
        title="SYNTHETIC Annual Report",
        ticker="ACME",
        form="10-K",
        fiscal_year=2024,
        is_synthetic=True,
    )
    return NormalizedDocument(
        document_id=make_document_id(text, metadata.source_path),
        kind=SourceKind.MARKDOWN,
        text=text,
        sections=sections,
        metadata=metadata,
    )


def filing_parts() -> list[tuple[str | None, str]]:
    """Return a three-section filing-shaped document body."""
    return [
        ("Item 1. Business", f"Item 1. Business\n\n{filler(400)}\n\n{filler(400)}\n\n"),
        ("Item 1A. Risk Factors", f"Item 1A. Risk Factors\n\n{filler(600)}\n\n"),
        ("Item 7. MD&A", f"Item 7. MD&A\n\n{filler(300)}"),
    ]


def settings(max_chars: int = 250, overlap_chars: int = 30, min_chars: int = 60) -> ChunkingSettings:
    """Return chunking settings with test-sized bounds."""
    return ChunkingSettings(max_chars=max_chars, overlap_chars=overlap_chars, min_chars=min_chars)


def assert_offsets_exact(document: NormalizedDocument, chunks: Sequence[Chunk]) -> None:
    """Assert every chunk's offsets slice its own text out of the document."""
    for chunk in chunks:
        assert document.text[chunk.start_char : chunk.end_char] == chunk.text
        assert chunk.start_char < chunk.end_char


def uncovered_indices(document: NormalizedDocument, chunks: Sequence[Chunk]) -> list[int]:
    """Return indices of non-whitespace characters no chunk covers."""
    covered: set[int] = set()
    for chunk in chunks:
        covered.update(range(chunk.start_char, chunk.end_char))
    return [i for i, char in enumerate(document.text) if not char.isspace() and i not in covered]


def test_offsets_are_exact_for_every_chunk_of_a_multi_section_document():
    document = make_document(filing_parts())

    chunks = Chunker(settings()).chunk(document)

    assert len(chunks) > 1
    assert_offsets_exact(document, chunks)


def test_chunks_cover_every_non_whitespace_character():
    document = make_document(filing_parts())

    chunks = Chunker(settings()).chunk(document)

    assert uncovered_indices(document, chunks) == []


def test_chunk_index_is_sequential_and_ids_are_unique():
    document = make_document(filing_parts())

    chunks = Chunker(settings()).chunk(document)

    assert [chunk.metadata.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert len({chunk.chunk_id for chunk in chunks}) == len(chunks)
    assert len({(chunk.start_char, chunk.end_char) for chunk in chunks}) == len(chunks)


def test_metadata_is_denormalized_from_the_document():
    document = make_document(filing_parts())

    chunks = Chunker(settings()).chunk(document)

    for chunk in chunks:
        assert chunk.document_id == document.document_id
        assert chunk.metadata.document_id == document.document_id
        assert chunk.metadata.source_path == SOURCE_PATH
        assert chunk.metadata.ticker == "ACME"
        assert chunk.metadata.form == "10-K"
        assert chunk.metadata.fiscal_year == 2024
        assert chunk.metadata.is_synthetic is True


def test_section_heading_is_the_heading_of_the_owning_section():
    document = make_document(filing_parts())

    chunks = Chunker(settings()).chunk(document)

    for chunk in chunks:
        owner = next(
            section for section in document.sections if section.start_char <= chunk.start_char < section.end_char
        )
        assert chunk.metadata.section_heading == owner.heading
    assert {chunk.metadata.section_heading for chunk in chunks} == {
        "Item 1. Business",
        "Item 1A. Risk Factors",
        "Item 7. MD&A",
    }


def test_chunks_never_cross_a_section_boundary():
    document = make_document(filing_parts())

    chunks = Chunker(settings()).chunk(document)

    bounds = [(section.start_char, section.end_char) for section in document.sections]
    for chunk in chunks:
        owners = [span for span in bounds if span[0] <= chunk.start_char and chunk.end_char <= span[1]]
        assert len(owners) == 1, f"chunk {chunk.metadata.chunk_index} spans more than one section"


def test_no_chunk_exceeds_max_chars():
    document = make_document(filing_parts())

    chunks = Chunker(settings(max_chars=180)).chunk(document)

    assert chunks
    assert max(len(chunk.text) for chunk in chunks) <= 180


def test_short_section_merges_forward_into_the_next_chunk():
    preamble = "Item 1. Business\n\n"
    document = make_document(
        [
            ("Item 1. Business", preamble),
            ("Item 1A. Risk Factors", f"Item 1A. Risk Factors\n\n{filler(200)}"),
        ]
    )

    chunks = Chunker(settings(max_chars=600, min_chars=120)).chunk(document)

    assert len(chunks) == 1
    merged = chunks[0]
    assert merged.start_char < len(preamble) < merged.end_char
    assert merged.metadata.section_heading == "Item 1. Business"
    assert "Item 1A. Risk Factors" in merged.text
    assert_offsets_exact(document, chunks)


def test_short_trailing_section_merges_backward_when_it_has_no_successor():
    body = f"Item 7. MD&A\n\n{filler(200)}\n\n"
    document = make_document([("Item 7. MD&A", body), ("Signatures", "Signatures\n")])

    chunks = Chunker(settings(max_chars=600, min_chars=120)).chunk(document)

    assert len(chunks) == 1
    assert chunks[0].start_char < len(body) < chunks[0].end_char
    assert chunks[0].text.endswith("Signatures")
    assert_offsets_exact(document, chunks)


def test_a_document_shorter_than_min_chars_still_yields_one_chunk():
    document = make_document([("Item 1. Business", f"Item 1. Business\n\n{filler(20)}")])

    chunks = Chunker(settings(max_chars=600, min_chars=400)).chunk(document)

    assert len(chunks) == 1
    assert chunks[0].text == document.text.strip()


def test_sections_at_or_above_min_chars_are_not_merged():
    first = f"Item 1. Business\n\n{filler(120)}"
    document = make_document(
        [
            ("Item 1. Business", first),
            ("Item 1A. Risk Factors", f"Item 1A. Risk Factors\n\n{filler(120)}"),
        ]
    )

    chunks = Chunker(settings(max_chars=600, min_chars=60)).chunk(document)

    assert len(chunks) == 2
    assert chunks[0].end_char <= len(first)
    assert chunks[1].start_char >= len(first)
    assert [chunk.metadata.section_heading for chunk in chunks] == [
        "Item 1. Business",
        "Item 1A. Risk Factors",
    ]


def test_consecutive_chunks_overlap_by_at_most_the_configured_amount():
    document = make_document([("Item 1. Business", filler(900))])

    chunks = Chunker(settings(max_chars=200, overlap_chars=40, min_chars=50)).chunk(document)

    assert len(chunks) > 3
    for previous, following in pairwise(chunks):
        overlap = previous.end_char - following.start_char
        assert 0 < overlap <= 40


def test_overlapping_text_is_shared_verbatim_between_neighbours():
    document = make_document([("Item 1. Business", filler(900))])

    chunks = Chunker(settings(max_chars=200, overlap_chars=40, min_chars=50)).chunk(document)

    for previous, following in pairwise(chunks):
        shared = document.text[following.start_char : previous.end_char]
        assert previous.text.endswith(shared)
        assert following.text.startswith(shared)


def test_zero_overlap_produces_adjacent_chunks():
    document = make_document([("Item 1. Business", filler(900))])

    chunks = Chunker(settings(max_chars=200, overlap_chars=0, min_chars=50)).chunk(document)

    assert len(chunks) > 3
    for previous, following in pairwise(chunks):
        assert previous.end_char <= following.start_char


def test_breaks_prefer_paragraph_boundaries():
    paragraph = filler(80)
    document = make_document([("Item 1. Business", f"{paragraph}\n\n{filler(300)}")])

    chunks = Chunker(settings(max_chars=200, overlap_chars=10, min_chars=20)).chunk(document)

    assert chunks[0].text == paragraph


def test_breaks_prefer_sentence_boundaries_when_no_paragraph_fits():
    document = make_document([("Item 1. Business", sentences(8))])

    chunks = Chunker(settings(max_chars=200, overlap_chars=10, min_chars=20)).chunk(document)

    assert len(chunks) > 1
    assert chunks[0].text.endswith(".")


def test_breaks_never_split_a_word():
    body = filler(900)
    document = make_document([("Item 1. Business", body)])

    chunks = Chunker(settings(max_chars=200, overlap_chars=40, min_chars=50)).chunk(document)

    for chunk in chunks:
        assert chunk.start_char == 0 or document.text[chunk.start_char - 1].isspace()
        assert chunk.end_char == len(body) or document.text[chunk.end_char].isspace()


def test_a_word_longer_than_max_chars_is_hard_split():
    body = "A" * 300
    document = make_document([("Item 1. Business", body)])

    chunks = Chunker(settings(max_chars=100, overlap_chars=10, min_chars=20)).chunk(document)

    assert len(chunks) > 1
    assert max(len(chunk.text) for chunk in chunks) <= 100
    assert_offsets_exact(document, chunks)
    assert uncovered_indices(document, chunks) == []


def test_empty_document_yields_no_chunks():
    document = make_document([(None, "")])

    assert Chunker(settings()).chunk(document) == []


def test_whitespace_only_document_yields_no_chunks():
    document = make_document([("Item 1. Business", "   \n\n\t  \n")])

    assert Chunker(settings()).chunk(document) == []


def test_document_without_sections_is_chunked_as_a_single_unit():
    text = filler(500)
    metadata = DocumentMetadata(source_path=SOURCE_PATH, is_synthetic=True)
    document = NormalizedDocument(
        document_id=make_document_id(text, metadata.source_path),
        kind=SourceKind.TEXT,
        text=text,
        sections=[],
        metadata=metadata,
    )

    chunks = Chunker(settings(max_chars=200, overlap_chars=30, min_chars=50)).chunk(document)

    assert len(chunks) > 1
    assert all(chunk.metadata.section_heading is None for chunk in chunks)
    assert_offsets_exact(document, chunks)
    assert uncovered_indices(document, chunks) == []


def test_text_outside_any_section_is_still_chunked():
    text = f"{filler(300)}\n\nItem 1. Business\n\n{filler(300)}"
    offset = text.index("Item 1. Business")
    metadata = DocumentMetadata(source_path=SOURCE_PATH, is_synthetic=True)
    document = NormalizedDocument(
        document_id=make_document_id(text, metadata.source_path),
        kind=SourceKind.MARKDOWN,
        text=text,
        sections=[
            Section(
                order=0,
                heading="Item 1. Business",
                text=text[offset:],
                start_char=offset,
                end_char=len(text),
            )
        ],
        metadata=metadata,
    )

    chunks = Chunker(settings(max_chars=200, overlap_chars=30, min_chars=50)).chunk(document)

    assert uncovered_indices(document, chunks) == []
    assert chunks[0].metadata.section_heading is None
    assert chunks[-1].metadata.section_heading == "Item 1. Business"


def test_chunking_is_deterministic_across_instances():
    document = make_document(filing_parts())

    first = Chunker(settings()).chunk(document)
    second = Chunker(settings()).chunk(document)

    assert [chunk.model_dump() for chunk in first] == [chunk.model_dump() for chunk in second]


def test_no_chunk_is_wholly_contained_in_another():
    # Wide whitespace runs plus a near-window overlap are what make two windows trim to nested
    # spans; the narrower one carries nothing the other does not.
    body = f"alpha beta{' ' * 100}gamma delta{' ' * 100}epsilon"
    document = make_document([("Item 1. Business", body)])

    chunks = Chunker(settings(max_chars=20, overlap_chars=15, min_chars=0)).chunk(document)

    spans = [(chunk.start_char, chunk.end_char) for chunk in chunks]
    for outer in spans:
        contained = [inner for inner in spans if inner != outer and outer[0] <= inner[0] and inner[1] <= outer[1]]
        assert contained == []
    assert uncovered_indices(document, chunks) == []


def test_repeated_boilerplate_gets_distinct_chunk_ids():
    boilerplate = f"{filler(150)}\n\n"
    document = make_document([("Item 1. Business", boilerplate * 6)])

    chunks = Chunker(settings(max_chars=180, overlap_chars=0, min_chars=40)).chunk(document)

    assert len(chunks) > 2
    assert len({chunk.text for chunk in chunks}) < len(chunks)
    assert len({chunk.chunk_id for chunk in chunks}) == len(chunks)


def test_max_chars_changes_the_chunking():
    document = make_document(filing_parts())

    small = Chunker(settings(max_chars=150)).chunk(document)
    large = Chunker(settings(max_chars=600)).chunk(document)

    assert len(small) > len(large)
    assert max(len(chunk.text) for chunk in small) <= 150
    assert max(len(chunk.text) for chunk in large) <= 600


def test_min_chars_changes_the_chunking():
    parts = [("Item 1. Business", f"Item 1. Business\n\n{filler(90)}")]
    parts.append(("Item 1A. Risk Factors", f"Item 1A. Risk Factors\n\n{filler(90)}"))
    document = make_document(parts)

    split = Chunker(settings(max_chars=600, min_chars=50)).chunk(document)
    merged = Chunker(settings(max_chars=600, min_chars=400)).chunk(document)

    assert len(split) == 2
    assert len(merged) == 1


def test_overlap_at_or_above_max_chars_is_clamped_and_terminates():
    body = filler(400)
    document = make_document([("Item 1. Business", body)])

    chunks = Chunker(settings(max_chars=50, overlap_chars=500, min_chars=10)).chunk(document)

    assert chunks
    assert len(chunks) <= len(body)
    assert max(len(chunk.text) for chunk in chunks) <= 50
    assert_offsets_exact(document, chunks)
    assert uncovered_indices(document, chunks) == []
    assert len({(chunk.start_char, chunk.end_char) for chunk in chunks}) == len(chunks)


def test_min_chars_at_or_above_max_chars_terminates():
    document = make_document([("Item 1. Business", filler(400))])

    chunks = Chunker(settings(max_chars=60, overlap_chars=10, min_chars=200)).chunk(document)

    assert chunks
    assert max(len(chunk.text) for chunk in chunks) <= 60
    assert uncovered_indices(document, chunks) == []


def test_single_character_window_terminates():
    document = make_document([("Item 1. Business", filler(40))])

    chunks = Chunker(settings(max_chars=1, overlap_chars=5, min_chars=0)).chunk(document)

    assert all(len(chunk.text) == 1 for chunk in chunks)
    assert uncovered_indices(document, chunks) == []


def pathological_documents() -> list[NormalizedDocument]:
    """Return the inputs a chunker is most likely to loop or lose text on."""
    return [
        make_document([(None, "")]),
        make_document([("Item 1. Business", "   \n\n\t  \n")]),
        make_document([("Item 1. Business", "A" * 300)]),
        make_document([("Item 1. Business", filler(300))]),
        make_document([("Item 1. Business", f"alpha beta{' ' * 80}gamma{' ' * 80}delta")]),
        make_document([("Item 1. Business", sentences(4))]),
        make_document(
            [
                ("Item 1. Business", "Item 1. Business\n"),
                ("Item 1A. Risk Factors", f"Item 1A. Risk Factors\n\n{filler(150)}\n\n"),
                ("Item 7. MD&A", f"Item 7. MD&A\n\n{filler(150)}"),
            ]
        ),
    ]


@pytest.mark.parametrize("max_chars", [1, 7, 50, 250])
@pytest.mark.parametrize("overlap_chars", [0, 3, 40, 500])
@pytest.mark.parametrize("min_chars", [0, 20, 400])
def test_invariants_hold_for_every_settings_combination(max_chars: int, overlap_chars: int, min_chars: int):
    chunker = Chunker(settings(max_chars=max_chars, overlap_chars=overlap_chars, min_chars=min_chars))

    for document in pathological_documents():
        chunks = chunker.chunk(document)

        assert_offsets_exact(document, chunks)
        assert uncovered_indices(document, chunks) == []
        assert all(len(chunk.text) <= max_chars for chunk in chunks)
        assert [chunk.metadata.chunk_index for chunk in chunks] == list(range(len(chunks)))
        assert len({chunk.chunk_id for chunk in chunks}) == len(chunks)
        starts = [chunk.start_char for chunk in chunks]
        ends = [chunk.end_char for chunk in chunks]
        assert starts == sorted(starts)
        assert ends == sorted(ends)
        assert len(set(zip(starts, ends, strict=True))) == len(chunks)


def test_default_settings_come_from_configuration():
    document = make_document(filing_parts())

    chunks = Chunker().chunk(document)

    assert chunks
    assert max(len(chunk.text) for chunk in chunks) <= ChunkingSettings().max_chars
    assert uncovered_indices(document, chunks) == []
