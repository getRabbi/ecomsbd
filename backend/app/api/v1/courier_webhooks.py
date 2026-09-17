"""Courier webhook receiver.

Brief section 18. ``STEADFAST_WEBHOOK_CONTRACT_REQUIRED``.

The route exists, is wired, is tested and stores every body it receives. What it
will not do is pretend to have verified one. The supplied Steadfast V1
documentation describes no webhook at all — no signature header, no algorithm,
no payload — so there is nothing to verify against, and an endpoint that
accepted requests anyway would let anyone mark any parcel delivered and move a
seller's money.

Unauthenticated by design: a provider callback arrives with no ecomsbd session.
That is precisely why signature verification is the only thing that could make
it safe, and why, without one, nothing is processed.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from app.api.deps import DbSession
from app.core.context import use_context
from app.core.logging import get_logger
from app.couriers.accounts import resolve_account_by_webhook_token
from app.couriers.pathao.webhooks import (
    WEBHOOK_INTEGRATION_SECRET_HEADER,
    WEBHOOK_INTEGRATION_SECRET_VALUE,
    PathaoWebhookParser,
    PathaoWebhookVerifier,
    is_integration_handshake,
)
from app.couriers.webhooks import WEBHOOK_BLOCKER, WebhookReceiver, build_receiver

router = APIRouter(prefix="/webhooks/couriers", tags=["webhooks"])

log = get_logger(__name__)


class WebhookAck(BaseModel):
    """What the provider gets back.

    Carries no parcel state, no shop identifier and no echo of the body: a
    webhook acknowledgement is a place where an unauthenticated caller could
    otherwise learn whether a reference exists.
    """

    received: bool
    processed: bool
    #: Present only when the delivery could not be acted on, so an operator
    #: reading provider-side logs sees the same identifier the manifest and the
    #: release checklist use.
    blocker: str | None = None
    detail: str


@router.post(
    "/steadfast",
    response_model=WebhookAck,
    summary="Steadfast delivery callback (receiver built, provider activation disabled)",
    # Excluded from the public schema: publishing an endpoint whose contract
    # does not exist yet would invite a client to call it.
    include_in_schema=False,
)
async def steadfast_webhook(
    request: Request,
    response: Response,
    db: DbSession,
) -> WebhookAck:
    """Receive and store a Steadfast callback without acting on it.

    Answers 202 rather than an error: the body has been stored, there is
    nothing the sender could do differently, and a 4xx would invite a retry
    loop over a state that will not change until a contract is supplied.
    """
    body = await request.body()
    receiver = build_receiver(db)
    result = await receiver.ingest(
        headers={key.lower(): value for key, value in request.headers.items()},
        body=body,
    )

    response.status_code = result.http_status
    return WebhookAck(
        received=True,
        processed=result.was_processed,
        blocker=None if result.was_processed else WEBHOOK_BLOCKER,
        detail=result.message,
    )


@router.post(
    "/pathao/{token}",
    response_model=WebhookAck,
    summary="Pathao delivery callback",
    # Excluded from the public schema: the path carries a per-shop routing
    # token, and publishing the shape invites probing for valid ones.
    include_in_schema=False,
)
async def pathao_webhook(
    token: str,
    request: Request,
    response: Response,
    db: DbSession,
) -> WebhookAck:
    """Receive a Pathao callback for one shop.

    Pathao publishes a real webhook contract, so unlike the Steadfast route this
    one can verify and accept. The order below is the security design, and each
    step exists because the step after it would otherwise be unsafe:

    1.  **Resolve the shop from the path token, across tenants.** A callback has
        no session. Reading the shop out of the body instead would mean parsing
        an unverified payload to decide how to verify it.
    2.  **Become that tenant**, so everything stored lands in the right shop.
    3.  **Verify the signature against that shop's own secret** — a constant-time
        comparison, because Pathao's scheme is a shared secret rather than an
        HMAC.
    4.  Only then is the body parsed.

    Every reply carries Pathao's required integration header, including the
    replies that reject: Pathao treats a response without it as a failed
    integration, and a shop whose secret is wrong should see a signature error
    rather than a silently broken webhook.
    """
    response.headers[WEBHOOK_INTEGRATION_SECRET_HEADER] = WEBHOOK_INTEGRATION_SECRET_VALUE

    body = await request.body()
    headers = {key.lower(): value for key, value in request.headers.items()}

    account = await resolve_account_by_webhook_token(db, provider="pathao", token=token)
    if account is None:
        # Deliberately the same answer an unconfigured account gets. A callback
        # URL must not become an oracle for which shops exist.
        response.status_code = 202
        log.warning(
            "pathao webhook for unknown token",
            extra={"provider": "pathao", "operation": "webhook_ingest"},
        )
        return WebhookAck(
            received=True,
            processed=False,
            blocker=None,
            detail="Received. This callback URL is not active.",
        )

    # Pathao's handshake carries no parcel and is sent before a shop has
    # necessarily finished configuring. Acknowledged with the integration
    # header — which is the whole point of the handshake — and stored by
    # nobody, because there is nothing in it to store.
    if is_integration_handshake(body):
        response.status_code = 202
        return WebhookAck(
            received=True,
            processed=True,
            blocker=None,
            detail="Webhook integration confirmed.",
        )

    from app.api.deps import get_vault
    from app.couriers.accounts import CourierAccountService

    with use_context(tenant_id=account.tenant_id):
        service = CourierAccountService(db, vault=get_vault())
        secret = service.webhook_secret_for(account)

        receiver = WebhookReceiver(
            db,
            verifier=PathaoWebhookVerifier(secret),
            parser=PathaoWebhookParser(),
            provider="pathao",
            blocker="PATHAO_WEBHOOK_SECRET_NOT_CONFIGURED",
            unconfigured_detail=(
                "Add the webhook secret from your Pathao merchant panel to this "
                "courier account. Pathao has no status lookup, so parcel updates "
                "arrive only by webhook."
            ),
        )
        result = await receiver.ingest(headers=headers, body=body)

    response.status_code = result.http_status
    return WebhookAck(
        received=True,
        processed=result.was_processed,
        blocker=None if secret else "PATHAO_WEBHOOK_SECRET_NOT_CONFIGURED",
        detail=result.message,
    )


__all__ = ["router"]
