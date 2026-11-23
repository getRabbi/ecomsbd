"""Invoice and shipping-label HTML (V4 WP0.5).

Documents are HTML printed by the browser or the phone's print system, because
Bangla needs complex-script shaping that Python and Dart PDF libraries do not
do correctly. Every value from the database or the customer goes through
:func:`html.escape` (:func:`_e`); nothing is formatted into markup unescaped.
The Bangla font is embedded once per HTML as a data URI, so a document renders
the same offline, in a WebView, or in a print preview.
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from functools import lru_cache
from html import escape
from pathlib import Path

from app.common.money import format_bdt
from app.documents import code128

__all__ = [
    "LABELS_PER_A4",
    "DocumentItem",
    "DocumentOrder",
    "LabelSize",
    "render_invoices",
    "render_labels",
]

_FONT_DIR = Path(__file__).resolve().parent / "fonts"
LABELS_PER_A4 = 8


class LabelSize(StrEnum):
    FOUR_BY_SIX = "4x6"
    THREE_BY_FOUR = "3x4"
    A4_EIGHT_UP = "a4-8up"


@dataclass(frozen=True, slots=True)
class DocumentItem:
    name: str
    variant: str | None
    quantity: int
    unit_price_paisa: int
    line_total_paisa: int


@dataclass(frozen=True, slots=True)
class DocumentOrder:
    order_number: str
    order_date: date
    shop_name: str
    shop_phone: str | None
    customer_name: str | None
    #: Full national number, or the masked form when the seller asked for it.
    customer_phone: str | None
    address: str | None
    area: str | None
    district: str | None
    courier: str | None
    tracking: str | None
    subtotal_paisa: int
    discount_paisa: int
    delivery_fee_paisa: int
    cod_amount_paisa: int
    items: Sequence[DocumentItem] = field(default_factory=tuple)
    note: str | None = None


def _e(value: object | None) -> str:
    return "" if value is None else escape(str(value), quote=True)


@lru_cache(maxsize=1)
def _font_faces() -> str:
    faces = []
    for weight in (400, 700):
        data = base64.b64encode((_FONT_DIR / f"NotoSansBengali-{weight}.ttf").read_bytes())
        faces.append(
            "@font-face{font-family:'Noto Sans Bengali';font-style:normal;"
            f"font-weight:{weight};src:url(data:font/ttf;base64,{data.decode('ascii')}) "
            "format('truetype');}"
        )
    return "".join(faces)


_BASE_CSS = (
    "*{box-sizing:border-box}"
    "body{margin:0;color:#000;font-family:'Noto Sans Bengali',system-ui,sans-serif;"
    "-webkit-print-color-adjust:exact;print-color-adjust:exact}"
    ".doc{break-after:page;page-break-after:always;overflow:hidden}"
    ".doc:last-child{break-after:auto;page-break-after:auto}"
    ".muted{color:#444}.num{font-variant-numeric:tabular-nums;white-space:nowrap}"
    ".barcode svg{display:block;width:100%;height:auto;max-height:18mm}"
    ".code{font-family:ui-monospace,monospace;letter-spacing:.06em;text-align:center}"
)


def _page(title: str, page_css: str, body: str) -> str:
    return (
        '<!doctype html><html lang="bn"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_e(title)}</title><style>{_font_faces()}{_BASE_CSS}{page_css}</style>"
        f"</head><body>{body}</body></html>"
    )


def _money(paisa: int) -> str:
    return _e(format_bdt(paisa))


def _barcode(value: str) -> str:
    try:
        return f'<div class="barcode">{code128.svg(value)}</div><div class="code">{_e(value)}</div>'
    except ValueError:
        # Not printable ASCII (should not happen for ids); print the text only.
        return f'<div class="code">{_e(value)}</div>'


def _address(order: DocumentOrder) -> str:
    parts = [order.address]
    for extra in (order.area, order.district):
        if extra and (not order.address or extra.casefold() not in order.address.casefold()):
            parts.append(extra)
    return ", ".join(_e(part) for part in parts if part)


def _item_summary(order: DocumentOrder) -> str:
    lines = []
    for item in order.items:
        name = item.name + (f" ({item.variant})" if item.variant else "")
        lines.append(f"{_e(name)} &times; {item.quantity}")
    return "; ".join(lines)


# --------------------------------------------------------------- invoice ---

_INVOICE_CSS = (
    "@page{size:A4;margin:14mm}"
    ".invoice{width:100%;min-height:260mm;font-size:11pt}"
    ".invoice header{display:flex;justify-content:space-between;align-items:flex-start;"
    "border-bottom:2px solid #000;padding-bottom:4mm;margin-bottom:5mm}"
    ".invoice h1{font-size:18pt;margin:0}.invoice h2{font-size:13pt;margin:0 0 2mm}"
    ".badge{display:inline-block;border:2px solid #000;border-radius:2mm;padding:1mm 3mm;"
    "font-weight:700;font-size:13pt}"
    ".invoice table{width:100%;border-collapse:collapse;margin-top:5mm}"
    ".invoice th,.invoice td{border-bottom:1px solid #999;padding:2mm;text-align:left;"
    "vertical-align:top}"
    ".invoice td.r,.invoice th.r{text-align:right}"
    ".totals{margin-left:auto;margin-top:4mm;width:75mm}"
    ".totals div{display:flex;justify-content:space-between;padding:1mm 0}"
    ".totals .grand{border-top:2px solid #000;font-weight:700;font-size:13pt}"
)


def _invoice(order: DocumentOrder) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{_e(item.name)}{'<br><span class=muted>' + _e(item.variant) + '</span>' if item.variant else ''}</td>"
        f'<td class="r num">{item.quantity}</td>'
        f'<td class="r num">{_money(item.unit_price_paisa)}</td>'
        f'<td class="r num">{_money(item.line_total_paisa)}</td>'
        "</tr>"
        for item in order.items
    )
    total = max(0, order.subtotal_paisa - order.discount_paisa + order.delivery_fee_paisa)
    cod_badge = (
        f'<div class="badge">COD {_money(order.cod_amount_paisa)}</div>'
        if order.cod_amount_paisa > 0
        else ""
    )
    return (
        '<section class="doc invoice">'
        "<header><div>"
        f"<h1>{_e(order.shop_name)}</h1>"
        f'<div class="muted">{_e(order.shop_phone)}</div>'
        '</div><div style="text-align:right">'
        f"<h2>Invoice / চালান</h2><div>#{_e(order.order_number)}</div>"
        f'<div class="muted">{_e(order.order_date.isoformat())}</div>{cod_badge}'
        "</div></header>"
        f"<div><strong>{_e(order.customer_name)}</strong></div>"
        f"<div>{_e(order.customer_phone)}</div><div>{_address(order)}</div>"
        '<table><thead><tr><th>Item / পণ্য</th><th class="r">Qty</th>'
        '<th class="r">Price</th><th class="r">Total</th></tr></thead>'
        f"<tbody>{rows}</tbody></table>"
        '<div class="totals">'
        f'<div><span>Subtotal</span><span class="num">{_money(order.subtotal_paisa)}</span></div>'
        f'<div><span>Delivery</span><span class="num">{_money(order.delivery_fee_paisa)}</span></div>'
        f'<div><span>Discount</span><span class="num">-{_money(order.discount_paisa)}</span></div>'
        f'<div class="grand"><span>Total</span><span class="num">{_money(total)}</span></div>'
        "</div>" + (f'<p class="muted">{_e(order.note)}</p>' if order.note else "") + "</section>"
    )


def render_invoices(orders: Sequence[DocumentOrder]) -> str:
    title = orders[0].order_number if len(orders) == 1 else f"{len(orders)} invoices"
    return _page(f"Invoice {title}", _INVOICE_CSS, "".join(_invoice(o) for o in orders))


# ----------------------------------------------------------------- label ---

_LABEL_GEOMETRY = {
    LabelSize.FOUR_BY_SIX: (
        "@page{size:4in 6in;margin:0}",
        "width:4in;height:6in;padding:5mm",
        "10pt",
        "22pt",
    ),
    LabelSize.THREE_BY_FOUR: (
        "@page{size:3in 4in;margin:0}",
        "width:3in;height:4in;padding:3mm",
        "8pt",
        "16pt",
    ),
    LabelSize.A4_EIGHT_UP: (
        "@page{size:A4;margin:0}",
        "width:105mm;height:74.25mm;padding:4mm",
        "8pt",
        "15pt",
    ),
}


def _label_css(size: LabelSize) -> str:
    page, box, text, cod = _LABEL_GEOMETRY[size]
    css = (
        f"{page}.label{{{box};font-size:{text};display:flex;flex-direction:column;gap:1.5mm;"
        "border:1px dashed #999}"
        ".label .shop{display:flex;justify-content:space-between;gap:2mm;"
        "border-bottom:1px solid #000;padding-bottom:1mm}"
        ".label .to{font-weight:700}.label .addr{flex:1;overflow:hidden}"
        f".label .cod{{font-size:{cod};font-weight:700;border:2px solid #000;text-align:center;"
        "padding:1mm}"
        ".label .meta{display:flex;justify-content:space-between;gap:2mm}"
        ".label .items{max-height:3.2em;overflow:hidden}"
    )
    if size is LabelSize.A4_EIGHT_UP:
        css += (
            ".sheet{width:210mm;height:297mm;display:grid;grid-template-columns:105mm 105mm;"
            "grid-template-rows:repeat(4,74.25mm)}"
            ".sheet .label{break-after:auto;page-break-after:auto}"
        )
    return css


def _label(order: DocumentOrder, *, in_sheet: bool) -> str:
    code = order.tracking or order.order_number
    cod = f"COD {_money(order.cod_amount_paisa)}" if order.cod_amount_paisa > 0 else "PAID / COD 0"
    classes = "label" if in_sheet else "doc label"
    return (
        f'<section class="{classes}">'
        f'<div class="shop"><strong>{_e(order.shop_name)}</strong>'
        f'<span class="num">{_e(order.shop_phone)}</span></div>'
        f'<div class="to">{_e(order.customer_name)}</div>'
        f'<div class="num">{_e(order.customer_phone)}</div>'
        f'<div class="addr">{_address(order)}</div>'
        f'<div class="cod">{cod}</div>'
        f'<div class="meta"><span>{_e(order.courier or "")}</span>'
        f"<span>#{_e(order.order_number)} · {_e(order.order_date.isoformat())}</span></div>"
        f"{_barcode(code)}"
        f'<div class="items muted">{_item_summary(order)}</div>'
        "</section>"
    )


def render_labels(orders: Sequence[DocumentOrder], size: LabelSize) -> str:
    title = orders[0].order_number if len(orders) == 1 else f"{len(orders)} labels"
    if size is LabelSize.A4_EIGHT_UP:
        sheets = []
        for start in range(0, len(orders), LABELS_PER_A4):
            chunk = orders[start : start + LABELS_PER_A4]
            sheets.append(
                '<section class="doc sheet">'
                + "".join(_label(o, in_sheet=True) for o in chunk)
                + "</section>"
            )
        body = "".join(sheets)
    else:
        body = "".join(_label(o, in_sheet=False) for o in orders)
    return _page(f"Label {title}", _label_css(size), body)
