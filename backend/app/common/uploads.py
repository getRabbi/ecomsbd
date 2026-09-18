"""Upload validation.

Master spec section 98 and the Phase F brief's section 29. The rule that shapes
this module:

    **Do not trust the MIME type from the client.**

A browser sends whatever it likes in ``Content-Type``, and a file named
``.csv`` can hold anything. So a file is accepted on the strength of what is
*in* it, in this order:

1.  size, before anything is read into memory;
2.  extension, as a fast, honest first filter;
3.  **content sniffing** — a ZIP magic number is an XLSX regardless of what the
    name and the header claim;
4.  decodability as text, with the encodings Bangladeshi sellers' tools
    actually produce;
5.  row count.

Filenames are sanitised rather than trusted: the original is kept for the
seller to recognise, and a separate, safe name is what ever reaches a storage
key or a log line.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from app.core.errors import ValidationError

__all__ = [
    "DEFAULT_MAX_UPLOAD_BYTES",
    "DetectedFormat",
    "UploadCheck",
    "safe_filename",
    "validate_upload",
]

#: 8 MiB. A statement or a product list from a real shop is far below this;
#: anything above is a mistake or an attempt to exhaust memory.
DEFAULT_MAX_UPLOAD_BYTES = 8 * 1024 * 1024

#: Compression ratio above which a payload is treated as a decompression bomb.
#: Only relevant once an archive format is ever accepted; today they are all
#: refused outright, and this constant documents the threshold for when they
#: are not.
MAX_DECOMPRESSION_RATIO = 100

_ALLOWED_EXTENSIONS = {".csv", ".txt", ".tsv"}
_ALLOWED_SPREADSHEET_EXTENSIONS = {".xlsx", ".xlsm"}
_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class DetectedFormat(StrEnum):
    CSV = "CSV"
    #: Recognised so it can be refused with a sentence a seller understands
    #: rather than mis-parsed into nonsense.
    XLSX = "XLSX"
    XLS = "XLS"
    PDF = "PDF"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class UploadCheck:
    """The result of validating an upload."""

    detected: DetectedFormat
    safe_name: str
    original_name: str
    byte_size: int
    encoding: str
    text: str


def safe_filename(name: str | None, *, fallback: str = "upload.csv") -> str:
    """A filename safe to put in a storage key, a header or a log line.

    Path separators, ``..``, control characters and Unicode direction marks all
    go. The result is never empty and never longer than 120 characters.
    """
    if not name:
        return fallback
    # NFKC first: a full-width solidus normalises to "/" and must be stripped
    # by the same rule as a plain one.
    cleaned = unicodedata.normalize("NFKC", name)
    cleaned = cleaned.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _FILENAME_SAFE.sub("_", cleaned).strip("._")
    if not cleaned:
        return fallback
    return cleaned[:120]


def _sniff(content: bytes) -> DetectedFormat:
    """Identify a file from its first bytes, ignoring name and MIME."""
    if content[:4] == b"PK\x03\x04":
        # Any ZIP container. XLSX, ODS and DOCX all land here.
        return DetectedFormat.XLSX
    if content[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return DetectedFormat.XLS
    if content[:5] == b"%PDF-":
        return DetectedFormat.PDF
    return DetectedFormat.UNKNOWN


#: Encodings tried in order. UTF-8 first; ``utf-8-sig`` because Excel writes a
#: BOM; then the two legacy encodings Bangladeshi spreadsheets still emit.
_ENCODINGS = ("utf-8", "utf-8-sig", "cp1252", "latin-1")


def validate_upload(
    content: bytes,
    *,
    filename: str | None,
    content_type: str | None = None,
    max_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
    max_rows: int | None = None,
    allow_spreadsheet: bool = False,
) -> UploadCheck:
    """Validate an uploaded file, or raise with a seller-readable reason.

    ``content_type`` is accepted and deliberately **not** used as evidence. It
    is recorded on the import batch so support can see what the client claimed,
    which is occasionally useful and never authoritative.

    ``allow_spreadsheet`` lets a caller accept XLSX. It is opt-in rather than
    the default because accepting a spreadsheet is only safe when the caller
    also reads it safely — streamed, with formulas off, and with every cell
    turned back into the text a CSV would have carried so the same money and
    phone parsers run. :mod:`app.imports.spreadsheet` is that reader. A caller
    that has not opted in still gets the old refusal, which is the right
    answer for one that would feed the bytes to a CSV reader.
    """
    size = len(content)
    if size == 0:
        raise ValidationError("That file is empty")
    if size > max_bytes:
        raise ValidationError(
            f"That file is {size // 1024} KB; the limit is {max_bytes // 1024} KB. "
            "Please split it into smaller files.",
            details={"byte_size": str(size), "max_bytes": str(max_bytes)},
        )

    safe = safe_filename(filename)
    extension = f".{safe.rsplit('.', 1)[-1].lower()}" if "." in safe else ""

    detected = _sniff(content)
    if detected is DetectedFormat.XLSX:
        if not allow_spreadsheet:
            raise ValidationError(
                "That looks like an Excel file. Save it as CSV and upload again — "
                "reading it directly would risk misreading your amounts.",
                details={"detected": str(detected)},
            )
        if extension and extension not in _ALLOWED_SPREADSHEET_EXTENSIONS:
            # A ZIP container that is not named like a workbook. ODS and DOCX
            # sniff identically, and handing either to a spreadsheet reader
            # produces nonsense rather than an error a seller can act on.
            raise ValidationError(
                "That looks like a compressed file rather than an Excel workbook. "
                "Please upload a .xlsx file or a CSV.",
                details={"extension": extension},
            )
        # The bytes are returned unread. Parsing belongs to the caller that
        # opted in, because only it knows how to do it safely.
        return UploadCheck(
            detected=DetectedFormat.XLSX,
            safe_name=safe,
            original_name=filename or safe,
            byte_size=size,
            encoding="binary",
            text="",
        )
    if detected in (DetectedFormat.XLS, DetectedFormat.PDF):
        raise ValidationError(
            f"That looks like a {detected} file. Only CSV is supported.",
            details={"detected": str(detected)},
        )

    if extension and extension not in _ALLOWED_EXTENSIONS:
        raise ValidationError(
            f"'{extension}' files are not supported. Please upload a CSV.",
            details={"extension": extension},
        )

    text, encoding = _decode(content)
    # A NUL byte in "text" means it is not text. Refusing beats importing a
    # binary as a one-row CSV full of replacement characters.
    if "\x00" in text:
        raise ValidationError("That file is not readable as text. Please upload a CSV.")

    if max_rows is not None:
        row_count = text.count("\n") + (0 if text.endswith("\n") else 1)
        if row_count > max_rows:
            raise ValidationError(
                f"This file has about {row_count} rows; the limit is {max_rows}. Please split it.",
                details={"rows": str(row_count), "max_rows": str(max_rows)},
            )

    return UploadCheck(
        detected=DetectedFormat.CSV,
        safe_name=safe,
        original_name=filename or safe,
        byte_size=size,
        encoding=encoding,
        text=text,
    )


def _decode(content: bytes) -> tuple[str, str]:
    for encoding in _ENCODINGS:
        try:
            return content.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValidationError(
        "That file's text encoding could not be read. Save it as CSV (UTF-8) and upload again."
    )
