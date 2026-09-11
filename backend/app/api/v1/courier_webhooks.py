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
from app.core.logging import get_logger
from app.couriers.webhooks import WEBHOOK_BLOCKER, build_receiver

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


__all__ = ["router"]
