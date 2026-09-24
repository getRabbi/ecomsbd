"""Suggested V2 automation rules to switch on after a connection goes live.

A recipe is a prefilled ``RuleInput`` for the existing rule engine and nothing
more: installing one goes through the V2 create path, with its validation,
rule limit and permission checks. Recipes the engine cannot express are not
offered (a "parcel returned" trigger does not exist in V2).
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.models import AutomationRule
from app.automation.schemas import RuleInput
from app.core.errors import NotFoundError
from app.messaging import service as messaging
from app.messaging.models import Channel

RECIPES: dict[str, dict[str, Any]] = {
    "external_order_followup": {
        "name": {"en": "Call to confirm new online orders", "bn": "নতুন অনলাইন অর্ডার ফোনে নিশ্চিত করুন"},
        "trigger": "order.created",
        "conditions": [{"field": "channel", "op": "eq", "value": "API"}],
        "action": "CREATE_FOLLOWUP",
        "config": lambda locale: {
            "text": "Call the customer to confirm this online order"
            if locale == "en"
            else "অনলাইন অর্ডারটি নিশ্চিত করতে গ্রাহককে ফোন করুন",
            "due_hours": 2,
        },
    },
    "confirmed_notify": {
        "name": {"en": "Tell me when an order is confirmed", "bn": "অর্ডার নিশ্চিত হলে আমাকে জানান"},
        "trigger": "order.status_changed",
        "conditions": [{"field": "status", "op": "eq", "value": "CONFIRMED"}],
        "action": "SELLER_NOTIFICATION",
        "config": lambda locale: {
            "title_en": "Order confirmed",
            "title_bn": "অর্ডার নিশ্চিত হয়েছে",
            "text_en": "An order was confirmed and is ready to pack.",
            "text_bn": "একটি অর্ডার নিশ্চিত হয়েছে, প্যাক করার জন্য প্রস্তুত।",
        },
    },
    "booked_tracking": {
        "name": {
            "en": "Send an update when the courier is booked",
            "bn": "কুরিয়ার বুক হলে গ্রাহককে আপডেট পাঠান",
        },
        "trigger": "order.status_changed",
        "conditions": [{"field": "status", "op": "eq", "value": "FULFILLMENT_STARTED"}],
        "action": "SEND_TEMPLATE",
        "config": lambda locale: {
            "template_key": "order_update",
            "locale": locale,
            "channel": "EMAIL",
        },
    },
    "cancelled_followup": {
        "name": {"en": "Follow up on cancelled orders", "bn": "বাতিল অর্ডারের খোঁজ নিন"},
        "trigger": "order.status_changed",
        "conditions": [{"field": "status", "op": "eq", "value": "CANCELLED"}],
        "action": "CREATE_FOLLOWUP",
        "config": lambda locale: {
            "text": "Ask the customer why the order was cancelled"
            if locale == "en"
            else "অর্ডার কেন বাতিল হলো, গ্রাহকের কাছে জানুন",
            "due_hours": 24,
        },
    },
}


def rule_input(key: str, locale: str) -> RuleInput:
    recipe = RECIPES.get(key)
    if recipe is None:
        raise NotFoundError()
    locale = "bn" if locale == "bn" else "en"
    return RuleInput(
        name=recipe["name"][locale],
        trigger=recipe["trigger"],
        conditions=recipe["conditions"],
        action=recipe["action"],
        config=recipe["config"](locale),
        enabled=True,
    )


async def _blocker(db: AsyncSession, action: str) -> str | None:
    if action != "SEND_TEMPLATE":
        return None
    channel = await db.scalar(sa.select(Channel).where(Channel.kind == "EMAIL"))
    if not messaging.capability("EMAIL")[0] or channel is None or not channel.enabled:
        return "CHANNEL_DISABLED"
    return None


async def catalog(db: AsyncSession) -> list[dict[str, Any]]:
    rules = (await db.scalars(sa.select(AutomationRule))).all()
    shapes = {(r.trigger, r.action, repr(r.conditions)) for r in rules}
    items = []
    for key, recipe in RECIPES.items():
        blocker = await _blocker(db, recipe["action"])
        items.append(
            {
                "key": key,
                "name": recipe["name"],
                "trigger": recipe["trigger"],
                "action": recipe["action"],
                "installed": (recipe["trigger"], recipe["action"], repr(recipe["conditions"]))
                in shapes,
                "available": blocker is None,
                "blocker": blocker,
            }
        )
    return items
