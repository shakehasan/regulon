"""Load source files and normalize them into :class:`~regulon.ingestion.models.NormalizedDocument`.

This is the first stage of ingestion: it turns a ``.txt``, ``.md``, ``.html``, or ``.pdf`` file
into clean plain text plus a :class:`~regulon.ingestion.models.Section` map that later stages
(redaction, chunking, retrieval) index with character offsets. Three properties the rest of the
pipeline relies on:

* **Determinism.** The same bytes at the same path always produce the same text, sections, and
  ``document_id``: no clock, no randomness, and sorted rather than filesystem file order.
* **Offset fidelity.** For every section, ``document.text[s.start_char:s.end_char] == s.text``,
  and the sections are ordered, non-overlapping, and cover the whole text.
* **Offline.** HTML is parsed with :mod:`html.parser` and PDFs with ``pypdf``; no network.

Normalization is conservative - LF line endings, no trailing whitespace, at most two blank lines
- so citation quotes still match the source. HTML is the exception, because its whitespace is
insignificant: outside ``<pre>``, whitespace runs collapse to one space and blocks break lines.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Final

import yaml
from pydantic import ValidationError
from pypdf import PdfReader

from regulon.ingestion.errors import DocumentParseError, UnsupportedSourceError
from regulon.ingestion.models import (
    DocumentMetadata,
    NormalizedDocument,
    Section,
    SourceKind,
    make_document_id,
)

_EXTENSION_KINDS: Final[Mapping[str, SourceKind]] = {
    ".txt": SourceKind.TEXT,
    ".md": SourceKind.MARKDOWN,
    ".markdown": SourceKind.MARKDOWN,
    ".html": SourceKind.HTML,
    ".htm": SourceKind.HTML,
    ".pdf": SourceKind.PDF,
}
_FRONT_MATTER_FIELDS: Final[Mapping[str, str]] = {
    "title": "title",
    "ticker": "ticker",
    "form": "form",
    "fiscal_year": "fiscal_year",
    "synthetic": "is_synthetic",
    "is_synthetic": "is_synthetic",
}

# The constants below are not behaviour tunables (those live in config/regulon.yaml): they define
# the *grammar* this parser recognizes, so changing one changes the section boundaries and ids of
# every document already ingested. They are pinned next to the code that uses them, in the same
# spirit as the id width in models.py.
_BLANK_LINE_RUN: Final = re.compile(r"\n{4,}")
_COLLAPSED_BREAK: Final = "\n\n\n"  # three newlines == two blank lines
_ATX_HEADING: Final = re.compile(r"^(#{1,6})\s+(\S.*)$")
_CODE_FENCE: Final = re.compile(r"^(?:```|~~~)")
_FRONT_MATTER: Final = re.compile(r"\A---[ \t]*\n(.*?)\n(?:---|\.\.\.)[ \t]*(?:\n|\Z)", re.DOTALL)
_WHITESPACE_RUN: Final = re.compile(r"\s+")
_MAX_PLAIN_HEADING_CHARS: Final = 80  # longest stand-alone line still read as a plain-text heading
_SENTENCE_ENDINGS: Final = (".", ",", ";")

_HEADING_TAGS: Final[frozenset[str]] = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_SKIPPED_TAGS: Final[frozenset[str]] = frozenset({"script", "style", "noscript"})
_BLOCK_TAGS: Final[frozenset[str]] = frozenset({
    "address", "article", "aside", "blockquote", "br", "caption", "dd", "div", "dl", "dt", "figure", "footer",
    "form", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table", "tbody", "td", "tfoot",
    "th", "thead", "tr", "ul",
})  # fmt: skip


def detect_kind(path: Path) -> SourceKind:
    """Return the source kind a path's file extension implies, ignoring case.

    Raises:
        UnsupportedSourceError: If the extension has no parser.
    """
    kind = _EXTENSION_KINDS.get(path.suffix.lower())
    if kind is None:
        supported = ", ".join(sorted(_EXTENSION_KINDS))
        raise UnsupportedSourceError(
            f"{path.as_posix()}: unsupported extension {path.suffix or '(none)'!r}; supported: {supported}"
        )
    return kind


def iter_source_files(root: Path) -> list[Path]:
    """Return every loadable file under a directory, sorted by POSIX path.

    Unsupported extensions and dot-prefixed (hidden) files and directories are skipped, so a
    corpus directory can hold notes and metadata without breaking a run, and the sorted result
    makes an ingestion run reproducible across machines. A ``root`` that is itself a supported
    file yields ``[root]``; a ``root`` that does not exist yields an empty list.
    """
    if root.is_file():
        return [root] if root.suffix.lower() in _EXTENSION_KINDS else []
    if not root.is_dir():
        return []
    found = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in _EXTENSION_KINDS
        and not any(part.startswith(".") for part in path.relative_to(root).parts)
    ]
    return sorted(found, key=lambda path: path.as_posix())


def load_document(path: Path, *, metadata: DocumentMetadata | None = None) -> NormalizedDocument:
    """Read one source file and return it as a normalized document.

    Metadata is assembled from three sources, in increasing precedence: the file path (which
    always supplies a fallback ``source_path``), attributes the source states about itself
    (markdown YAML front matter, the HTML ``<title>``), and the ``metadata`` argument. Only the
    fields the caller actually set on ``metadata`` override the source; unset fields fall back.
    The resulting ``document_id`` is a deterministic hash of the normalized text and that
    ``source_path``.

    Args:
        path: Path to a ``.txt``, ``.md``/``.markdown``, ``.html``/``.htm``, or ``.pdf`` file.
        metadata: Caller-supplied provenance that wins over anything found in the document.

    Raises:
        UnsupportedSourceError: If the extension has no parser.
        DocumentParseError: If the file cannot be read, is not valid UTF-8, carries malformed
            front matter, or (for PDFs) cannot be parsed or holds no extractable text.
    """
    kind = detect_kind(path)
    text, sections, discovered = _parse_source(path, kind)
    resolved = _resolve_metadata(path, discovered, metadata)
    document_id = make_document_id(text, resolved.source_path)
    return NormalizedDocument(document_id=document_id, kind=kind, text=text, sections=sections, metadata=resolved)


def _parse_source(path: Path, kind: SourceKind) -> tuple[str, list[Section], dict[str, Any]]:
    """Return the normalized text, sections, and any metadata the source itself states."""
    if kind is SourceKind.MARKDOWN:
        body, discovered = _split_front_matter(_normalize(_read_text(path)), path)
        text = _normalize(body)
        return text, _build_sections(text, _markdown_headings(text)), discovered
    if kind is SourceKind.HTML:
        extractor = _HtmlTextExtractor()
        extractor.feed(_read_text(path))
        extractor.close()
        text = _normalize(extractor.text())
        discovered = {"title": extractor.title} if extractor.title else {}
        return text, _build_sections(text, _locate_headings(text, extractor.headings)), discovered
    raw = _read_pdf(path) if kind is SourceKind.PDF else _read_text(path)
    text = _normalize(raw)
    return text, _build_sections(text, _plain_headings(text)), {}


def _read_text(path: Path) -> str:
    """Read a text-like file as UTF-8, tolerating a byte-order mark."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise DocumentParseError(f"{path.as_posix()}: cannot read source file: {exc}") from exc
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DocumentParseError(f"{path.as_posix()}: source file is not valid UTF-8") from exc


def _read_pdf(path: Path) -> str:
    """Extract page text from a PDF, joining pages with a blank line."""
    try:
        reader = PdfReader(path)
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:  # pypdf raises a wide family of errors on damaged or missing files.
        raise DocumentParseError(f"{path.as_posix()}: cannot parse PDF: {exc}") from exc
    text = "\n\n".join(page for page in pages if page.strip())
    if not text.strip():
        raise DocumentParseError(f"{path.as_posix()}: PDF holds no extractable text (a scanned image needs OCR)")
    return text


def _normalize(raw: str) -> str:
    """Return text with unified line endings, no trailing spaces, and at most two blank lines."""
    unified = raw.replace("\r\n", "\n").replace("\r", "\n")
    trimmed = "\n".join(line.rstrip() for line in unified.split("\n"))
    return _BLANK_LINE_RUN.sub(_COLLAPSED_BREAK, trimmed).strip("\n")


def _iter_lines(text: str) -> Iterator[tuple[int, str]]:
    """Yield ``(start_offset, line)`` for each line, with offsets into ``text``."""
    offset = 0
    for line in text.split("\n"):
        yield offset, line
        offset += len(line) + 1


def _build_sections(text: str, headings: Sequence[tuple[int, str]]) -> list[Section]:
    """Turn heading start offsets into contiguous, non-overlapping sections covering ``text``."""
    boundaries: list[tuple[int, str | None]] = list(headings)
    if not boundaries or boundaries[0][0] > 0:
        boundaries.insert(0, (0, None))
    sections: list[Section] = []
    for order, (start, heading) in enumerate(boundaries):
        end = boundaries[order + 1][0] if order + 1 < len(boundaries) else len(text)
        sections.append(Section(order=order, heading=heading, text=text[start:end], start_char=start, end_char=end))
    return sections


def _markdown_headings(text: str) -> list[tuple[int, str]]:
    """Locate ATX headings (``#`` .. ``######``), ignoring anything inside a fenced code block."""
    headings: list[tuple[int, str]] = []
    fenced = False
    for offset, line in _iter_lines(text):
        if _CODE_FENCE.match(line.lstrip()):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = _ATX_HEADING.match(line)
        if match is not None:
            headings.append((offset, match.group(2).strip().rstrip("#").strip()))
    return headings


def _plain_headings(text: str) -> list[tuple[int, str]]:
    """Locate headings in unstructured text: single-line blocks that read like a title.

    A heading is a line that stands alone between blank lines (or document edges), contains a
    letter, and is either all upper case or short enough not to read like a sentence.
    """
    lines = list(_iter_lines(text))
    headings: list[tuple[int, str]] = []
    for index, (offset, line) in enumerate(lines):
        stripped = line.strip()
        alone = (index == 0 or not lines[index - 1][1].strip()) and (
            index == len(lines) - 1 or not lines[index + 1][1].strip()
        )
        if not stripped or not alone or not any(character.isalpha() for character in stripped):
            continue
        short = len(stripped) <= _MAX_PLAIN_HEADING_CHARS and not stripped.endswith(_SENTENCE_ENDINGS)
        if stripped.isupper() or short:
            headings.append((offset, stripped))
    return headings


def _locate_headings(text: str, headings: Sequence[str]) -> list[tuple[int, str]]:
    """Match extracted heading strings back to their own line in the normalized text, in order."""
    located: list[tuple[int, str]] = []
    pending = 0
    for offset, line in _iter_lines(text):
        if pending >= len(headings):
            break
        if line.strip() == headings[pending]:
            located.append((offset, headings[pending]))
            pending += 1
    return located


class _HtmlTextExtractor(HTMLParser):
    """Collect plain text, heading lines, and the ``<title>`` from an HTML source.

    Script, style, and noscript content is dropped; character entities are decoded by the base
    parser; block elements become line breaks so that headings land on a line of their own.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.headings: list[str] = []
        self.title: str | None = None
        self._parts: list[str] = []
        self._heading_parts: list[str] = []
        self._title_parts: list[str] = []
        self._skip_depth = self._pre_depth = self._heading_depth = 0
        self._in_title = False
        self._at_line_start = True

    def text(self) -> str:
        """Return the extracted plain text."""
        return "".join(self._parts)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Open a tag: track skipped regions, headings, and block-level line breaks."""
        if tag in _SKIPPED_TAGS:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
            return
        if tag == "pre":
            self._pre_depth += 1
        if tag in _HEADING_TAGS:
            self._break()
            self._heading_depth += 1
            self._heading_parts = []
        elif tag in _BLOCK_TAGS and not self._heading_depth:
            self._break()

    def handle_endtag(self, tag: str) -> None:
        """Close a tag: finish a heading, leave a skipped region, or end a block."""
        if tag in _SKIPPED_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag == "title":
            self._in_title = False
            self.title = _WHITESPACE_RUN.sub(" ", "".join(self._title_parts)).strip() or None
            return
        if tag == "pre":
            self._pre_depth = max(0, self._pre_depth - 1)
        if tag in _HEADING_TAGS:
            self._heading_depth = max(0, self._heading_depth - 1)
            heading = _WHITESPACE_RUN.sub(" ", "".join(self._heading_parts)).strip()
            if heading:
                self.headings.append(heading)
            self._break()
        elif tag in _BLOCK_TAGS and not self._heading_depth:
            self._break()

    def handle_data(self, data: str) -> None:
        """Buffer text, collapsing insignificant whitespace outside ``<pre>``."""
        if self._skip_depth:
            return
        data = data.replace("\xa0", " ")
        if self._in_title:
            self._title_parts.append(data)
            return
        text = data if self._pre_depth else _WHITESPACE_RUN.sub(" ", data)
        if self._heading_depth:
            self._heading_parts.append(text)
        self._emit(text)

    def _emit(self, text: str) -> None:
        # Drops whitespace that would indent the start of a line.
        if self._at_line_start and not self._pre_depth:
            text = text.lstrip()
        if not text:
            return
        self._parts.append(text)
        self._at_line_start = text.endswith("\n")

    def _break(self) -> None:
        # A no-op when the buffer already sits at the start of a line.
        if self._parts and not self._at_line_start:
            self._parts.append("\n")
        self._at_line_start = True


def _split_front_matter(text: str, path: Path) -> tuple[str, dict[str, Any]]:
    """Strip a leading YAML front-matter block and return the body plus its recognized fields.

    ``title``, ``ticker``, ``form``, ``fiscal_year``, and ``synthetic``/``is_synthetic`` become
    metadata fields; other keys are kept as strings in ``extra``. A block that parses to
    anything but a mapping is not front matter (a markdown file may open with a thematic
    break) and stays in the body.

    Raises:
        DocumentParseError: If the block looks like front matter but is not valid YAML.
    """
    match = _FRONT_MATTER.match(text)
    if match is None:
        return text, {}
    try:
        parsed = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise DocumentParseError(f"{path.as_posix()}: front matter is not valid YAML: {exc}") from exc
    if parsed is None:
        return text[match.end() :], {}
    if not isinstance(parsed, dict):
        return text, {}
    fields: dict[str, Any] = {}
    extra: dict[str, str] = {}
    for key in sorted(parsed, key=str):
        value = parsed[key]
        field = _FRONT_MATTER_FIELDS.get(str(key).lower())
        if field is None:
            extra[str(key)] = "" if value is None else str(value)
        elif value is not None:
            fields[field] = value
    if extra:
        fields["extra"] = extra
    return text[match.end() :], fields


def _resolve_metadata(path: Path, discovered: dict[str, Any], override: DocumentMetadata | None) -> DocumentMetadata:
    """Combine path, in-document, and caller-supplied metadata into one validated model.

    Raises:
        DocumentParseError: If the combined values do not satisfy the metadata contract.
    """
    values: dict[str, Any] = {"source_path": path.as_posix(), **discovered}
    if override is not None:
        values.update({name: getattr(override, name) for name in sorted(override.model_fields_set)})
    try:
        return DocumentMetadata.model_validate(values)
    except ValidationError as exc:
        raise DocumentParseError(f"{path.as_posix()}: invalid document metadata: {exc}") from exc
