"""Cryptographic primitives.

Covers four separate concerns that must not share a secret:

*   **Credential vault** (master spec section 47) — AES-256-GCM with a versioned
    key id, used for courier and billing credentials. Ciphertext is bound to the
    row it belongs to via GCM additional authenticated data, so a ciphertext
    copied into a different row fails to decrypt instead of silently working.
*   **Phone search hash** (section 133) — keyed HMAC of the canonical E.164
    number under its *own* secret. Explicitly not a plain unsalted SHA-256: the
    Bangladeshi mobile keyspace is small enough to enumerate offline.
*   **OTP hashing** (section 4) — HMAC under a server-side pepper, bound to the
    challenge id so a code captured for one challenge cannot verify another.
*   **Session tokens** (section 47) — opaque high-entropy refresh tokens stored
    only as a keyed hash, plus short-lived signed access tokens.

Every comparison uses :func:`hmac.compare_digest`.
"""

from __future__ import annotations

import base64
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from typing import Any

import jwt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.clock import utc_now
from app.core.config import Settings
from app.core.errors import AppError, ErrorCode

__all__ = [
    "AccessTokenClaims",
    "CredentialVault",
    "SecretHasher",
    "TokenService",
    "generate_numeric_code",
    "generate_opaque_token",
]

_ENVELOPE_SEPARATOR = "."
_NONCE_BYTES = 12  # GCM standard nonce length


def generate_numeric_code(length: int) -> str:
    """Cryptographically random decimal OTP, zero-padded, never predictable."""
    if not 4 <= length <= 10:
        raise ValueError("OTP length must be between 4 and 10 digits")
    upper = 10**length
    return str(secrets.randbelow(upper)).zfill(length)


def generate_opaque_token(byte_length: int = 32) -> str:
    """URL-safe random token. Used for refresh tokens and webhook path tokens."""
    return secrets.token_urlsafe(byte_length)


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


class CredentialVault:
    """AES-256-GCM encryption for provider credentials at rest.

    The stored envelope is ``<key_version>.<nonce>.<ciphertext>``. Keeping the
    key version inside the envelope is what makes decrypt-old / encrypt-new key
    rotation possible without a flag day (master spec section 132).
    """

    def __init__(self, settings: Settings) -> None:
        key = base64.b64decode(settings.credential_encryption_key.get_secret_value())
        self._aead = AESGCM(key)
        self._key_version = settings.credential_key_version
        # Historical keys for rotation: {version: AESGCM}. Populated from a
        # secret manager once rotation is exercised; empty is the normal case.
        self._previous: dict[str, AESGCM] = {}

    @property
    def key_version(self) -> str:
        return self._key_version

    def register_previous_key(self, version: str, key_b64: str) -> None:
        """Allow decryption of values written under a superseded key."""
        self._previous[version] = AESGCM(base64.b64decode(key_b64))

    def encrypt(self, plaintext: str, *, context: str) -> str:
        """Encrypt, binding the result to ``context`` (for example ``courier_account:<id>``)."""
        nonce = secrets.token_bytes(_NONCE_BYTES)
        ciphertext = self._aead.encrypt(nonce, plaintext.encode(), context.encode())
        return _ENVELOPE_SEPARATOR.join(
            (self._key_version, _b64encode(nonce), _b64encode(ciphertext))
        )

    def decrypt(self, envelope: str, *, context: str) -> str:
        """Decrypt an envelope. Raises if the context or key does not match."""
        try:
            version, nonce_b64, ciphertext_b64 = envelope.split(_ENVELOPE_SEPARATOR)
        except ValueError as exc:
            raise AppError("Malformed credential envelope", code=ErrorCode.INTERNAL_ERROR) from exc

        aead = self._aead if version == self._key_version else self._previous.get(version)
        if aead is None:
            raise AppError(
                f"No decryption key available for key version {version!r}",
                code=ErrorCode.INTERNAL_ERROR,
            )
        plaintext = aead.decrypt(
            _b64decode(nonce_b64), _b64decode(ciphertext_b64), context.encode()
        )
        return plaintext.decode()


class SecretHasher:
    """Keyed one-way hashes.

    Separate secrets per purpose: a leak of the phone-search key must not also
    let an attacker verify OTPs or forge session-token lookups.
    """

    def __init__(self, settings: Settings) -> None:
        self._phone_key = settings.phone_search_hmac_key.get_secret_value().encode()
        self._otp_key = settings.otp_hash_secret.get_secret_value().encode()
        # Session tokens are stored hashed; derived from the JWT key so a single
        # rotation invalidates both signed and opaque credentials together.
        self._token_key = sha256(
            b"ecomsbd.session.v1|" + settings.jwt_signing_key.get_secret_value().encode()
        ).digest()

    @staticmethod
    def _hmac_hex(key: bytes, *parts: str) -> str:
        message = "|".join(parts).encode()
        return hmac.new(key, message, sha256).hexdigest()

    def phone_search_hash(self, canonical_e164: str) -> str:
        """Deterministic lookup key for an exact phone match (section 133)."""
        return self._hmac_hex(self._phone_key, "phone.v1", canonical_e164)

    def otp_hash(self, code: str, *, challenge_id: uuid.UUID) -> str:
        """Bind the code hash to its challenge so it cannot be replayed elsewhere."""
        return self._hmac_hex(self._otp_key, "otp.v1", str(challenge_id), code)

    def verify_otp(self, code: str, *, challenge_id: uuid.UUID, expected_hash: str) -> bool:
        return hmac.compare_digest(self.otp_hash(code, challenge_id=challenge_id), expected_hash)

    def token_hash(self, token: str) -> str:
        """Hash for refresh tokens. The plaintext is never stored."""
        return self._hmac_hex(self._token_key, "token.v1", token)

    def ip_hash(self, ip: str | None) -> str | None:
        """Keyed hash of a client address.

        Rate limiting and abuse investigation need to recognise a repeat caller;
        neither needs the address itself, so the raw value is never persisted.
        """
        if not ip:
            return None
        return self._hmac_hex(self._token_key, "ip.v1", ip)

    def verify_token(self, token: str, expected_hash: str) -> bool:
        return hmac.compare_digest(self.token_hash(token), expected_hash)


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    """Decoded access-token payload."""

    user_id: uuid.UUID
    session_id: uuid.UUID
    tenant_id: uuid.UUID | None
    role: str | None
    token_id: str

    @property
    def has_tenant(self) -> bool:
        return self.tenant_id is not None


class TokenService:
    """Issues and verifies short-lived signed access tokens.

    Refresh tokens are deliberately *not* JWTs: they are opaque random strings
    checked against a database row, which is what makes server-side revocation
    and rotation-reuse detection possible.
    """

    def __init__(self, settings: Settings) -> None:
        self._key = settings.jwt_signing_key.get_secret_value()
        self._algorithm = settings.jwt_algorithm
        self._issuer = settings.jwt_issuer
        self._key_version = settings.jwt_key_version
        self._ttl = timedelta(seconds=settings.access_token_ttl_seconds)

    @property
    def access_token_ttl_seconds(self) -> int:
        return int(self._ttl.total_seconds())

    def issue_access_token(
        self,
        *,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        tenant_id: uuid.UUID | None,
        role: str | None,
    ) -> str:
        now = utc_now()
        payload: dict[str, Any] = {
            "iss": self._issuer,
            "sub": str(user_id),
            "sid": str(session_id),
            "tid": str(tenant_id) if tenant_id else None,
            "role": role,
            "iat": int(now.timestamp()),
            "exp": int((now + self._ttl).timestamp()),
            "jti": secrets.token_urlsafe(12),
        }
        return jwt.encode(
            payload, self._key, algorithm=self._algorithm, headers={"kid": self._key_version}
        )

    def decode_access_token(self, token: str) -> AccessTokenClaims:
        try:
            payload = jwt.decode(
                token,
                self._key,
                algorithms=[self._algorithm],
                issuer=self._issuer,
                options={"require": ["exp", "iat", "sub", "sid", "iss"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AppError("Access token expired", code=ErrorCode.TOKEN_EXPIRED) from exc
        except jwt.InvalidTokenError as exc:
            raise AppError("Access token is not valid", code=ErrorCode.INVALID_TOKEN) from exc

        try:
            tenant_raw = payload.get("tid")
            return AccessTokenClaims(
                user_id=uuid.UUID(payload["sub"]),
                session_id=uuid.UUID(payload["sid"]),
                tenant_id=uuid.UUID(tenant_raw) if tenant_raw else None,
                role=payload.get("role"),
                token_id=payload.get("jti", ""),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise AppError(
                "Access token claims are malformed", code=ErrorCode.INVALID_TOKEN
            ) from exc
