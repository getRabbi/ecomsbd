"""Seller-facing wording for smart alerts, in English and Bangla.

A V2.2 alert stores its *facts* — kind plus parameters — and is worded when it
is read or pushed, in the reader's language. The English rendering is also
written to ``title``/``body`` at creation, so support, the audit trail and any
client older than this module read the same sentence.

Only numbers the detector measured appear here. No template predicts, scores
or characterises a business; each one states a count, an amount and a window.
Provider and technical terms (COD, RTO, Pathao, Steadfast) stay in English in
both languages, as they do on the mobile app and the web dashboard.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from app.common.money import format_bdt
from app.notifications.models import NotificationKind

__all__ = ["normalize_locale", "render"]

Params = Mapping[str, Any]


def normalize_locale(locale: str | None) -> str:
    """``bn``, ``bn-BD``, ``bn_BD`` → ``bn``; anything else → ``en``."""
    if locale and locale.strip().lower().replace("_", "-").split("-")[0] == "bn":
        return "bn"
    return "en"


def render(
    kind: NotificationKind | str, params: Params, locale: str | None
) -> tuple[str, str] | None:
    """``(title, body)`` for a structured alert, or ``None`` when the row has
    no template (a pre-V2.2 notification, whose stored text is used as is)."""
    try:
        kind = NotificationKind(str(kind))
    except ValueError:
        return None
    renderer = _RENDERERS.get(kind)
    if renderer is None or not params:
        return None
    try:
        return renderer(params, normalize_locale(locale))
    except (KeyError, TypeError, ValueError):
        # A row written by a newer build with parameters this one does not
        # know. Its stored English is still correct.
        return None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

_PROVIDERS = {"steadfast": "Steadfast", "pathao": "Pathao", "redx": "RedX", "manual": "Manual"}


def _provider(value: object) -> str:
    text = str(value or "")
    return _PROVIDERS.get(text.lower(), text.title())


def _money(paisa: int | str | None) -> str:
    return format_bdt(int(paisa or 0))


def _pct(bps: int | str | None) -> str:
    text = f"{int(bps or 0) / 100:.1f}"
    return text[:-2] if text.endswith(".0") else text


def _n(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


_CASE_LABEL = {
    "MISSING_COD": ("missing COD", "COD পাওয়া যায়নি"),
    "UNDERPAID": ("paid short", "কম টাকা এসেছে"),
    "OVERPAID": ("paid extra", "বেশি টাকা এসেছে"),
    "CHARGE_MISMATCH": ("charge mismatch", "চার্জ মেলেনি"),
    "RETURN_CHARGE_MISMATCH": ("return charge mismatch", "রিটার্ন চার্জ মেলেনি"),
    "UNKNOWN_DEDUCTION": ("unexplained deduction", "অজানা কর্তন"),
    "DUPLICATE_PAYOUT_LINE": ("duplicate payout line", "ডুপ্লিকেট পেআউট লাইন"),
    "UNMAPPABLE_PAYOUT": ("unmatched payout line", "মেলানো যায়নি এমন পেআউট লাইন"),
}

_STATUS_LABEL = {
    "BOOKED": ("booked, not picked up", "বুক হয়েছে, পিকআপ হয়নি"),
    "PICKED_UP": ("picked up", "পিকআপ হয়েছে"),
    "IN_TRANSIT": ("in transit", "পথে আছে"),
    "OUT_FOR_DELIVERY": ("out for delivery", "ডেলিভারির জন্য বের হয়েছে"),
    "RETURN_REQUESTED": ("return requested", "রিটার্ন অনুরোধ করা হয়েছে"),
    "RETURNING": ("returning", "ফেরত আসছে"),
}

_TEMPLATE_LABEL = {
    "ORDERS": ("order", "অর্ডার"),
    "PRODUCTS": ("product", "পণ্য"),
    "PAYOUT_STATEMENT": ("payout statement", "পেআউট স্টেটমেন্ট"),
}


def _breakdown(
    counts: Mapping[str, int], labels: Mapping[str, tuple[str, str]], locale: str
) -> str:
    index = 0 if locale == "en" else 1
    parts = [
        (f"{count} {labels[key][index]}" if locale == "en" else f"{labels[key][index]} {count}টি")
        for key, count in sorted(counts.items(), key=lambda item: (-int(item[1]), item[0]))
        if int(count) > 0 and key in labels
    ]
    return ", ".join(parts)


# --------------------------------------------------------------------------- #
# One renderer per kind
# --------------------------------------------------------------------------- #


def _payout_overdue(p: Params, locale: str) -> tuple[str, str]:
    provider, amount, count, days = (
        _provider(p["provider"]),
        _money(p["amount_paisa"]),
        int(p["count"]),
        int(p["days"]),
    )
    manual = str(p["provider"]).lower() == "manual"
    if locale == "bn":
        return (
            f"{amount} COD বকেয়া" if manual else f"{provider}: {amount} বকেয়া",
            f"ডেলিভারি হওয়া {count}টি পার্সেলের টাকা {days} দিনের বেশি সময় ধরে আসেনি।",
        )
    return (
        f"{amount} COD overdue" if manual else f"{provider}: {amount} overdue",
        f"{_n(count, 'delivered parcel has', 'delivered parcels have')} been unpaid "
        f"for more than {days} days.",
    )


def _reconciliation(p: Params, locale: str) -> tuple[str, str]:
    count, amount = int(p["count"]), int(p.get("amount_paisa") or 0)
    parts = _breakdown(p.get("by_kind") or {}, _CASE_LABEL, locale)
    if locale == "bn":
        body = f"{parts}।" if parts else ""
        if amount:
            body = f"{body} মোট {_money(amount)} নিয়ে প্রশ্ন।".strip()
        return f"{count}টি সেটেলমেন্ট অমিল দেখতে হবে", body
    body = f"{parts[:1].upper()}{parts[1:]}." if parts else ""
    if amount:
        body = f"{body} {_money(amount)} in question.".strip()
    return f"{_n(count, 'settlement discrepancy', 'settlement discrepancies')} to check", body


def _courier_stuck(p: Params, locale: str) -> tuple[str, str]:
    count, oldest, cod = int(p["count"]), int(p["oldest_days"]), int(p.get("cod_paisa") or 0)
    parts = _breakdown(p.get("by_status") or {}, _STATUS_LABEL, locale)
    if locale == "bn":
        body = (
            f"অনেক দিন কুরিয়ারের কোনো আপডেট নেই: {parts}। সবচেয়ে পুরনোটি {oldest} দিন ধরে একই অবস্থায়।"
        )
        if cod:
            body += f" এগুলোতে {_money(cod)} COD।"
        return f"{count}টি পার্সেল কুরিয়ারে আটকে আছে", body
    body = f"No courier update for days: {parts}. The oldest has not moved in {oldest} days."
    if cod:
        body += f" {_money(cod)} COD on them."
    return f"{_n(count, 'parcel', 'parcels')} not moving at the courier", body


def _high_rto(p: Params, locale: str) -> tuple[str, str]:
    rto, completed, days, rate = int(p["rto"]), int(p["completed"]), int(p["days"]), p["rate_bps"]
    previous = p.get("prev_completed")
    if locale == "bn":
        body = f"গত {days} দিনে সম্পন্ন {completed}টি পার্সেলের {rto}টি RTO হয়েছে।"
        if previous:
            body += (
                f" তার আগের {days} দিনে: {int(previous)}টির {int(p['prev_rto'])}টি "
                f"({_pct(p['prev_rate_bps'])}%)।"
            )
        return f"গত {days} দিনে RTO {_pct(rate)}%", body
    body = f"{rto} of {completed} completed parcels were RTO in the last {days} days."
    if previous:
        body += (
            f" The {days} days before: {int(p['prev_rto'])} of {int(previous)} "
            f"({_pct(p['prev_rate_bps'])}%)."
        )
    return f"RTO {_pct(rate)}% in the last {days} days", body


def _low_stock(p: Params, locale: str) -> tuple[str, str]:
    count = int(p["count"])
    names = [str(name) for name in p.get("names") or []]
    single = p.get("single")
    more = count - len(names)
    if single and count == 1:
        name, stock, threshold = single["name"], int(single["stock"]), int(single["threshold"])
        if locale == "bn":
            return (
                f"{name}: আর {stock}টি আছে",
                f"স্টক আপনার সতর্কতা সীমা {threshold}-এ বা তার নিচে নেমেছে।",
            )
        return f"{name}: {stock} left", f"Stock is at or below your alert level of {threshold}."
    if locale == "bn":
        tail = f" ও আরও {more}টি" if more > 0 else ""
        return f"{count}টি পণ্যের স্টক কম", f"{', '.join(names)}{tail}।"
    tail = f" and {more} more" if more > 0 else ""
    return (
        f"{count} products at or below their low-stock level",
        f"{', '.join(names)}{tail}.",
    )


def _negative_margin(p: Params, locale: str) -> tuple[str, str]:
    count, loss, days = int(p["count"]), _money(p["loss_paisa"]), int(p["days"])
    if locale == "bn":
        return (
            f"ডেলিভারি হওয়া {count}টি পার্সেলে লোকসান",
            f"গত {days} দিনে সব খরচ জানা থাকা এই পার্সেলগুলোতে মোট {loss} লোকসান হয়েছে।",
        )
    return (
        f"{_n(count, 'delivered parcel', 'delivered parcels')} made a loss",
        f"With every cost recorded, they lost {loss} in total over the last {days} days.",
    )


def _import_failure(p: Params, locale: str) -> tuple[str, str]:
    index = 0 if locale == "en" else 1
    template = _TEMPLATE_LABEL.get(str(p.get("template")), ("import", "ইমপোর্ট"))[index]
    file = str(p.get("file") or "")
    if p["reason"] == "REJECTED_ROWS":
        rejected, created, rows = int(p["rejected"]), int(p["created"]), int(p["row_count"])
        if locale == "bn":
            return (
                f"ইমপোর্ট শেষ, {rejected}টি সারি বাদ পড়েছে",
                f"{file} থেকে {rows}টির মধ্যে {created}টি {template} সারি ইমপোর্ট হয়েছে। "
                "বাকিগুলো কেন বাদ পড়েছে দেখতে ইমপোর্টটি খুলুন।",
            )
        return (
            f"Import finished with {_n(rejected, 'rejected row', 'rejected rows')}",
            f"{created} of {rows} {template} rows from {file} were imported. "
            "Open the import to see why the others were rejected.",
        )
    if locale == "bn":
        return (
            "ইমপোর্ট শেষ হয়নি",
            f"{file} থেকে {template} ইমপোর্ট মাঝপথে থেমে গেছে। যেসব সারি তৈরি হয়েছে সেগুলো "
            "নিরাপদ; বাকিগুলো চালাতে ইমপোর্টটি খুলুন।",
        )
    return (
        "Import did not finish",
        f"The {template} import from {file} stopped before finishing. Rows already "
        "created are safe; open it to run the rest.",
    )


def _courier_account(p: Params, locale: str) -> tuple[str, str]:
    provider = _provider(p["provider"])
    if locale == "bn":
        return (
            f"{provider} অ্যাকাউন্ট আবার সংযুক্ত করতে হবে",
            f"{provider} সংরক্ষিত ক্রেডেনশিয়াল বারবার প্রত্যাখ্যান করেছে। আবার দেওয়া না "
            "পর্যন্ত এই অ্যাকাউন্ট দিয়ে বুকিং হবে না।",
        )
    return (
        f"{provider} account needs reconnecting",
        f"{provider} has repeatedly rejected the saved credentials. Booking through this "
        "account will not work until they are entered again.",
    )


def _returned_not_restocked(p: Params, locale: str) -> tuple[str, str]:
    count = int(p["count"])
    if locale == "bn":
        return "রিটার্ন স্টকে ফেরেনি", f"ফেরত আসা {count}টি পার্সেলের পণ্য স্টকে যোগ হয়নি।"
    return (
        "Returns not back in stock",
        f"{_n(count, 'returned parcel', 'returned parcels')} came back but "
        f"{'its items were' if count == 1 else 'their items were'} never added to stock.",
    )


def _weekly_summary(p: Params, locale: str) -> tuple[str, str]:
    profit = _money(p["contribution_profit_paisa"])
    delivered, returned = int(p["delivered_count"]), int(p["return_count"])
    cod = _money(p["cod_outstanding_paisa"])
    if locale == "bn":
        return (
            f"আপনার সপ্তাহ: {profit} লাভ",
            f"{delivered}টি ডেলিভারি, {returned}টি ফেরত। {cod} এখনো কুরিয়ারের কাছে।",
        )
    return (
        f"Your week: {profit} profit",
        f"{delivered} delivered, {returned} came back. {cod} still with couriers.",
    )


def _follow_up_due(p: Params, locale: str) -> tuple[str, str]:
    count = int(p["count"])
    if locale == "bn":
        return "ফলো-আপের সময় হয়েছে", f"{count}টি ফলো-আপ বাকি আছে। কাস্টমার তালিকা দেখুন।"
    return "Customer follow-ups due", f"{count} follow-ups are due. Open your customer list."


def _po_overdue(p: Params, locale: str) -> tuple[str, str]:
    count, numbers = int(p["count"]), ", ".join(str(n) for n in p.get("numbers") or [])
    if locale == "bn":
        return (
            f"{count}টি ক্রয় আদেশের মাল আসেনি",
            f"প্রত্যাশিত তারিখ পেরিয়ে গেছে: {numbers}। সরবরাহকারীর সঙ্গে যোগাযোগ করুন।",
        )
    return (
        f"{_n(count, 'purchase order is', 'purchase orders are')} late",
        f"Past the expected date: {numbers}. Check with the supplier.",
    )


def _stockout_predicted(p: Params, locale: str) -> tuple[str, str]:
    count, names = int(p["count"]), ", ".join(str(n) for n in p.get("names") or [])
    if locale == "bn":
        return (
            f"{count}টি পণ্য শেষ হয়ে যেতে পারে",
            f"আবার অর্ডার করলেও সময়মতো আসার আগে ফুরিয়ে যাবে বলে পূর্বাভাস: {names}।",
        )
    return (
        f"{_n(count, 'item is', 'items are')} likely to run out",
        f"Forecast to sell out before a new order could arrive: {names}. Review reorders.",
    )


def _partial_pending(p: Params, locale: str) -> tuple[str, str]:
    count, days = int(p["count"]), int(p["days"])
    numbers = ", ".join(str(n) for n in p.get("numbers") or [])
    if locale == "bn":
        return (
            f"{count}টি ক্রয় আদেশের বাকি মাল আসেনি",
            f"আংশিক মাল আসার {days} দিন পরও বাকি আছে: {numbers}।",
        )
    return (
        f"{_n(count, 'purchase order is', 'purchase orders are')} partly received",
        f"The rest is still outstanding {days}+ days after the first delivery: {numbers}.",
    )


def _supplier_overdue(p: Params, locale: str) -> tuple[str, str]:
    count, amount = int(p["count"]), _money(p["amount_paisa"])
    if locale == "bn":
        return (
            f"সরবরাহকারীর {amount} পরিশোধ বাকি",
            f"{count}টি ক্রয় আদেশের পরিশোধের তারিখ পেরিয়ে গেছে।",
        )
    return (
        f"{amount} due to suppliers",
        f"{_n(count, 'purchase order is', 'purchase orders are')} past the payment due date.",
    )


_CHANNELS = {"MESSENGER": "Messenger", "WHATSAPP": "WhatsApp"}


def _chat_order_ready(p: Params, locale: str) -> tuple[str, str]:
    # No customer name or product: a push is shown on a lock screen.
    channel = _CHANNELS.get(str(p["provider"]), "Chat")
    if locale == "bn":
        return (
            f"{channel} থেকে নতুন অর্ডার পাওয়া গেছে",
            "ইনবক্সে দেখে নিশ্চিত করুন।",
        )
    return f"New order from {channel}", "Review it in the Inbox and confirm."


def _device_needs_attention(p: Params, locale: str) -> tuple[str, str]:
    if str(p.get("platform")) == "extension":
        if locale == "bn":
            return (
                "ব্রাউজার এক্সটেনশন থেকে সাড়া নেই",
                "এক দিনের বেশি সময় ধরে লিংক করা এক্সটেনশন থেকে কোনো সাড়া আসেনি। "
                "ব্রাউজারে এক্সটেনশনটি চালু আছে কিনা দেখুন।",
            )
        return (
            "A linked browser extension went quiet",
            "The linked extension has not checked in for over a day. Check that it is "
            "still installed and switched on.",
        )
    if locale == "bn":
        return (
            "লিংক করা ফোন থেকে সাড়া নেই",
            "এক দিনের বেশি সময় ধরে লিংক করা একটি ফোন থেকে কোনো সাড়া আসেনি। ফোনে অ্যাপটি "
            "খুলে নোটিফিকেশন অ্যাক্সেস চালু আছে কিনা দেখুন।",
        )
    return (
        "A linked phone went quiet",
        "A linked phone has not checked in for over a day, so new orders are not being "
        "detected on it. Open the app on that phone and check notification access.",
    )


#: (en, bn) per reason. No customer name, phone or amount: a push is shown on
#: a lock screen.
_CONFIRMATION_REASONS: dict[str, tuple[tuple[str, str], tuple[str, str]]] = {
    "REVIEW_NEEDED": (
        (
            "An order needs your confirmation",
            "We could not confirm this order with the customer. Call them, then mark it "
            "confirmed or cancelled.",
        ),
        (
            "একটি অর্ডার আপনার নিশ্চিত করা দরকার",
            "কাস্টমারের সাথে অর্ডারটি নিশ্চিত করা যায়নি। কাস্টমারকে কল করে অর্ডারটি নিশ্চিত বা বাতিল করুন।",
        ),
    ),
    "ORDER_CHANGED": (
        (
            "Customer answered, but the order changed",
            "The order was edited after we asked the customer, so their answer was not "
            "applied. Check the order and confirm it yourself.",
        ),
        (
            "কাস্টমার উত্তর দিয়েছেন, কিন্তু অর্ডার বদলেছে",
            "কাস্টমারকে জিজ্ঞেস করার পর অর্ডারটি এডিট হয়েছে, তাই উত্তরটি প্রয়োগ করা হয়নি। "
            "অর্ডারটি দেখে নিজে নিশ্চিত করুন।",
        ),
    ),
    "ORDER_NOT_DRAFT": (
        (
            "Customer answered after the order moved on",
            "The order was already confirmed or cancelled when the customer answered, so "
            "their answer was not applied. Check the order.",
        ),
        (
            "অর্ডার এগিয়ে যাওয়ার পর কাস্টমার উত্তর দিয়েছেন",
            "কাস্টমার উত্তর দেওয়ার আগেই অর্ডারটি নিশ্চিত বা বাতিল হয়ে গিয়েছিল, তাই "
            "উত্তরটি প্রয়োগ করা হয়নি। অর্ডারটি দেখে নিন।",
        ),
    ),
}


def _confirmation_needs_attention(p: Params, locale: str) -> tuple[str, str]:
    en, bn = _CONFIRMATION_REASONS.get(str(p.get("reason")), _CONFIRMATION_REASONS["REVIEW_NEEDED"])
    return bn if locale == "bn" else en


def _chat_callback_requested(_p: Params, locale: str) -> tuple[str, str]:
    """No name or number: a push is shown on a lock screen."""
    if locale == "bn":
        return (
            "কাস্টমার দোকানের সাথে কথা বলতে চান",
            "কনফার্মেশন কলে কাস্টমার ৩ চেপেছেন। কল তালিকা খুলে কাস্টমারকে কল করুন।",
        )
    return (
        "A customer wants to talk to the shop",
        "On the confirmation call the customer pressed 3. Open the call queue and call them.",
    )


_PLAN_NAMES_BN = {"starter": "স্টার্টার", "pro": "প্রো"}


def _billing_payment_reviewed(p: Params, locale: str) -> tuple[str, str]:
    """No TrxID, number or rejection text: a push is shown on a lock screen."""
    plan = str(p.get("plan") or "")
    until = str(p.get("valid_until") or "")
    if str(p.get("state")) == "APPROVED":
        if locale == "bn":
            return (
                "পেমেন্ট গ্রহণ করা হয়েছে",
                f"আপনার {_PLAN_NAMES_BN.get(plan, plan)} প্ল্যান চালু হয়েছে, মেয়াদ {until} পর্যন্ত।",
            )
        return "Payment approved", f"Your {plan.title()} plan is active until {until}."
    if locale == "bn":
        return (
            "পেমেন্ট যাচাই করা যায়নি",
            "আপনার বিকাশ/নগদ পেমেন্টটি গ্রহণ করা যায়নি। কারণ দেখতে বিলিং খুলুন।",
        )
    return (
        "Payment not approved",
        "We could not accept your bKash/Nagad payment. Open Billing to see why.",
    )


def _automation(p: Params, locale: str) -> tuple[str, str]:
    return str(p[f"title_{locale}"]), str(p[f"text_{locale}"])


_RENDERERS: dict[NotificationKind, Callable[[Params, str], tuple[str, str]]] = {
    NotificationKind.AUTOMATION: _automation,
    NotificationKind.CHAT_ORDER_READY: _chat_order_ready,
    NotificationKind.DEVICE_NEEDS_ATTENTION: _device_needs_attention,
    NotificationKind.CONFIRMATION_NEEDS_ATTENTION: _confirmation_needs_attention,
    NotificationKind.CHAT_CALLBACK_REQUESTED: _chat_callback_requested,
    NotificationKind.BILLING_PAYMENT_REVIEWED: _billing_payment_reviewed,
    NotificationKind.FOLLOW_UP_DUE: _follow_up_due,
    NotificationKind.PAYOUT_OVERDUE: _payout_overdue,
    NotificationKind.RECONCILIATION_DISCREPANCY: _reconciliation,
    NotificationKind.COURIER_STATUS_STUCK: _courier_stuck,
    NotificationKind.HIGH_RTO: _high_rto,
    NotificationKind.LOW_STOCK: _low_stock,
    NotificationKind.NEGATIVE_MARGIN: _negative_margin,
    NotificationKind.IMPORT_FAILURE: _import_failure,
    NotificationKind.COURIER_ACCOUNT_PROBLEM: _courier_account,
    NotificationKind.RETURNED_NOT_RESTOCKED: _returned_not_restocked,
    NotificationKind.WEEKLY_SUMMARY: _weekly_summary,
    NotificationKind.PURCHASE_ORDER_OVERDUE: _po_overdue,
    NotificationKind.PARTIAL_RECEIPT_PENDING: _partial_pending,
    NotificationKind.SUPPLIER_PAYMENT_OVERDUE: _supplier_overdue,
    NotificationKind.STOCKOUT_PREDICTED: _stockout_predicted,
}
