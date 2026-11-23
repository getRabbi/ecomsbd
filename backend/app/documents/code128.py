"""Code 128 set B barcodes as SVG, in pure Python (V4 WP0.5).

Set B covers printable ASCII (space to ``~``), which is every order number and
every courier tracking id this app prints. The output is a self-contained
``<svg>`` with one ``<rect>`` per bar, so it prints sharply at any size and
needs no image library.

Symbol widths are the ISO/IEC 15417 table: each symbol is three bars and three
spaces, 11 modules wide; the stop symbol adds a final 2-module bar (13). A
barcode is: quiet zone, start B (104), data values, checksum, stop, quiet zone.
Checksum = (104 + Σ position x value) mod 103, positions counted from 1.
"""

from __future__ import annotations

from html import escape

__all__ = ["PATTERNS", "START_B", "STOP", "checksum", "encode_values", "modules", "svg"]

#: Bar/space widths for values 0..106 (103 = start A, 104 = start B,
#: 105 = start C, 106 = stop).
PATTERNS: tuple[str, ...] = (
    "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312",
    "132212", "221213", "221312", "231212", "112232", "122132", "122231", "113222",
    "123122", "123221", "223211", "221132", "221231", "213212", "223112", "312131",
    "311222", "321122", "321221", "312212", "322112", "322211", "212123", "212321",
    "232121", "111323", "131123", "131321", "112313", "132113", "132311", "211313",
    "231113", "231311", "112133", "112331", "132131", "113123", "113321", "133121",
    "313121", "211331", "231131", "213113", "213311", "213131", "311123", "311321",
    "331121", "312113", "312311", "332111", "314111", "221411", "431111", "111224",
    "111422", "121124", "121421", "141122", "141221", "112214", "112412", "122114",
    "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111",
    "111242", "121142", "121241", "114212", "124112", "124211", "411212", "421112",
    "421211", "212141", "214121", "412121", "111143", "111341", "131141", "114113",
    "114311", "411113", "411311", "113141", "114131", "311141", "411131", "211412",
    "211214", "211232", "2331112",
)  # fmt: skip

START_B = 104
STOP = 106
_QUIET_MODULES = 10


def _value(char: str) -> int:
    code = ord(char)
    if not 32 <= code <= 126:
        raise ValueError("Code 128-B encodes printable ASCII only")
    return code - 32


def checksum(text: str) -> int:
    total = START_B + sum(index * _value(char) for index, char in enumerate(text, start=1))
    return total % 103


def encode_values(text: str) -> list[int]:
    """Start B, the data values, the checksum and stop."""
    if not text:
        raise ValueError("Nothing to encode")
    return [START_B, *(_value(char) for char in text), checksum(text), STOP]


def modules(text: str) -> str:
    """The barcode as a run-width string, quiet zones excluded (bar first)."""
    return "".join(PATTERNS[value] for value in encode_values(text))


def svg(text: str, *, height: int = 60, module_width: float = 2.0) -> str:
    """A standalone SVG of ``text`` (no human-readable line; the caller prints that)."""
    widths = modules(text)
    total_modules = sum(int(w) for w in widths) + 2 * _QUIET_MODULES
    x = _QUIET_MODULES
    rects = []
    for index, width in enumerate(widths):
        run = int(width)
        if index % 2 == 0:  # bars at even positions, spaces at odd
            rects.append(f'<rect x="{x}" y="0" width="{run}" height="{height}"/>')
        x += run
    view_width = total_modules
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-label="{escape(text)}" '
        f'viewBox="0 0 {view_width} {height}" width="{view_width * module_width:g}" '
        f'height="{height}" preserveAspectRatio="none" shape-rendering="crispEdges">'
        f'<rect width="{view_width}" height="{height}" fill="#fff"/>'
        f'<g fill="#000">{"".join(rects)}</g></svg>'
    )
