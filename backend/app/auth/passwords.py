"""Password hashing and policy.

Argon2id, through ``cryptography`` — which is already a dependency, so this
adds no new one. Argon2id is the current recommendation because it is *memory*
hard: an attacker with a GPU farm gains far less against it than against a
purely iterative KDF, and the memory cost is the parameter that buys that.

The digest is stored in PHC string format::

    $argon2id$v=19$m=65536,t=2,p=4$<b64 salt>$<b64 hash>

Parameters live inside the string rather than in configuration, so raising the
cost later does not invalidate existing hashes: an old digest still verifies
against its own parameters, and :func:`PasswordHasher.needs_rehash` says when
to write a stronger one on the next successful sign-in.

The policy deliberately has three rules and no more. Composition requirements —
one upper, one digit, one symbol — push people toward `Password1!`, which is in
every wordlist; length is what actually costs an attacker something.
"""

from __future__ import annotations

import base64
import hmac
import os
import unicodedata
from dataclasses import dataclass

from cryptography.exceptions import InvalidKey, UnsupportedAlgorithm
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

from app.core.errors import ValidationError

__all__ = [
    "MAX_PASSWORD_LENGTH",
    "MIN_PASSWORD_LENGTH",
    "PasswordHasher",
    "validate_password",
]

#: OWASP's floor for a user-chosen password with no composition rules.
MIN_PASSWORD_LENGTH = 10
#: A cap exists because the KDF cost is paid on the server: an unbounded
#: password is an unauthenticated way to spend our CPU.
MAX_PASSWORD_LENGTH = 200

#: Argon2id cost. ~64 MiB and two passes lands around 150-250 ms on a small
#: VPS, which is slow enough to matter to an attacker and fast enough that a
#: seller does not notice it on the sign-in screen.
_MEMORY_COST_KIB = 64 * 1024
_ITERATIONS = 2
_LANES = 4
_SALT_BYTES = 16
_HASH_BYTES = 32

#: Obvious choices that pass a length check. Deliberately short: a real
#: breached-password check needs a corpus and a service, and pretending a
#: hard-coded list is one would be worse than being honest that it is a
#: courtesy rather than a control.
_OBVIOUS_PASSWORDS = frozenset(
    {
        "password",
        "password1",
        "password123",
        "12345678",
        "123456789",
        "1234567890",
        "qwertyuiop",
        "letmein123",
        "iloveyou123",
        "ecomsbd123",
    }
)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"))


def normalize_password(raw: str) -> str:
    """NFKC, so a password typed on two keyboards is the same password.

    Not trimmed: a leading or trailing space is a character the person chose,
    and silently dropping it would make a password that works once fail later
    from a client that does not trim.
    """
    return unicodedata.normalize("NFKC", raw)


def validate_password(raw: str) -> str:
    """Check a proposed password, or refuse it with a sentence a person can act on."""
    password = normalize_password(raw)
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationError(
            f"Use at least {MIN_PASSWORD_LENGTH} characters.",
            message_bn=f"কমপক্ষে {MIN_PASSWORD_LENGTH}টি অক্ষর ব্যবহার করুন।",
            details={"field": "password", "min_length": MIN_PASSWORD_LENGTH},
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValidationError(
            f"Use at most {MAX_PASSWORD_LENGTH} characters.",
            details={"field": "password", "max_length": MAX_PASSWORD_LENGTH},
        )
    if password.lower() in _OBVIOUS_PASSWORDS:
        raise ValidationError(
            "That password is too easy to guess. Choose another one.",
            message_bn="এই পাসওয়ার্ডটি সহজেই অনুমান করা যায়। অন্য একটি বেছে নিন।",
            details={"field": "password"},
        )
    return password


@dataclass(frozen=True, slots=True)
class _Params:
    memory_cost: int
    iterations: int
    lanes: int


class PasswordHasher:
    """Argon2id hashing with PHC-encoded, self-describing digests."""

    scheme = "argon2id"

    def __init__(
        self,
        *,
        memory_cost: int = _MEMORY_COST_KIB,
        iterations: int = _ITERATIONS,
        lanes: int = _LANES,
    ) -> None:
        self._params = _Params(memory_cost=memory_cost, iterations=iterations, lanes=lanes)

    def hash(self, password: str) -> str:
        salt = os.urandom(_SALT_BYTES)
        digest = self._derive(normalize_password(password), salt, self._params)
        p = self._params
        return (
            f"${self.scheme}$v=19$m={p.memory_cost},t={p.iterations},p={p.lanes}"
            f"${_b64(salt)}${_b64(digest)}"
        )

    def verify(self, password: str, encoded: str | None) -> bool:
        """Constant-time check. A missing or malformed digest is simply false.

        It never raises for a bad stored value: an identity with no password —
        a Google-only account, say — must fail a password login the same way a
        wrong password does, in the same time, telling the caller nothing about
        which case it was.
        """
        if not encoded:
            return False
        try:
            params, salt, expected = self._decode(encoded)
            actual = self._derive(normalize_password(password), salt, params)
        except Exception:
            return False
        return hmac.compare_digest(actual, expected)

    def needs_rehash(self, encoded: str | None) -> bool:
        """Whether a verified digest should be rewritten at stronger settings."""
        if not encoded:
            return False
        try:
            params, _salt, _expected = self._decode(encoded)
        except Exception:
            return True
        return (
            params.memory_cost < self._params.memory_cost
            or params.iterations < self._params.iterations
            or params.lanes != self._params.lanes
        )

    # ------------------------------------------------------------ internals --

    def _derive(self, password: str, salt: bytes, params: _Params) -> bytes:
        try:
            kdf = Argon2id(
                salt=salt,
                length=_HASH_BYTES,
                iterations=params.iterations,
                lanes=params.lanes,
                memory_cost=params.memory_cost,
            )
            return kdf.derive(password.encode("utf-8"))
        except (UnsupportedAlgorithm, InvalidKey) as exc:  # pragma: no cover - build-dependent
            # Argon2id needs OpenSSL 3.2 or newer. Falling back to a weaker KDF
            # here would silently downgrade every password on a host nobody
            # checked, so this refuses instead and names the cause.
            raise RuntimeError(
                "Argon2id is unavailable in this build of cryptography/OpenSSL; "
                "password authentication cannot run safely without it"
            ) from exc

    def _decode(self, encoded: str) -> tuple[_Params, bytes, bytes]:
        parts = encoded.split("$")
        if len(parts) != 6 or parts[0] != "" or parts[1] != self.scheme:
            raise ValueError("not an argon2id PHC string")
        raw_params = dict(item.split("=", 1) for item in parts[3].split(","))
        params = _Params(
            memory_cost=int(raw_params["m"]),
            iterations=int(raw_params["t"]),
            lanes=int(raw_params["p"]),
        )
        return params, _unb64(parts[4]), _unb64(parts[5])
