"""Capability-authenticated browser handoff; no seller/provider token in URLs."""

import uuid
from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import DbSession
from app.api.v1.integration_webhooks import _ip, _limit
from app.core.errors import ForbiddenError
from app.integrations import whatsapp_signup as signup

router = APIRouter(prefix="/integration-callbacks/whatsapp-signup", tags=["integrations"])


class BootstrapInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signup_state: str = Field(min_length=40, max_length=100)


class CompleteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    browser_session: str = Field(min_length=40, max_length=100)
    connection_id: uuid.UUID
    authorization_code: str | None = Field(default=None, min_length=1, max_length=4096)
    waba_id: str | None = Field(default=None, pattern=r"^[0-9]{1,40}$")
    phone_number_id: str | None = Field(default=None, pattern=r"^[0-9]{1,40}$")
    cancelled: bool = False


async def guard(request: Request, response: Response) -> None:
    # JSON-only, exact-origin POSTs plus an unguessable, one-time capability.
    # No cookie credentials: another site cannot cause an ambient-auth action.
    if request.headers.get("origin") != signup.origin():
        raise ForbiddenError("Open WhatsApp setup from ecomsbd")
    await _limit("whatsapp-signup", _ip(request), 30)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


@router.post("/bootstrap")
async def bootstrap(
    body: BootstrapInput, request: Request, response: Response, db: DbSession
) -> dict[str, Any]:
    await guard(request, response)
    return await signup.bootstrap(db, body.signup_state)


@router.post("/complete")
async def complete(
    body: CompleteInput, request: Request, response: Response, db: DbSession
) -> dict[str, Any]:
    await guard(request, response)
    return await signup.finish(db, **body.model_dump())
