"""WhatsApp Business Platform (Cloud API), from Meta's developer documentation only.

*   Send an approved template: ``POST /{phone-number-id}/messages`` with
    ``messaging_product=whatsapp``, ``type=template`` and the template's name,
    language code and body parameters. The reply carries ``messages[0].id``
    (a ``wamid``), which later status webhooks name.
*   Check the number: ``GET /{phone-number-id}?fields=display_phone_number,verified_name``.
*   Read templates: ``GET /{waba-id}/message_templates?fields=name,language,status,category``.
*   Receive webhooks for the account: ``POST /{waba-id}/subscribed_apps``.
*   Webhooks: ``object=whatsapp_business_account``; each ``entry[].changes[]``
    with ``field=messages`` has ``value.metadata.phone_number_id``,
    ``value.statuses[]`` (``id``, ``status`` sent/delivered/read/failed,
    ``timestamp``, ``errors[].code``) and ``value.messages[]`` (inbound). They
    are signed like Messenger's, ``X-Hub-Signature-256`` under the app secret.

Only templates Meta has approved are ever sent: business-initiated WhatsApp
messages outside a customer's 24-hour window must be templates, and marketing
templates are billed by Meta to the seller's WhatsApp Business Account.
"""

from __future__ import annotations

from typing import Any

from app.integrations import http, meta
from app.integrations.http import ProviderError

#: Statuses Meta reports, in the order a message moves through them.
STATUS_RANK = {"sent": 1, "delivered": 2, "read": 3}


def _graph() -> str:
    return meta._graph()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def phone_number(phone_number_id: str, token: str) -> dict[str, Any]:
    reply = await http.send(
        "GET",
        f"{_graph()}/{phone_number_id}",
        params={"fields": "id,display_phone_number,verified_name"},
        headers=_auth(token),
    )
    data = meta._checked(reply) or {}
    if str(data.get("id")) != phone_number_id:
        raise ProviderError("ACCOUNT_MISMATCH")
    return data


async def subscribe(waba_id: str, token: str) -> None:
    reply = await http.send("POST", f"{_graph()}/{waba_id}/subscribed_apps", headers=_auth(token))
    if not (meta._checked(reply) or {}).get("success"):
        raise ProviderError("WEBHOOK_REGISTRATION_FAILED")


async def templates(waba_id: str, token: str) -> list[dict[str, str]]:
    """Every template on the account, following Meta's paging cursor (bounded)."""
    found: list[dict[str, str]] = []
    after: str | None = None
    for _ in range(10):
        params: dict[str, Any] = {"fields": "name,language,status,category", "limit": 100}
        if after:
            params["after"] = after
        reply = await http.send(
            "GET", f"{_graph()}/{waba_id}/message_templates", params=params, headers=_auth(token)
        )
        data = meta._checked(reply) or {}
        for row in data.get("data") or []:
            if isinstance(row, dict) and row.get("name") and row.get("language"):
                found.append(
                    {
                        "name": str(row["name"]),
                        "language": str(row["language"]),
                        "status": str(row.get("status") or ""),
                        "category": str(row.get("category") or ""),
                    }
                )
        after = ((data.get("paging") or {}).get("cursors") or {}).get("after")
        if not after or not (data.get("paging") or {}).get("next"):
            break
    return found


async def send_template(
    phone_number_id: str,
    token: str,
    *,
    to_e164: str,
    name: str,
    language: str,
    params: list[str],
) -> str:
    """Send one approved template. Returns the ``wamid``."""
    template: dict[str, Any] = {"name": name, "language": {"code": language}}
    if params:
        template["components"] = [
            {"type": "body", "parameters": [{"type": "text", "text": value} for value in params]}
        ]
    reply = await http.send(
        "POST",
        f"{_graph()}/{phone_number_id}/messages",
        headers=_auth(token),
        json_body={
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_e164.removeprefix("+"),
            "type": "template",
            "template": template,
        },
    )
    if 400 <= reply.status < 500 and reply.status != 429 and isinstance(reply.data, dict):
        code = (reply.data.get("error") or {}).get("code")
        if isinstance(code, int) and code not in {190, 10} and not 200 <= code < 300:
            # Meta's numeric error code is kept as a seller-safe reference; its
            # message text (which can echo input) is not.
            raise ProviderError(f"WA_{code}", status=reply.status)
    data = meta._checked(reply) or {}
    messages = data.get("messages") or []
    wamid = messages[0].get("id") if messages and isinstance(messages[0], dict) else None
    if not isinstance(wamid, str) or not wamid:
        raise ProviderError("MALFORMED_RESPONSE")
    return wamid
