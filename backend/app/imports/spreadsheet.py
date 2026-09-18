"""Reading an XLSX workbook safely enough to import money from it.

V1 refused spreadsheets outright, and the reason it gave was the right one:
"reading it directly would risk misreading your amounts." This module is what
makes that no longer true, rather than a decision to live with the risk.

Four things make it safe, and each one is a real failure it prevents.

**Formulas are never evaluated.** ``data_only=True`` reads the cached value
Excel last wrote. A workbook whose ``=A1*B1`` has never been recalculated
yields ``None``, which becomes an empty cell and fails validation like any
other blank — instead of a number this code invented.

**Every cell becomes the text a CSV would have carried.** Excel stores 1050 as
a float and 2026-09-18 as the serial 46283. Handing either to the money parser
directly is how ``1050`` becomes ``1050.0000000001`` and a date becomes a price.
:func:`cell_to_text` renders each type back to a plain string, and the *same*
parsers the CSV path uses then run on it. XLSX therefore gains no special
number handling — which is precisely why it cannot drift from CSV.

**The sheet is streamed, and bounded.** ``read_only=True`` iterates rather than
materialising, and the row cap is enforced while reading. A workbook that
declares a million rows stops at the limit instead of becoming a memory spike.

**Only the first worksheet is read.** A workbook with three sheets is ambiguous,
and guessing which one holds the orders is exactly the kind of guess that
imports the wrong data silently.
"""

from __future__ import annotations

import datetime as dt
import io
from decimal import Decimal
from typing import Any

from app.core.errors import ValidationError

__all__ = ["MAX_COLUMNS", "cell_to_text", "read_xlsx"]

#: Columns past this are ignored. A real order sheet has a dozen; a file with
#: sixteen thousand is a corrupt export, and reading them all would turn every
#: row into a dict with sixteen thousand empty keys.
MAX_COLUMNS = 64


def cell_to_text(value: Any) -> str:
    """Render one cell as the string a CSV export of the same sheet would hold.

    This is the whole safety argument for XLSX support: after this function,
    nothing downstream can tell a spreadsheet from a CSV, so the money and
    phone parsers that already refuse to coerce bad values are the ones that
    run.
    """
    if value is None:
        return ""

    if isinstance(value, bool):
        # Before the int check: bool is an int in Python, and "True" is what a
        # CSV export writes.
        return "TRUE" if value else "FALSE"

    if isinstance(value, dt.datetime):
        # Midnight almost always means the cell was a date, not a timestamp.
        if value.hour == value.minute == value.second == 0:
            return value.date().isoformat()
        return value.isoformat(sep=" ")

    if isinstance(value, dt.date):
        return value.isoformat()

    if isinstance(value, dt.time):
        return value.isoformat()

    if isinstance(value, int):
        return str(value)

    if isinstance(value, float):
        # Excel holds every number as a float, so an amount typed as 1050
        # arrives as 1050.0. Rendering that as "1050.0" would be read by the
        # money parser as a different thing from what the seller typed.
        if value != value or value in (float("inf"), float("-inf")):  # NaN / inf
            return ""
        if float(value).is_integer():
            return str(int(value))
        # normalize() drops the trailing zeros a float repr leaves behind.
        return format(Decimal(str(value)).normalize(), "f")

    if isinstance(value, Decimal):
        return format(value.normalize(), "f")

    return str(value).strip()


def read_xlsx(content: bytes, *, max_rows: int) -> tuple[list[str], list[dict[str, str]]]:
    """Read the first worksheet into headers and row dicts.

    Returns the same shape the CSV reader returns, so the pipeline behind it is
    identical. Raises :class:`ValidationError` with a sentence a seller can act
    on rather than letting an openpyxl error reach them.
    """
    try:
        from openpyxl import load_workbook
    except ImportError as error:  # pragma: no cover - dependency is declared
        raise ValidationError(
            "Excel files cannot be read on this server yet. Please upload a CSV."
        ) from error

    try:
        workbook = load_workbook(
            io.BytesIO(content),
            # No formula evaluation, and no recalculation: the cached value or
            # nothing.
            data_only=True,
            # Stream the sheet instead of building the whole object graph.
            read_only=True,
            # Charts, images and pivot caches are not data and cost memory.
            keep_links=False,
        )
    except Exception as error:
        raise ValidationError(
            "That Excel file could not be opened. It may be password protected "
            "or damaged — try saving it again, or export it as CSV.",
            details={"reason": type(error).__name__},
        ) from error

    try:
        worksheet = workbook.worksheets[0] if workbook.worksheets else None
        if worksheet is None:
            raise ValidationError("That Excel file has no sheets in it")

        rows_iter = worksheet.iter_rows(values_only=True)

        header_row: tuple[Any, ...] | None = None
        for candidate in rows_iter:
            if any(cell_to_text(cell).strip() for cell in candidate):
                header_row = candidate
                break
        if header_row is None:
            raise ValidationError("That Excel file is empty")

        headers: list[str] = []
        for cell in header_row[:MAX_COLUMNS]:
            # A blank header means the column has no name; its values cannot be
            # mapped to anything, so it is dropped rather than given an
            # invented name like "column_7".
            headers.append(cell_to_text(cell).strip())

        named = [header for header in headers if header]
        if not named:
            raise ValidationError(
                "The first row of that sheet has no column names. Put the "
                "headings in the first row and upload it again."
            )

        rows: list[dict[str, str]] = []
        for record in rows_iter:
            if len(rows) >= max_rows:
                raise ValidationError(
                    f"This file has more than {max_rows} rows; that is the limit. "
                    "Please split it into smaller files.",
                    details={"max_rows": str(max_rows)},
                )
            values = [cell_to_text(cell) for cell in record[:MAX_COLUMNS]]
            if not any(value.strip() for value in values):
                # Trailing blank rows are what Excel leaves behind; they are
                # not failures to report back to the seller.
                continue
            row: dict[str, str] = {}
            for header, value in zip(headers, values, strict=False):
                if header:
                    row[header] = value
            rows.append(row)

        return named, rows
    finally:
        # read_only workbooks hold the zip open until closed.
        workbook.close()
