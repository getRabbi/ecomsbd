"""CSV writing that a spreadsheet cannot be tricked into executing.

Master spec section 99 and the Phase F brief's section 28.

A CSV cell beginning ``=``, ``+``, ``-``, ``@`` — or a tab or carriage return,
which Excel strips before looking at the first character — is interpreted as a
formula when the file is opened. A seller's own export is full of text they did
not write: customer names, addresses, notes, a courier's deduction label. Any of
those can carry ``=HYPERLINK("http://…"&A1,"Click")`` and turn an innocent
download into data exfiltration on the seller's own machine.

The defence used here is the **prefix an apostrophe** strategy:

*   a dangerous leading character is escaped by prefixing ``'``;
*   quoting is left to :mod:`csv`, which handles commas and embedded quotes;
*   embedded carriage returns and newlines are normalised so a cell cannot
    smuggle a second row past the escape.

Why an apostrophe and not stripping: an address genuinely beginning with ``-``
should still be readable. A leading apostrophe is how spreadsheets have always
meant "this is text", it survives a round trip, and it loses no information.

The escaping happens in :class:`SafeCsvWriter`, which is the *only* CSV writer
in the codebase, so an export added later cannot forget to use it.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

__all__ = ["FORMULA_PREFIXES", "SafeCsvWriter", "escape_cell", "rows_to_csv"]

#: Characters a spreadsheet treats as the start of a formula.
FORMULA_PREFIXES = ("=", "+", "-", "@")

#: Characters Excel discards before evaluating the first character, so a cell
#: starting "\t=cmd" is still a formula. Stripped before the prefix check.
_LEADING_STRIPPABLE = "\t\r\n\x00 "


def escape_cell(value: Any) -> str:
    """Render one value as a CSV cell that cannot become a formula."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")

    text = str(value)
    # Normalise embedded line breaks. csv would quote them correctly, but a
    # cell spanning rows makes a support engineer's grep useless and gives an
    # attacker a second place to hide a leading "=".
    text = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    text = text.replace("\x00", "")

    probe = text.lstrip(_LEADING_STRIPPABLE)
    if probe.startswith(FORMULA_PREFIXES):
        return "'" + text
    return text


class SafeCsvWriter:
    """A CSV writer that escapes every cell on the way out.

    Deliberately the only writer: there is no path in the codebase that reaches
    :func:`csv.writer` directly, so "we forgot to escape this export" cannot
    happen by omission.
    """

    def __init__(self, headers: Sequence[str]) -> None:
        self._buffer = io.StringIO()
        # QUOTE_MINIMAL plus escaping: quoting alone does not stop a formula,
        # and quoting everything makes the file harder to read for no gain.
        self._writer = csv.writer(self._buffer, lineterminator="\n")
        self._headers = list(headers)
        self._writer.writerow([escape_cell(h) for h in self._headers])
        self._row_count = 0

    def write(self, row: Sequence[Any]) -> None:
        self._writer.writerow([escape_cell(cell) for cell in row])
        self._row_count += 1

    def write_all(self, rows: Iterable[Sequence[Any]]) -> None:
        for row in rows:
            self.write(row)

    @property
    def row_count(self) -> int:
        return self._row_count

    def getvalue(self) -> str:
        return self._buffer.getvalue()

    def as_bytes(self) -> bytes:
        """UTF-8 with a BOM.

        Excel on Windows reads a BOM-less UTF-8 CSV as the system codepage,
        which turns every Bangla name in a seller's export into mojibake. The
        BOM is three bytes and makes the file open correctly everywhere the
        sellers actually are.
        """
        return b"\xef\xbb\xbf" + self.getvalue().encode("utf-8")


def rows_to_csv(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> bytes:
    """Convenience wrapper for a complete, escaped CSV file."""
    writer = SafeCsvWriter(headers)
    writer.write_all(rows)
    return writer.as_bytes()
