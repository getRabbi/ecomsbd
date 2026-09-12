"""Google and Apple identity-token verification.

The rule this module exists to enforce: **the client's claim that a provider
approved it is not evidence.** A mobile app posting `{"google": "ok"}` — or
posting somebody else's token, or a token minted for a different application —
must not produce an ecomsbd session. So the token is checked here, against the
provider's own published keys, before any identity is touched:

* the signature, against the provider's current JWKS;
* ``iss``, against the provider's issuer;
* ``aud``, against *our* configured client ids — this is the check that stops a
  token issued for an unrelated app from working here, and it is the one most
  often left out;
* ``exp`` and ``iat``, with a small clock skew allowance;
* ``sub``, which must be present, because it is the identity.

Nothing here reads a database or issues a session. It answers one question —
"whose token is this?" — and the caller decides what that means.

Apple has two behaviours worth knowing about, both handled below:

* the identity token carries an email only when the person **first** authorizes
  the app. Later sign-ins carry ``sub`` alone. Requiring an email would break
  every returning seller, so the email is optional and ``sub`` is authoritative.
* ``email`` may be a private relay address (``…@privaterelay.appleid.com``). It
  is a real, deliverable address and is stored as-is; it is flagged so the
  caller can decide not to treat it as a linking candidate.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import jwt

from app.auth.identities import AuthProvider, normalize_email
from app.core.errors import AuthenticationError, ErrorCode

__all__ = [
    "APPLE_ISSUER",
    "APPLE_JWKS_URI",
    "APPLE_PRIVATE_RELAY_DOMAIN",
    "GOOGLE_ISSUERS",
    "GOOGLE_JWKS_URI",
    "IdTokenVerifier",
    "JwksCache",
    "OidcIdentity",
    "OidcTokenVerifier",
    "build_apple_verifier",
    "build_google_verifier",
]

GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
GOOGLE_JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"

APPLE_ISSUER = "https://appleid.apple.com"
APPLE_JWKS_URI = "https://appleid.apple.com/auth/keys"
APPLE_PRIVATE_RELAY_DOMAIN = "privaterelay.appleid.com"

#: Tolerance for clock drift between us and the provider. Small on purpose: it
#: is a grace period on `exp`, so every second here is a second a stolen token
#: outlives its own expiry.
CLOCK_SKEW_SECONDS = 30

#: How long a fetched key set is trusted. Providers rotate keys on the order of
#: days; re-fetching per sign-in would put an outbound request on the login path
#: and make the provider's availability our availability.
JWKS_TTL_SECONDS = 3600


@dataclass(frozen=True, slots=True)
class OidcIdentity:
    """What a verified identity token says. Nothing here is client-supplied."""

    provider: AuthProvider
    subject: str
    email: str | None
    email_verified: bool
    #: Present on the first Apple authorization and on most Google tokens.
    display_name: str | None = None
    #: Apple's relay address. Deliverable, but it identifies the Apple account
    #: rather than a mailbox the person uses elsewhere, so it must not be
    #: treated as proof that they control some other account with that name.
    is_private_relay: bool = False

    @property
    def linkable_email(self) -> str | None:
        """The address this identity may be linked on, if any."""
        if not self.email or not self.email_verified or self.is_private_relay:
            return None
        return self.email


class IdTokenVerifier(Protocol):
    """Verifies one provider's identity tokens."""

    provider: AuthProvider

    @property
    def is_configured(self) -> bool: ...

    async def verify(self, id_token: str) -> OidcIdentity: ...


class JwksCache:
    """Fetches and caches a provider's JWKS.

    Keyed by URI so Google and Apple do not share an entry. Expired key sets
    cannot be used after a refresh failure: a removed key may be compromised.
    """

    def __init__(self, *, ttl_seconds: int = JWKS_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._last_attempt: dict[str, float] = {}

    async def get(self, uri: str, *, force: bool = False) -> dict[str, Any]:
        cached = self._cache.get(uri)
        now = time.monotonic()
        if cached is not None and not force and now - cached[0] < self._ttl:
            return cached[1]

        # Unknown kids must not turn each junk token into an outbound request.
        if cached is not None and now - self._last_attempt.get(uri, -float("inf")) < 60:
            if now - cached[0] < self._ttl:
                return cached[1]
            raise AuthenticationError(
                "Sign-in provider keys are temporarily unavailable.",
                code=ErrorCode.SERVICE_UNAVAILABLE,
                http_status=503,
            )
        self._last_attempt[uri] = now

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                response = await client.get(uri)
                response.raise_for_status()
                data: dict[str, Any] = response.json()
                if not isinstance(data, dict) or not isinstance(data.get("keys"), list):
                    raise ValueError("invalid JWKS response")
        except Exception as exc:
            raise AuthenticationError(
                "Could not reach the sign-in provider. Try again.",
                code=ErrorCode.SERVICE_UNAVAILABLE,
                http_status=503,
            ) from exc

        self._cache[uri] = (now, data)
        return data

    def seed(self, uri: str, jwks: dict[str, Any]) -> None:
        """Install a key set directly. Tests use this instead of the network."""
        self._cache[uri] = (time.monotonic(), jwks)


class OidcTokenVerifier:
    """Signature, issuer, audience and expiry checks for one provider."""

    def __init__(
        self,
        *,
        provider: AuthProvider,
        issuers: tuple[str, ...],
        jwks_uri: str,
        audiences: tuple[str, ...],
        jwks: JwksCache | None = None,
        algorithms: tuple[str, ...] = ("RS256",),
    ) -> None:
        self.provider = provider
        self._issuers = issuers
        self._jwks_uri = jwks_uri
        self._audiences = tuple(a for a in audiences if a)
        self._jwks = jwks or JwksCache()
        self._algorithms = list(algorithms)

    @property
    def is_configured(self) -> bool:
        """No audience means nothing to check `aud` against.

        Reported rather than assumed: a verifier with an empty audience set
        would accept a token minted for any application on earth, so it refuses
        to run at all instead.
        """
        return bool(self._audiences)

    @property
    def jwks_cache(self) -> JwksCache:
        return self._jwks

    async def verify(self, id_token: str) -> OidcIdentity:
        if not self.is_configured:
            raise AuthenticationError(
                f"{self.provider.title()} sign-in is not configured on this server.",
                code=ErrorCode.FEATURE_DISABLED,
                http_status=503,
            )
        if not id_token or not isinstance(id_token, str):
            raise self._refuse("no identity token was supplied")

        key = await self._resolve_key(id_token)
        claims = self._decode(id_token, key)
        return self._to_identity(claims)

    # ------------------------------------------------------------ internals --

    async def _resolve_key(self, id_token: str) -> Any:
        try:
            kid = jwt.get_unverified_header(id_token).get("kid")
        except jwt.PyJWTError as exc:
            raise self._refuse("the identity token is malformed") from exc
        if not kid:
            raise self._refuse("the identity token names no signing key")

        key = self._find(await self._jwks.get(self._jwks_uri), kid)
        if key is None:
            # A rotation the cache has not seen yet. One forced refresh, then
            # give up — retrying on every unknown kid would turn a stream of
            # junk tokens into a stream of outbound requests.
            key = self._find(await self._jwks.get(self._jwks_uri, force=True), kid)
        if key is None:
            raise self._refuse("the identity token was signed by an unknown key")
        return key

    @staticmethod
    def _find(jwks: dict[str, Any], kid: str) -> Any:
        for entry in jwks.get("keys", []):
            if entry.get("kid") == kid:
                return jwt.PyJWK(entry).key
        return None

    def _decode(self, id_token: str, key: Any) -> dict[str, Any]:
        last_error: Exception | None = None
        # Try Google's two documented issuer spellings with full verification.
        for issuer in self._issuers:
            try:
                claims: dict[str, Any] = jwt.decode(
                    id_token,
                    key=key,
                    algorithms=self._algorithms,
                    audience=list(self._audiences),
                    issuer=issuer,
                    leeway=CLOCK_SKEW_SECONDS,
                    options={"require": ["exp", "iat", "sub", "aud", "iss"]},
                )
                return claims
            except jwt.InvalidIssuerError as exc:
                last_error = exc
                continue
            except jwt.ExpiredSignatureError as exc:
                raise self._refuse("the identity token has expired") from exc
            except jwt.InvalidAudienceError as exc:
                raise self._refuse(
                    "the identity token was issued for a different application"
                ) from exc
            except jwt.PyJWTError as exc:
                raise self._refuse("the identity token could not be verified") from exc
        raise self._refuse("the identity token came from an unexpected issuer") from last_error

    def _to_identity(self, claims: dict[str, Any]) -> OidcIdentity:
        subject = str(claims.get("sub") or "").strip()
        if not subject:
            raise self._refuse("the identity token carries no subject")

        raw_email = claims.get("email")
        email = normalize_email(str(raw_email)) if raw_email else None
        verified = _claim_is_true(claims.get("email_verified"))
        if self.provider is AuthProvider.GOOGLE:
            # Google warns that email_verified alone does not prove current
            # ownership of third-party mailboxes. Only Gmail or Workspace may
            # supply email proof for automatic linking. Sign-in still uses sub.
            verified = verified and bool(
                email and (email.endswith("@gmail.com") or claims.get("hd"))
            )
        relay = bool(email and email.endswith(f"@{APPLE_PRIVATE_RELAY_DOMAIN}"))

        if self.provider is AuthProvider.APPLE and email and not claims.get("email_verified"):
            # Apple omits `email_verified` on some tokens while still only ever
            # returning an address it controls. A relay address is verified by
            # construction; a real one is trusted only when Apple says so.
            verified = relay

        return OidcIdentity(
            provider=self.provider,
            subject=subject,
            email=email,
            email_verified=verified,
            display_name=_display_name(claims),
            is_private_relay=relay,
        )

    def _refuse(self, reason: str) -> AuthenticationError:
        """One shape for every failure.

        The reason is specific enough to debug and says nothing about whether
        an account exists: every path through here happens before any lookup.
        """
        return AuthenticationError(
            f"Could not verify this {self.provider.title()} sign-in: {reason}.",
            code=ErrorCode.INVALID_TOKEN,
            message_bn="সাইন-ইন যাচাই করা যায়নি। আবার চেষ্টা করুন।",
        )


def _claim_is_true(value: Any) -> bool:
    """Providers send booleans as booleans, and sometimes as ``"true"``."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


def _display_name(claims: dict[str, Any]) -> str | None:
    name = claims.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()[:160]
    given, family = claims.get("given_name"), claims.get("family_name")
    joined = " ".join(part for part in (given, family) if isinstance(part, str) and part.strip())
    return joined.strip()[:160] or None


def build_google_verifier(settings: Any, *, jwks: JwksCache | None = None) -> OidcTokenVerifier:
    return OidcTokenVerifier(
        provider=AuthProvider.GOOGLE,
        issuers=GOOGLE_ISSUERS,
        jwks_uri=GOOGLE_JWKS_URI,
        audiences=settings.google_client_ids,
        jwks=jwks,
    )


def build_apple_verifier(settings: Any, *, jwks: JwksCache | None = None) -> OidcTokenVerifier:
    audiences = tuple(value for value in (settings.apple_client_id,) if value)
    return OidcTokenVerifier(
        provider=AuthProvider.APPLE,
        issuers=(APPLE_ISSUER,),
        jwks_uri=APPLE_JWKS_URI,
        audiences=audiences,
        jwks=jwks,
    )
