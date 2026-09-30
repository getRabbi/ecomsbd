"""Revoking a seller's Sign in with Apple authorization.

App Store Review Guideline 5.1.1(v): an app offering Sign in with Apple must
revoke the user's Apple tokens when they delete their account. Apple's
supported flow is two calls, both authenticated by a short-lived ES256 client
secret signed with the team's Sign in with Apple key:

1. exchange the one-time authorization code the app just obtained from Apple
   (``/auth/token``) for the user's tokens, then
2. revoke the refresh token (``/auth/revoke``).

Nothing is stored. Supabase's native id-token sign-in keeps no Apple tokens,
so the app re-authorizes with Apple at deletion time and sends the fresh code;
it is spent here and never written anywhere.

Revocation is best effort by design. It must not decide whether an account can
be deleted: Apple being slow, or the code having expired, is reported as an
outcome and the deletion request stands.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum

import httpx
import jwt

from app.core.clock import utc_now
from app.core.config import Settings
from app.core.logging import bind, get_logger

__all__ = ["AppleRevocationOutcome", "apple_client_secret", "revoke_apple_authorization"]

log = get_logger(__name__)

APPLE_TOKEN_URL = "https://appleid.apple.com/auth/token"  # noqa: S105 - a URL, not a secret
APPLE_REVOKE_URL = "https://appleid.apple.com/auth/revoke"
#: Apple allows up to six months; each secret here lives for one request.
_CLIENT_SECRET_TTL = timedelta(minutes=5)


class AppleRevocationOutcome(StrEnum):
    REVOKED = "revoked"
    #: No Sign in with Apple key in this deployment.
    NOT_CONFIGURED = "not_configured"
    #: Apple refused the code or the revocation, or could not be reached.
    FAILED = "failed"


def apple_client_secret(settings: Settings, *, now: datetime | None = None) -> str | None:
    """The ES256 JWT Apple accepts as ``client_secret``, or None if unconfigured."""
    if not (
        settings.apple_team_id
        and settings.apple_key_id
        and settings.apple_client_id
        and settings.apple_private_key
    ):
        return None
    issued = now or utc_now()
    # Accept a PEM pasted into a single-line environment variable.
    key = settings.apple_private_key.get_secret_value().replace("\\n", "\n")
    return jwt.encode(
        {
            "iss": settings.apple_team_id,
            "iat": int(issued.timestamp()),
            "exp": int((issued + _CLIENT_SECRET_TTL).timestamp()),
            "aud": "https://appleid.apple.com",
            "sub": settings.apple_client_id,
        },
        key,
        algorithm="ES256",
        headers={"kid": settings.apple_key_id},
    )


async def revoke_apple_authorization(
    authorization_code: str,
    settings: Settings,
    *,
    client: httpx.AsyncClient | None = None,
) -> AppleRevocationOutcome:
    """Exchange ``authorization_code`` and revoke the resulting refresh token."""
    try:
        client_secret = apple_client_secret(settings)
    except (ValueError, TypeError, jwt.PyJWTError):
        # A malformed key is a deployment fault; never log the key itself.
        log.error("apple_revocation.invalid_key")
        return AppleRevocationOutcome.FAILED
    if client_secret is None:
        log.warning("apple_revocation.not_configured")
        return AppleRevocationOutcome.NOT_CONFIGURED

    credentials = {"client_id": settings.apple_client_id, "client_secret": client_secret}
    owned = client is None
    http = client or httpx.AsyncClient(timeout=10)
    try:
        exchanged = await http.post(
            APPLE_TOKEN_URL,
            data={
                **credentials,
                "code": authorization_code,
                "grant_type": "authorization_code",
            },
        )
        if exchanged.status_code != 200:
            # Apple's error body is a code such as ``invalid_grant``; keep only that.
            log.warning(
                "apple_revocation.exchange_refused",
                extra=bind(status=exchanged.status_code, error=_error_code(exchanged)),
            )
            return AppleRevocationOutcome.FAILED
        tokens = exchanged.json()
        if tokens.get("refresh_token"):
            token, hint = tokens["refresh_token"], "refresh_token"
        elif tokens.get("access_token"):
            token, hint = tokens["access_token"], "access_token"
        else:
            log.warning("apple_revocation.no_token")
            return AppleRevocationOutcome.FAILED
        revoked = await http.post(
            APPLE_REVOKE_URL,
            data={**credentials, "token": token, "token_type_hint": hint},
        )
        if revoked.status_code != 200:
            log.warning(
                "apple_revocation.revoke_refused",
                extra=bind(status=revoked.status_code, error=_error_code(revoked)),
            )
            return AppleRevocationOutcome.FAILED
        return AppleRevocationOutcome.REVOKED
    except (httpx.HTTPError, ValueError):
        log.warning("apple_revocation.unreachable")
        return AppleRevocationOutcome.FAILED
    finally:
        if owned:
            await http.aclose()


def _error_code(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    return error if isinstance(error, str) else None
