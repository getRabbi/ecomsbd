"""Seller-ready workflow recipes (V3.4).

A recipe is a prefilled definition and nothing more. Installing one creates an
ordinary workflow through the same validation and publish path as the builder,
so it stays editable afterwards and cannot do anything a hand-built workflow
could not. Tags a recipe needs are created (or reused) through the CRM.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.models import AutomationRule

Params = dict[str, Any]


def _t(locale: str, en: str, bn: str) -> str:
    return bn if locale == "bn" else en


def _followup(step_id: str, locale: str, en: str, bn: str, hours: int) -> dict:
    return {
        "type": "action",
        "id": step_id,
        "action": "CREATE_FOLLOWUP",
        "config": {"text": _t(locale, en, bn), "due_hours": hours},
    }


def _notice(step_id: str, audience: str, title: tuple[str, str], text: tuple[str, str]) -> dict:
    return {
        "type": "action",
        "id": step_id,
        "action": "SELLER_NOTIFICATION",
        "config": {
            "title_en": title[0],
            "title_bn": title[1],
            "text_en": text[0],
            "text_bn": text[1],
            "audience": audience,
        },
    }


def _message(step_id: str, p: Params) -> dict:
    return {
        "type": "action",
        "id": step_id,
        "action": "SEND_TEMPLATE",
        "config": {"template_key": "order_update", "locale": p["locale"], "channel": p["channel"]},
    }


def _tag(step_id: str, p: Params, name: str) -> dict:
    return {
        "type": "action",
        "id": step_id,
        "action": "ADD_TAG",
        "config": {"tag_id": p["tags"][name]},
    }


def _web_order(p: Params) -> dict:
    return {
        "trigger": "order.external_received",
        "steps": [
            _followup(
                "confirm_call",
                p["locale"],
                "Call the customer to confirm this website order",
                "ওয়েবসাইটের অর্ডারটি নিশ্চিত করতে গ্রাহককে ফোন করুন",
                2,
            )
        ],
    }


def _book_and_track(p: Params) -> dict:
    return {
        "trigger": "order.confirmed",
        "steps": [
            {
                "type": "action",
                "id": "book",
                "action": "BOOK_COURIER",
                "config": {"provider": p["provider"]},
            },
            _message("tracking", p),
        ],
    }


def _returned(p: Params) -> dict:
    return {
        "trigger": "order.returned",
        "steps": [
            _tag("tag_returned", p, "returned"),
            _followup(
                "ask_why",
                p["locale"],
                "Ask the customer why the parcel came back",
                "পার্সেল কেন ফেরত এলো, গ্রাহকের কাছে জানুন",
                24,
            ),
        ],
    }


def _delivered_store(p: Params) -> dict:
    return {
        "trigger": "order.delivered",
        "conditions": {
            "match": "all",
            "conditions": [
                {"field": "external_source", "op": "in", "value": ["SHOPIFY", "WOOCOMMERCE"]}
            ],
        },
        "steps": [{"type": "action", "id": "push", "action": "PUSH_STORE_STATUS", "config": {}}],
    }


def _low_stock(p: Params) -> dict:
    return {
        "trigger": "inventory.low",
        "steps": [
            _notice(
                "tell_owner",
                "INVENTORY",
                ("Stock is running low", "স্টক কমে যাচ্ছে"),
                (
                    "A product just reached its low-stock level. Restock it before it sells out.",
                    "একটি পণ্য কম স্টকের সীমায় পৌঁছেছে। শেষ হওয়ার আগে স্টক যোগ করুন।",
                ),
            )
        ],
    }


def _cod_overdue(p: Params) -> dict:
    return {
        "trigger": "payout.overdue",
        "steps": [
            _notice(
                "tell_finance",
                "FINANCE",
                ("COD payout is overdue", "COD পেমেন্ট সময়মতো আসেনি"),
                (
                    "A courier is holding COD past its usual payout time. Check Money.",
                    "একটি কুরিয়ার সময়ের পরও COD টাকা ধরে রেখেছে। মানি পেজ দেখুন।",
                ),
            )
        ],
    }


def _integration_retry(p: Params) -> dict:
    return {
        "trigger": "integration.sync_failed",
        "steps": [
            _notice(
                "tell_owner",
                "OPERATIONS",
                ("A store sync failed", "স্টোর সিঙ্ক ব্যর্থ হয়েছে"),
                (
                    "ecomsbd will retry it once in 30 minutes. Open Integrations for details.",
                    "৩০ মিনিট পরে একবার আবার চেষ্টা করা হবে। বিস্তারিত ইন্টিগ্রেশন পেজে।",
                ),
            ),
            {"type": "delay", "id": "wait", "mode": "duration", "minutes": 30},
            {"type": "action", "id": "retry", "action": "RETRY_INTEGRATION_SYNC", "config": {}},
        ],
    }


def _repeat_buyer(p: Params) -> dict:
    return {
        "trigger": "customer.segment_entered",
        "conditions": {
            "match": "all",
            "conditions": [{"field": "entered_segment", "op": "eq", "value": "REPEAT"}],
        },
        "steps": [_tag("tag_repeat", p, "repeat")],
    }


def _inactive(p: Params) -> dict:
    return {
        "trigger": "customer.segment_entered",
        "conditions": {
            "match": "all",
            "conditions": [{"field": "entered_segment", "op": "eq", "value": "INACTIVE"}],
        },
        "steps": [
            # A campaign audience can target this tag; the campaign then checks
            # marketing consent. Automation never sends marketing itself.
            _tag("tag_candidate", p, "candidate"),
            _followup(
                "check_in",
                p["locale"],
                "This customer has not ordered for 90 days. Consider a check-in.",
                "এই গ্রাহক ৯০ দিন অর্ডার করেননি। একবার খোঁজ নেওয়ার কথা ভাবুন।",
                48,
            ),
        ],
    }


def _confirm_then_ship(p: Params) -> dict:
    return {
        "trigger": "order.external_received",
        "steps": [
            {
                "type": "branch",
                "id": "stock",
                "conditions": {
                    "match": "all",
                    "conditions": [{"field": "items_in_stock", "op": "is_true"}],
                },
                "then": [
                    _message("confirm_msg", p),
                    {
                        "type": "wait_event",
                        "id": "wait_confirm",
                        "event": "order.confirmed",
                        "timeout_minutes": 24 * 60,
                        "on_timeout": "continue",
                    },
                    {
                        "type": "branch",
                        "id": "confirmed",
                        "conditions": {
                            "match": "all",
                            "conditions": [
                                {
                                    "field": "status",
                                    "op": "in",
                                    "value": ["CONFIRMED", "PACKED", "FULFILLMENT_STARTED"],
                                }
                            ],
                        },
                        "then": [
                            {
                                "type": "action",
                                "id": "book",
                                "action": "BOOK_COURIER",
                                "config": {"provider": p["provider"]},
                            },
                            _message("tracking_msg", p),
                            _tag("tag_shipped", p, "automated"),
                        ],
                        "else": [
                            _followup(
                                "not_confirmed",
                                p["locale"],
                                "Not confirmed after a day: call the customer",
                                "এক দিনেও নিশ্চিত হয়নি: গ্রাহককে ফোন করুন",
                                4,
                            )
                        ],
                    },
                ],
                "else": [
                    _notice(
                        "no_stock",
                        "INVENTORY",
                        ("Website order without stock", "স্টক ছাড়া ওয়েবসাইট অর্ডার"),
                        (
                            "A new website order has items that are not in stock.",
                            "নতুন ওয়েবসাইট অর্ডারের কিছু পণ্য স্টকে নেই।",
                        ),
                    )
                ],
            }
        ],
    }


#: Tag names a recipe creates if the shop does not have them yet.
TAGS: dict[str, tuple[str, str]] = {
    "returned": ("Returned parcel", "ফেরত পার্সেল"),
    "repeat": ("Repeat buyer", "নিয়মিত ক্রেতা"),
    "candidate": ("Campaign candidate", "ক্যাম্পেইনের জন্য"),
    "automated": ("Auto-shipped", "অটো-শিপড"),
}


RECIPES: dict[str, dict[str, Any]] = {
    "web_order_followup": {
        "name": ("New website order → confirm by phone", "নতুন ওয়েবসাইট অর্ডার → ফোনে নিশ্চিত করুন"),
        "build": _web_order,
        "needs": [],
    },
    "confirmed_book_track": {
        "name": (
            "Order confirmed → book courier → send update",
            "অর্ডার নিশ্চিত → কুরিয়ার বুক → আপডেট পাঠান",
        ),
        "build": _book_and_track,
        "needs": ["provider", "channel"],
    },
    "returned_followup": {
        "name": ("Returned parcel → tag and follow up", "ফেরত পার্সেল → ট্যাগ ও ফলো-আপ"),
        "build": _returned,
        "needs": ["tags:returned"],
    },
    "delivered_update_store": {
        "name": ("Delivered → update my website", "ডেলিভারি হলে → ওয়েবসাইট আপডেট"),
        "build": _delivered_store,
        "needs": [],
    },
    "low_stock_notify": {
        "name": ("Low stock → notify owner", "স্টক কম → মালিককে জানান"),
        "build": _low_stock,
        "needs": [],
    },
    "cod_overdue_finance": {
        "name": ("COD overdue → tell finance", "COD বাকি → হিসাব বিভাগকে জানান"),
        "build": _cod_overdue,
        "needs": [],
    },
    "integration_failed_retry": {
        "name": (
            "Store sync failed → notify and retry once",
            "স্টোর সিঙ্ক ব্যর্থ → জানান ও একবার আবার চেষ্টা",
        ),
        "build": _integration_retry,
        "needs": [],
    },
    "repeat_buyer_tag": {
        "name": ("Repeat buyer → add tag", "নিয়মিত ক্রেতা → ট্যাগ যোগ"),
        "build": _repeat_buyer,
        "needs": ["tags:repeat"],
    },
    "inactive_candidate": {
        "name": ("Inactive customer → campaign candidate", "নিষ্ক্রিয় গ্রাহক → ক্যাম্পেইনের জন্য চিহ্নিত"),
        "build": _inactive,
        "needs": ["tags:candidate"],
    },
    "confirm_then_ship": {
        "name": (
            "Website order → check stock → confirm → ship",
            "ওয়েবসাইট অর্ডার → স্টক দেখুন → নিশ্চিত → পাঠান",
        ),
        "build": _confirm_then_ship,
        "needs": ["provider", "channel", "tags:automated"],
    },
}


def build(key: str, params: Params) -> dict[str, Any]:
    recipe = RECIPES[key]
    builder: Callable[[Params], dict] = recipe["build"]
    definition = builder(params)
    definition.setdefault("conditions", {"match": "all", "conditions": [], "groups": []})
    return definition


def needs_tags(key: str) -> list[str]:
    return [need.split(":", 1)[1] for need in RECIPES[key]["needs"] if need.startswith("tags:")]


async def catalog(db: AsyncSession) -> list[dict[str, Any]]:
    installed = set(
        (
            await db.scalars(
                sa.select(AutomationRule.recipe_key).where(AutomationRule.recipe_key.is_not(None))
            )
        ).all()
    )
    items = []
    for key, recipe in RECIPES.items():
        sample = build(
            key,
            {
                "locale": "en",
                "provider": "steadfast",
                "channel": "EMAIL",
                "tags": dict.fromkeys(TAGS, "00000000-0000-0000-0000-000000000000"),
            },
        )
        items.append(
            {
                "key": key,
                "name": {"en": recipe["name"][0], "bn": recipe["name"][1]},
                "trigger": sample["trigger"],
                "needs": [n for n in recipe["needs"] if not n.startswith("tags:")],
                "installed": key in installed,
            }
        )
    return items
