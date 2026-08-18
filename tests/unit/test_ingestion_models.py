"""Tests for the ingestion contract: immutability, id determinism, validation, serialization.

Sample text is synthetic and carries no personal data; redaction samples use placeholder
strings only, never a realistic email, phone, or identifier literal.
"""

import pytest
from pydantic import ValidationError

from regulon.core.errors import RegulonError
from regulon.ingestion import (
    REDACTION_KINDS,
    Chunk,
    ChunkMetadata,
    DocumentIngestSummary,
    DocumentMetadata,
    DocumentParseError,
    EdgarError,
    IngestionError,
    IngestReport,
    NormalizedDocument,
    RedactionEvent,
    RedactionResult,
    Section,
    SourceKind,
    StoreError,
    UnsupportedSourceError,
    make_chunk_id,
    make_document_id,
)

DOC_TEXT = "Item 1. Business\nThe issuer operates two segments.\nItem 1A. Risk Factors\nMarkets move."


def build_metadata(**overrides) -> DocumentMetadata:
    fields = {
        "source_path": "data/synthetic/SYNTHETIC-acme-10k.md",
        "title": "SYNTHETIC Annual Report",
        "ticker": "ACME",
        "form": "10-K",
        "fiscal_year": 2024,
        "is_synthetic": True,
    }
    fields.update(overrides)
    return DocumentMetadata(**fields)


def build_document() -> NormalizedDocument:
    metadata = build_metadata()
    sections = [
        Section(order=0, heading="Item 1. Business", text=DOC_TEXT[:52], start_char=0, end_char=52),
        Section(order=1, heading="Item 1A. Risk Factors", text=DOC_TEXT[52:], start_char=52, end_char=len(DOC_TEXT)),
    ]
    return NormalizedDocument(
        document_id=make_document_id(DOC_TEXT, metadata.source_path),
        kind=SourceKind.MARKDOWN,
        text=DOC_TEXT,
        sections=sections,
        metadata=metadata,
    )


def build_chunk(index: int = 0) -> Chunk:
    document = build_document()
    text = document.text[:40]
    return Chunk(
        chunk_id=make_chunk_id(document.document_id, index, text),
        document_id=document.document_id,
        text=text,
        start_char=0,
        end_char=40,
        metadata=ChunkMetadata(
            document_id=document.document_id,
            source_path=document.metadata.source_path,
            chunk_index=index,
            section_heading="Item 1. Business",
            ticker="ACME",
            form="10-K",
            fiscal_year=2024,
            is_synthetic=True,
        ),
    )


# --- SourceKind -------------------------------------------------------------------------


def test_source_kind_values_are_stable_lowercase_strings():
    assert [k.value for k in SourceKind] == ["text", "markdown", "html", "pdf"]
    # StrEnum members compare and serialize as plain strings.
    assert SourceKind.HTML == "html"
    assert f"{SourceKind.PDF}" == "pdf"


def test_source_kind_rejects_unknown_format():
    with pytest.raises(ValueError):
        SourceKind("docx")


# --- Immutability -----------------------------------------------------------------------


def test_every_contract_model_is_frozen():
    document = build_document()
    chunk = build_chunk()
    frozen_cases = [
        (build_metadata(), "title", "other"),
        (document.sections[0], "order", 5),
        (document, "text", "tampered"),
        (chunk.metadata, "chunk_index", 9),
        (chunk, "text", "tampered"),
        (RedactionEvent(kind="email", start_char=0, end_char=4, replacement="[REDACTED:email]"), "kind", "phone"),
        (RedactionResult(text="clean"), "text", "tampered"),
        (DocumentIngestSummary(document_id="doc_1", source_path="a.md", chunks=1, redactions=0), "chunks", 7),
        (IngestReport(documents_ingested=0, chunks_created=0, redactions_applied=0), "chunks_created", 7),
    ]
    for model, field, value in frozen_cases:
        with pytest.raises(ValidationError) as exc:
            setattr(model, field, value)
        assert exc.value.errors()[0]["type"] == "frozen_instance"


def test_frozen_scalar_models_are_hashable():
    event = RedactionEvent(kind="ssn", start_char=3, end_char=14, replacement="[REDACTED:ssn]")
    same = RedactionEvent(kind="ssn", start_char=3, end_char=14, replacement="[REDACTED:ssn]")
    assert hash(event) == hash(same)
    assert len({event, same}) == 1


# --- Defaults and shape -----------------------------------------------------------------


def test_document_metadata_defaults_are_conservative():
    metadata = DocumentMetadata(source_path="data/raw/filing.txt")
    assert metadata.title is None
    assert metadata.ticker is None
    assert metadata.form is None
    assert metadata.fiscal_year is None
    assert metadata.is_synthetic is False
    assert metadata.extra == {}


def test_document_metadata_extra_is_not_shared_between_instances():
    first = DocumentMetadata(source_path="a.txt")
    second = DocumentMetadata(source_path="b.txt", extra={"cik": "0000000001"})
    assert first.extra == {}
    assert second.extra == {"cik": "0000000001"}


def test_chunk_metadata_optional_fields_default_to_none():
    metadata = ChunkMetadata(document_id="doc_1", source_path="a.md", chunk_index=0)
    assert (metadata.section_heading, metadata.ticker, metadata.form, metadata.fiscal_year) == (None,) * 4
    assert metadata.is_synthetic is False


def test_report_collections_default_to_empty_tuples():
    report = IngestReport(documents_ingested=0, chunks_created=0, redactions_applied=0)
    assert report.documents == ()
    assert report.skipped == ()


def test_redaction_result_coerces_event_list_to_tuple():
    result = RedactionResult(
        text="Contact: [REDACTED:email]",
        events=[RedactionEvent(kind="email", start_char=9, end_char=25, replacement="[REDACTED:email]")],
    )
    assert isinstance(result.events, tuple)
    assert result.events[0].kind in REDACTION_KINDS


def test_redaction_kinds_constant_covers_the_documented_categories():
    assert sorted(REDACTION_KINDS) == ["email", "phone", "ssn"]


def test_section_offsets_index_into_document_text():
    document = build_document()
    for section in document.sections:
        assert document.text[section.start_char : section.end_char] == section.text


# --- Validation failures ----------------------------------------------------------------


def test_negative_offsets_are_rejected():
    with pytest.raises(ValidationError) as exc:
        Section(order=0, heading=None, text="x", start_char=-1, end_char=4)
    assert exc.value.errors()[0]["type"] == "greater_than_equal"


def test_reversed_span_is_rejected_on_every_span_model():
    with pytest.raises(ValidationError, match="must not precede"):
        Section(order=0, heading=None, text="x", start_char=10, end_char=4)
    with pytest.raises(ValidationError, match="must not precede"):
        Chunk(
            chunk_id="chk_1",
            document_id="doc_1",
            text="x",
            start_char=10,
            end_char=4,
            metadata=ChunkMetadata(document_id="doc_1", source_path="a.md", chunk_index=0),
        )
    with pytest.raises(ValidationError, match="must not precede"):
        RedactionEvent(kind="phone", start_char=10, end_char=4, replacement="[REDACTED:phone]")


def test_empty_span_is_allowed():
    section = Section(order=0, heading=None, text="", start_char=7, end_char=7)
    assert section.start_char == section.end_char


def test_negative_counters_and_indexes_are_rejected():
    with pytest.raises(ValidationError):
        ChunkMetadata(document_id="doc_1", source_path="a.md", chunk_index=-1)
    with pytest.raises(ValidationError):
        Section(order=-1, heading=None, text="x", start_char=0, end_char=1)
    with pytest.raises(ValidationError):
        DocumentIngestSummary(document_id="doc_1", source_path="a.md", chunks=-1, redactions=0)
    with pytest.raises(ValidationError):
        IngestReport(documents_ingested=0, chunks_created=0, redactions_applied=-1)


def test_identifier_and_path_fields_reject_empty_strings():
    with pytest.raises(ValidationError):
        DocumentMetadata(source_path="")
    with pytest.raises(ValidationError):
        ChunkMetadata(document_id="", source_path="a.md", chunk_index=0)


def test_missing_required_field_is_rejected():
    with pytest.raises(ValidationError) as exc:
        DocumentMetadata()
    assert exc.value.errors()[0]["type"] == "missing"


def test_unknown_fields_are_rejected_rather_than_silently_dropped():
    with pytest.raises(ValidationError) as exc:
        DocumentMetadata(source_path="a.md", tickr="ACME")
    assert exc.value.errors()[0]["type"] == "extra_forbidden"


def test_non_numeric_fiscal_year_is_rejected_but_numeric_string_is_coerced():
    with pytest.raises(ValidationError):
        DocumentMetadata(source_path="a.md", fiscal_year="last year")
    assert DocumentMetadata(source_path="a.md", fiscal_year="2024").fiscal_year == 2024


def test_unknown_source_kind_is_rejected_by_the_document_model():
    with pytest.raises(ValidationError):
        NormalizedDocument(
            document_id="doc_1",
            kind="docx",
            text="x",
            sections=[],
            metadata=build_metadata(),
        )


# --- Deterministic ids ------------------------------------------------------------------


def test_document_id_shape():
    doc_id = make_document_id(DOC_TEXT, "a.md")
    prefix, _, digest = doc_id.partition("_")
    assert prefix == "doc"
    assert len(digest) == 16
    assert set(digest) <= set("0123456789abcdef")


def test_chunk_id_shape():
    chunk_id = make_chunk_id("doc_abc", 0, "hello")
    prefix, _, digest = chunk_id.partition("_")
    assert prefix == "chk"
    assert len(digest) == 16
    assert set(digest) <= set("0123456789abcdef")


def test_document_id_is_deterministic():
    assert make_document_id(DOC_TEXT, "a.md") == make_document_id(DOC_TEXT, "a.md")


def test_document_id_changes_with_text_or_path():
    base = make_document_id(DOC_TEXT, "a.md")
    assert make_document_id(DOC_TEXT + " ", "a.md") != base
    assert make_document_id(DOC_TEXT, "b.md") != base


def test_document_id_is_not_confused_by_field_boundaries():
    # A naive concatenation of text and path would collide on these two inputs.
    assert make_document_id("ab", "c.md") != make_document_id("a", "bc.md")


def test_chunk_id_is_deterministic():
    assert make_chunk_id("doc_abc", 3, "hello") == make_chunk_id("doc_abc", 3, "hello")


def test_chunk_id_changes_with_document_index_or_text():
    base = make_chunk_id("doc_abc", 3, "hello")
    assert make_chunk_id("doc_xyz", 3, "hello") != base
    assert make_chunk_id("doc_abc", 4, "hello") != base
    assert make_chunk_id("doc_abc", 3, "hello!") != base


def test_repeated_text_within_a_document_still_gets_distinct_chunk_ids():
    boilerplate = "See the accompanying notes."
    ids = {make_chunk_id("doc_abc", index, boilerplate) for index in range(5)}
    assert len(ids) == 5


def test_document_and_chunk_id_namespaces_do_not_collide():
    doc_id = make_document_id("hello", "a.md")
    chunk_id = make_chunk_id("a.md", 0, "hello")
    assert doc_id[4:] != chunk_id[4:]


def test_ids_are_stable_against_regressions():
    # Pinned digests: a change here means every previously stored id would change.
    assert make_document_id("hello", "a.md") == "doc_749ed25508fd7771"
    assert make_chunk_id("doc_abc", 0, "hello") == "chk_ccd033e2e880c896"


# --- Serialization round-trips ----------------------------------------------------------


def test_normalized_document_round_trips_through_json():
    document = build_document()
    restored = NormalizedDocument.model_validate_json(document.model_dump_json())
    assert restored == document
    assert restored.kind is SourceKind.MARKDOWN


def test_chunk_round_trips_through_python_dict():
    chunk = build_chunk(index=2)
    restored = Chunk.model_validate(chunk.model_dump())
    assert restored == chunk
    assert restored.metadata == chunk.metadata


def test_ingest_report_round_trips_and_keeps_tuple_types():
    report = IngestReport(
        documents_ingested=1,
        chunks_created=2,
        redactions_applied=1,
        documents=(DocumentIngestSummary(document_id="doc_1", source_path="a.md", chunks=2, redactions=1),),
        skipped=("b.bin",),
    )
    restored = IngestReport.model_validate_json(report.model_dump_json())
    assert restored == report
    assert isinstance(restored.documents, tuple)
    assert isinstance(restored.skipped, tuple)


def test_redaction_result_round_trips():
    result = RedactionResult(
        text="Reach us at [REDACTED:email].",
        events=(RedactionEvent(kind="email", start_char=12, end_char=28, replacement="[REDACTED:email]"),),
    )
    assert RedactionResult.model_validate_json(result.model_dump_json()) == result


def test_json_dump_uses_the_enum_string_value():
    assert '"kind":"markdown"' in build_document().model_dump_json()


# --- Errors -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error_type",
    [UnsupportedSourceError, DocumentParseError, EdgarError, StoreError],
)
def test_ingestion_errors_share_one_catchable_base(error_type):
    assert issubclass(error_type, IngestionError)
    with pytest.raises(IngestionError):
        raise error_type("boom")


def test_ingestion_error_derives_from_regulon_error():
    assert issubclass(IngestionError, RegulonError)
    with pytest.raises(RegulonError):
        raise StoreError("cannot open store")
