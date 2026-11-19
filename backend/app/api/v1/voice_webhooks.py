"""Voice provider callbacks (V4 WP3.2).

``POST /v1/webhooks/voice/{provider}``: unauthenticated by necessity (a
provider callback carries no session), so the provider's signature is the only
thing that makes it safe. The body is stored before anything else, verified
by the deployment's :class:`~app.voice.transport.VoiceTransport` (which refuses
everything without a callback secret), and only then parsed and applied.

The reply carries no call state, no shop and no echo of the body: an
unauthenticated caller must not learn whether a call reference exists.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from app.confirmations.calls import CALLBACK_MAX_BYTES, receive_callback

router = APIRouter(prefix="/webhooks/voice", tags=["webhooks"])


class VoiceCallbackAck(BaseModel):
    received: bool
    processed: bool


@router.post(
    "/{provider}",
    response_model=VoiceCallbackAck,
    summary="Voice provider call callback",
    # The only provider today is the local/test mock; no contract exists yet
    # (VOICE_PROVIDER_CONTRACT_REQUIRED), so the shape is not published.
    include_in_schema=False,
)
async def voice_callback(provider: str, request: Request, response: Response) -> VoiceCallbackAck:
    body = await request.body()
    if len(body) > CALLBACK_MAX_BYTES:
        response.status_code = 413
        return VoiceCallbackAck(received=False, processed=False)
    headers = {key.lower(): value for key, value in request.headers.items()}
    result = await receive_callback(provider[:40], headers, body)
    response.status_code = result.status_code
    return VoiceCallbackAck(received=result.status_code != 404, processed=result.status_code == 200)


__all__ = ["router"]
