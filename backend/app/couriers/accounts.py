"""Connecting, validating and disconnecting a courier account.

Brief sections 3, 4, 5 and 49; master spec sections 47, 103, 134.

Four rules hold this module together, and each one exists because of a specific
way integrations like this normally go wrong:

*   **Plaintext credentials live for the length of one call.** They arrive in a
    request body, go into the vault, and come back out only inside
    :meth:`CourierAccountService.credentials_for`. Nothing here can hand one to
    a client, because nothing here returns one.

*   **Validation uses the safest documented read.** For Steadfast that is
    ``GET /get_balance``. Creating a parcel to check an API key leaves a real
    parcel, a real delivery attempt and a real charge behind.

*   **A transient failure never invalidates credentials.** The four-valued
    :class:`~app.couriers.models.CredentialValidation` exists so that "we could
    not find out" is a first-class answer. Only a *deterministic* rejection
    counts against an account, and only after several in a row, because a
    single 401 during a provider deployment must not log a shop out of its
    courier.

*   **Every credential action is audited, and no audit row holds a secret.**
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from functools import partial
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import AuditAction, record_audit
from app.common.provider_health import ProviderHealthService, ProviderKind
from app.core.clock import utc_now
from app.core.context import current_context
from app.core.errors import ConflictError, ErrorCode, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.security import CredentialVault
from app.couriers.adapter import CourierAdapter, ValidationResult
from app.couriers.capabilities import Capability, load_all_manifests, load_manifest
from app.couriers.credentials import build_credentials, spec_for
from app.couriers.metrics import CourierMetric, record_metric
from app.couriers.models import (
    AUTH_FAILURES_BEFORE_RECONNECT,
    CourierAccount,
    CourierAccountStatus,
    CredentialValidation,
)
from app.couriers.registry import CourierAdapterRegistry, get_courier_registry
from app.db.tenancy import allow_cross_tenant

__all__ = [
    "BookableCourier",
    "BookableReason",
    "ConnectRequest",
    "CourierAccountService",
    "ValidationOutcome",
    "resolve_account_by_webhook_token",
]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ConnectRequest:
    """Credentials as a seller typed them.

    Deliberately a plain object with no persistence: it exists for the length of
    the connect call and is never stored, logged or echoed.

    ``api_key`` and ``secret_key`` are **storage slots**, not Steadfast field
    names. Each provider's declaration in :mod:`app.couriers.credentials` says
    which of its own fields fills which slot — Steadfast puts its API key and
    secret key there, Pathao its Client ID and Client Secret. Keeping the slots
    positional is what lets one encryption path, one masking rule and one audit
    shape serve every provider.
    """

    provider: str
    api_key: str
    secret_key: str
    label: str | None = None
    #: Non-secret provider settings — sandbox mode, chosen pickup store. Shown
    #: back to the seller, so nothing secret may be put here.
    config: dict[str, Any] = field(default_factory=dict)
    #: The provider's webhook signing secret, when it has one. Encrypted at
    #: rest in its own slot and never returned.
    webhook_secret: str | None = None

    def __repr__(self) -> str:
        return f"ConnectRequest(provider={self.provider!r}, api_key='[redacted]', ...)"

    __str__ = __repr__

    def cleaned(self) -> ConnectRequest:
        """Trim whitespace.

        A key pasted from an email arrives with a trailing newline more often
        than not, and "your key is wrong" for an invisible character is a
        support ticket nobody can diagnose from a screenshot.
        """
        return ConnectRequest(
            provider=self.provider.strip().lower(),
            api_key=self.api_key.strip(),
            secret_key=self.secret_key.strip(),
            label=(self.label or "").strip() or None,
            config=dict(self.config or {}),
            webhook_secret=(self.webhook_secret or "").strip() or None,
        )


@dataclass(frozen=True, slots=True)
class ValidationOutcome:
    """What checking an account's credentials concluded."""

    result: CredentialValidation
    message: str
    capabilities: frozenset[Capability] = frozenset()
    masked_identifier: str | None = None
    checked_at: datetime | None = None
    #: The provider's balance as read during the check, if it was.
    reported_balance_paisa: int | None = None

    @property
    def is_valid(self) -> bool:
        return self.result is CredentialValidation.VALID


class CourierAccountService:
    """Owns courier credentials and their state."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        vault: CredentialVault,
        registry: CourierAdapterRegistry | None = None,
        health: ProviderHealthService | None = None,
    ) -> None:
        self._db = session
        self._vault = vault
        self._registry = registry or get_courier_registry()
        self._health = health or ProviderHealthService(session)

    # ---------------------------------------------------------- retrieving --

    async def get(self, account_id: uuid.UUID) -> CourierAccount:
        account = await self._db.get(CourierAccount, account_id)
        if account is None:
            raise NotFoundError("Courier account not found")
        return account

    async def for_provider(self, provider: str) -> CourierAccount | None:
        """This shop's account for a provider, connected or not.

        Returns disconnected accounts too: the settings screen needs to show
        "you had one and removed it" rather than pretending it never existed.
        """
        result = await self._db.execute(
            sa.select(CourierAccount).where(CourierAccount.provider == provider)
        )
        return result.scalar_one_or_none()

    async def usable_account(self, provider: str) -> CourierAccount | None:
        """The account a provider call may actually use, or ``None``.

        ``None`` is a normal answer meaning "fall back to manual courier mode"
        (brief section 46), not an error.
        """
        account = await self.for_provider(provider)
        if account is None or not account.is_usable:
            return None
        return account

    async def list_accounts(self) -> list[CourierAccount]:
        result = await self._db.execute(
            sa.select(CourierAccount).order_by(CourierAccount.provider.asc())
        )
        return list(result.scalars().all())

    def adapter_for(self, provider: str) -> CourierAdapter | None:
        """The adapter for a provider, or ``None`` if there is no integration.

        Public because booking, recovery, status sync and payment ingestion all
        need one, and reaching into this service's registry attribute from four
        modules would make the registry impossible to change.
        """
        return self._registry.get(provider)

    # ---------------------------------------------------------- connecting --

    async def connect(self, request: ConnectRequest) -> tuple[CourierAccount, ValidationOutcome]:
        """Save credentials for a provider, after checking them.

        The order matters: credentials are **validated before they are stored**,
        so a mistyped key never becomes a saved account that quietly fails every
        booking. A provider outage is the one case where they are stored
        unvalidated — refusing to save a correct key because the provider is
        down would make an outage look like the seller's mistake — and the
        account is marked as needing verification rather than connected.
        """
        cleaned = request.cleaned()
        self._guard_inputs(cleaned)

        adapter = self._require_adapter(cleaned.provider)
        outcome = await self._validate_with(
            adapter,
            build_credentials(
                cleaned.provider,
                primary=cleaned.api_key,
                secondary=cleaned.secret_key,
                config=cleaned.config,
            ),
        )

        if outcome.result is CredentialValidation.INVALID:
            # Nothing is stored. The seller sees the provider's verdict and
            # fixes it; we do not keep a key we know does not work.
            raise ValidationError(
                outcome.message,
                code=ErrorCode.INVALID_COURIER_CREDENTIALS,
                details={"provider": cleaned.provider},
            )

        account = await self.for_provider(cleaned.provider)
        is_new = account is None
        if account is None:
            account = CourierAccount(provider=cleaned.provider)
            self._db.add(account)
            # Flushed first so the row has an id: the vault binds the
            # ciphertext to it, which is what stops an envelope being copied
            # into another shop's row.
            await self._db.flush()

        account.label = cleaned.label or account.label
        self._store_credentials(account, cleaned)
        self._apply_outcome(account, outcome)
        account.status = str(
            CourierAccountStatus.CONNECTED
            if outcome.is_valid
            else CourierAccountStatus.NEEDS_RECONNECT
        )
        account.consecutive_auth_failures = 0
        account.connected_at = utc_now()
        account.disconnected_at = None
        context = current_context()
        account.connected_by = context.user_id

        await self._db.flush()

        await record_audit(
            self._db,
            AuditAction.COURIER_CREDENTIAL_SAVED,
            entity_type="courier_account",
            entity_id=account.id,
            context={
                "provider": account.provider,
                "masked_identifier": account.masked_identifier,
                "validation_result": account.last_validation_result,
                "is_new": is_new,
            },
        )
        record_metric(
            CourierMetric.CREDENTIAL_CONNECTED,
            provider=account.provider,
            result=str(outcome.result),
        )
        return account, outcome

    async def disconnect(self, provider: str, *, reason: str | None = None) -> CourierAccount:
        """Remove credentials, keep the account row.

        The ciphertext is erased rather than the row deleted: parcels booked
        under this account still reference it, and an audit trail that loses
        the account a booking was made with cannot answer the only question
        worth asking after an incident.
        """
        account = await self.for_provider(provider)
        if account is None:
            raise NotFoundError("No courier account to disconnect")

        account.api_key_encrypted = None
        account.secret_key_encrypted = None
        account.webhook_secret_encrypted = None
        account.key_version = None
        # The routing token goes too. A disconnected account's callback URL
        # must stop resolving, or a stale URL left in a provider panel keeps
        # pointing at a shop that revoked it.
        account.webhook_token = None
        account.status = str(CourierAccountStatus.DISCONNECTED)
        account.disconnected_at = utc_now()
        account.capabilities_json = {}
        account.last_validation_result = None
        account.last_validation_message = None
        await self._db.flush()

        await record_audit(
            self._db,
            AuditAction.COURIER_CREDENTIAL_REVOKED,
            entity_type="courier_account",
            entity_id=account.id,
            reason=reason,
            context={
                "provider": account.provider,
                "masked_identifier": account.masked_identifier,
            },
        )
        record_metric(CourierMetric.CREDENTIAL_DISCONNECTED, provider=account.provider)
        log.info(
            "courier account disconnected",
            extra={"provider": account.provider, "operation": "disconnect"},
        )
        return account

    # ---------------------------------------------------------- validating --

    async def test_connection(self, provider: str) -> ValidationOutcome:
        """Re-check stored credentials against the provider.

        Used by the "Test connection" button and by the credential-health job.
        Updates the account's state, which is what lets a revoked key surface as
        ``NEEDS_RECONNECT`` before the seller discovers it mid-booking.
        """
        account = await self.for_provider(provider)
        if account is None or not account.has_credentials:
            raise NotFoundError("No courier account is connected for that provider")

        adapter = self._require_adapter(provider)
        outcome = await self._validate_with(adapter, self.credentials_for(account))
        await self.record_validation(account, outcome)
        return outcome

    async def record_validation(
        self, account: CourierAccount, outcome: ValidationOutcome
    ) -> CourierAccount:
        """Apply a validation result to an account's state.

        The whole of brief section 5 is in the branching here. A conclusive
        ``VALID`` clears the failure streak; a conclusive ``INVALID`` increments
        it and, past the threshold, moves the account to ``NEEDS_RECONNECT``.
        Anything inconclusive updates the message and **leaves the status
        alone** — a provider outage is not evidence about a key.
        """
        self._apply_outcome(account, outcome)
        previous = account.status

        if outcome.result is CredentialValidation.VALID:
            account.consecutive_auth_failures = 0
            if account.account_status is not CourierAccountStatus.DISCONNECTED:
                account.status = str(CourierAccountStatus.CONNECTED)
        elif outcome.result is CredentialValidation.INVALID:
            account.consecutive_auth_failures += 1
            record_metric(CourierMetric.AUTH_FAILURE, provider=account.provider)
            if account.consecutive_auth_failures >= AUTH_FAILURES_BEFORE_RECONNECT:
                account.status = str(CourierAccountStatus.NEEDS_RECONNECT)
        # PROVIDER_UNAVAILABLE and UNKNOWN deliberately change nothing.

        await self._db.flush()

        if account.status != previous:
            await record_audit(
                self._db,
                AuditAction.PROVIDER_HEALTH_CHANGED,
                entity_type="courier_account",
                entity_id=account.id,
                context={
                    "provider": account.provider,
                    "from": previous,
                    "to": account.status,
                    "consecutive_auth_failures": account.consecutive_auth_failures,
                },
            )
        return account

    async def _validate_with(self, adapter: CourierAdapter, credentials: Any) -> ValidationOutcome:
        """Ask the provider, and classify the answer into four outcomes."""
        started = utc_now()
        try:
            result: ValidationResult = await adapter.validate_credentials(credentials)
        except Exception as exc:
            # An unexpected exception is not evidence that a key is wrong.
            log.warning(
                "courier credential check failed",
                extra={
                    "provider": adapter.provider,
                    "operation": "validate_credentials",
                    "error": type(exc).__name__,
                },
            )
            await self._health.record_failure(
                ProviderKind.COURIER,
                adapter.provider,
                capability=str(Capability.CREDENTIAL_VALIDATION),
                error_code=type(exc).__name__,
            )
            return ValidationOutcome(
                result=CredentialValidation.UNKNOWN,
                message=(
                    "We could not reach the courier to check these details. "
                    "They have not been marked wrong."
                ),
                checked_at=started,
            )

        if result.valid:
            await self._health.record_success(
                ProviderKind.COURIER,
                adapter.provider,
                capability=str(Capability.CREDENTIAL_VALIDATION),
            )
            return ValidationOutcome(
                result=CredentialValidation.VALID,
                message=result.message or "Connected.",
                capabilities=result.detected_capabilities,
                masked_identifier=result.account_label,
                checked_at=started,
                reported_balance_paisa=result.reported_balance_paisa,
            )

        # The adapter states this explicitly rather than us inferring it from
        # its message: a rejection is the only outcome that may count against
        # an account, and deriving it from prose would make a wording change
        # start disconnecting sellers.
        rejected = result.rejected
        await self._health.record_failure(
            ProviderKind.COURIER,
            adapter.provider,
            capability=str(Capability.CREDENTIAL_VALIDATION),
            error_code="INVALID_CREDENTIALS" if rejected else "PROVIDER_UNAVAILABLE",
            is_auth_failure=rejected,
        )
        return ValidationOutcome(
            result=(
                CredentialValidation.INVALID
                if rejected
                else CredentialValidation.PROVIDER_UNAVAILABLE
            ),
            message=result.message or "The courier did not accept these details.",
            masked_identifier=result.account_label,
            checked_at=started,
        )

    # -------------------------------------------------------- credentials --

    def credentials_for(self, account: CourierAccount) -> Any:
        """Decrypt this account's credentials for one call.

        Returns whichever credentials type the account's provider declares. The
        returned object masks itself in ``repr`` and ``str``, so it cannot reach
        a log line by being interpolated into a message — which is how
        credentials usually get into logs.
        """
        if not account.has_credentials:
            raise ConflictError(
                "That courier account has no credentials stored",
                code=ErrorCode.COURIER_ACCOUNT_NEEDS_RECONNECT,
                details={"provider": account.provider},
            )
        assert account.api_key_encrypted is not None  # noqa: S101 - narrowed above
        assert account.secret_key_encrypted is not None  # noqa: S101
        context = account.vault_context
        return build_credentials(
            account.provider,
            primary=self._vault.decrypt(account.api_key_encrypted, context=context),
            secondary=self._vault.decrypt(account.secret_key_encrypted, context=context),
            config=dict(account.metadata_json or {}),
        )

    async def bookable_couriers(
        self, *, enabled: dict[str, bool] | None = None
    ) -> list[BookableCourier]:
        """Which couriers this shop can book with right now, and why not.

        Every input is provider-independent — the capability manifest, the
        credential declaration, the account's own state and provider health —
        so adding a courier changes no logic here and the client never branches
        on a provider name.

        ``enabled`` carries the per-shop feature flags, which live in the API
        layer; a provider missing from the map is treated as enabled so this
        method is usable from a worker with no flag service.

        The order matters. It reports the *first* thing a seller would have to
        fix, so "connect it" is never shown for a courier the shop has not been
        given, and "choose a pickup store" is never shown for one that is not
        connected.
        """
        flags = enabled or {}
        accounts = {account.provider: account for account in await self.list_accounts()}
        results: list[BookableCourier] = []

        for provider, manifest in sorted(load_all_manifests().items()):
            spec = spec_for(provider)
            if spec is None:
                # Nothing to connect: manual mode, or a provider with no
                # verified contract yet. Not a booking option at all.
                continue

            display_name = spec.display_name
            requires_store = spec.requires_store
            account = accounts.get(provider)
            store_name = (account.metadata_json or {}).get("store_name") if account else None

            # `partial` rather than a closure: it binds this iteration's
            # values now. A closure would capture the loop variables by
            # reference, so every entry would report the last provider's name.
            _no = partial(
                BookableCourier,
                provider=provider,
                display_name=display_name,
                bookable=False,
                requires_store=requires_store,
                store_name=store_name,
                supports_delivery_type=spec.supports_delivery_type,
            )

            if not flags.get(provider, True):
                results.append(_no(reason=BookableReason.NOT_ENABLED))
                continue
            if not manifest.supports(Capability.CREATE_SINGLE):
                results.append(_no(reason=BookableReason.NO_VERIFIED_CREATE))
                continue
            if account is None or not account.has_credentials:
                results.append(_no(reason=BookableReason.NOT_CONNECTED))
                continue
            if not account.is_usable:
                results.append(_no(reason=BookableReason.NEEDS_RECONNECT))
                continue
            if requires_store and not (account.metadata_json or {}).get("store_id"):
                # Connected is not bookable: Pathao rejects every create with
                # no store_id, and discovering that at booking time wastes a
                # seller's attempt on an order they were trying to ship.
                results.append(_no(reason=BookableReason.NEEDS_PICKUP_STORE))
                continue
            if not await self._health.allows(
                provider,
                capability=str(Capability.CREATE_SINGLE),
                tenant_id=account.tenant_id,
            ):
                results.append(_no(reason=BookableReason.PROVIDER_UNAVAILABLE))
                continue

            results.append(
                BookableCourier(
                    provider=provider,
                    display_name=display_name,
                    bookable=True,
                    requires_store=requires_store,
                    store_name=store_name,
                    supports_delivery_type=spec.supports_delivery_type,
                )
            )

        # Bookable first, then alphabetically, so the picker opens on something
        # a seller can actually use.
        results.sort(key=lambda row: (not row.bookable, row.display_name))
        return results

    async def select_store(
        self, provider: str, *, provider_store_id: str, name: str | None = None
    ) -> CourierAccount:
        """Record which pickup store this shop books from.

        Config, not credential: it is not secret, it is shown back to the
        seller, and every booking reads it. Audited, because changing where
        parcels are collected from is the kind of change someone will later
        need to explain.
        """
        account = await self.for_provider(provider)
        if account is None or not account.has_credentials:
            raise NotFoundError("No courier account is connected for that provider")

        chosen = (provider_store_id or "").strip()
        if not chosen:
            raise ValidationError("A pickup store is required")

        merged = dict(account.metadata_json or {})
        previous = merged.get("store_id")
        merged["store_id"] = chosen
        if name:
            merged["store_name"] = name.strip()
        account.metadata_json = merged
        await self._db.flush()

        await record_audit(
            self._db,
            AuditAction.COURIER_CREDENTIAL_SAVED,
            entity_type="courier_account",
            entity_id=account.id,
            context={
                "provider": account.provider,
                "change": "pickup_store",
                "previous_store_id": previous,
                "store_id": chosen,
            },
        )
        return account

    async def ensure_webhook_token(self, account: CourierAccount) -> str:
        """This account's callback routing token, minting one if it has none.

        Stable once issued, so a URL a seller has already pasted into a
        provider's panel keeps working. Regenerating it is a deliberate act —
        disconnecting the account — not a side effect of re-saving credentials.
        """
        if account.webhook_token:
            return account.webhook_token
        account.webhook_token = secrets.token_urlsafe(32)
        await self._db.flush()
        return account.webhook_token

    def webhook_secret_for(self, account: CourierAccount) -> str | None:
        """This account's webhook signing secret, or ``None`` if it has none.

        Separate from :meth:`credentials_for` because it is used by a different
        caller for a different reason: an unauthenticated inbound callback, not
        an outbound API call. Returning ``None`` is the normal answer for a
        provider without webhooks, and for a shop that has not configured one
        yet — the receiver treats that as *not configured*, never as verified.
        """
        if not account.webhook_secret_encrypted:
            return None
        return self._vault.decrypt(account.webhook_secret_encrypted, context=account.vault_context)

    def _store_credentials(self, account: CourierAccount, request: ConnectRequest) -> None:
        context = account.vault_context
        account.api_key_encrypted = self._vault.encrypt(request.api_key, context=context)
        account.secret_key_encrypted = self._vault.encrypt(request.secret_key, context=context)
        account.key_version = self._vault.key_version
        account.masked_identifier = _mask(request.api_key)

        if request.config:
            # Merged, not replaced: re-entering credentials must not silently
            # drop a pickup store the seller chose in a separate step.
            merged = dict(account.metadata_json or {})
            merged.update(request.config)
            account.metadata_json = merged

        if request.webhook_secret:
            account.webhook_secret_encrypted = self._vault.encrypt(
                request.webhook_secret, context=context
            )

    # ------------------------------------------------------------ balance --

    async def record_balance(
        self, account: CourierAccount, *, balance_paisa: int, observed_at: datetime | None = None
    ) -> CourierAccount:
        """Store the provider's reported account balance.

        Kept next to the account and labelled as the provider's own figure. It
        is **never** added to, compared with, or reconciled against the ecomsbd
        COD outstanding total: they measure different things, and a screen that
        merged them would be wrong in a way nobody could unpick (brief
        section 19).
        """
        account.reported_balance_paisa = balance_paisa
        account.reported_balance_at = observed_at or utc_now()
        await self._db.flush()
        return account

    # ----------------------------------------------------------- internals --

    def _require_adapter(self, provider: str) -> CourierAdapter:
        adapter = self._registry.get(provider)
        if adapter is None:
            raise ValidationError(
                f"{provider} is not a courier ecomsbd can connect to",
                details={"provider": provider},
            )
        return adapter

    def _guard_inputs(self, request: ConnectRequest) -> None:
        spec = spec_for(request.provider)
        # Field names come from the provider's own declaration, so the error a
        # Pathao seller reads says "Client ID", not "API key".
        primary_label = "API key"
        secondary_label = "secret key"
        if spec is not None:
            primary_field = spec.field_named(spec.primary)
            secondary_field = spec.field_named(spec.secondary)
            if primary_field is not None:
                primary_label = primary_field.label_en
            if secondary_field is not None:
                secondary_label = secondary_field.label_en

        if not request.api_key or not request.secret_key:
            raise ValidationError(
                f"Both the {primary_label} and the {secondary_label} are required"
            )
        # Generous bounds. The point is to refuse an obviously pasted-wrong
        # value (a whole email, an empty string) without guessing the
        # provider's key format, which no provider states.
        for name, value in (
            (primary_label, request.api_key),
            (secondary_label, request.secret_key),
        ):
            if len(value) > 300:
                raise ValidationError(f"That {name} is too long to be a credential")
        if request.webhook_secret and len(request.webhook_secret) > 300:
            raise ValidationError("That webhook secret is too long")
        manifest = load_manifest(request.provider)
        if manifest is None:
            raise ValidationError(
                f"{request.provider} has no integration manifest",
                details={"provider": request.provider},
            )
        if manifest.is_fully_unverified:
            # Nothing about this provider has been confirmed against real
            # documentation, so there is no call we could make with these
            # credentials and no booking they could serve. Keeping them would
            # mean holding a seller's secret for no purpose and showing a
            # connected courier that cannot move a parcel — so they are not
            # stored, and the blocker says what would change that.
            raise ValidationError(
                f"{manifest.display_name} cannot be connected yet: none of its "
                "API behaviour has been verified against real documentation. "
                "Use manual courier mode for now.",
                code=ErrorCode.COURIER_PROVIDER_UNAVAILABLE,
                details={
                    "provider": request.provider,
                    "blockers": list(manifest.blockers),
                },
            )

    def _apply_outcome(self, account: CourierAccount, outcome: ValidationOutcome) -> None:
        account.last_validation_result = str(outcome.result)
        account.last_validation_message = outcome.message[:300]
        if outcome.is_valid:
            account.last_verified_at = outcome.checked_at or utc_now()
            account.capabilities_json = {
                str(capability): "true" for capability in sorted(outcome.capabilities, key=str)
            }
        if outcome.masked_identifier and not account.masked_identifier:
            account.masked_identifier = outcome.masked_identifier
        if outcome.is_valid and outcome.reported_balance_paisa is not None:
            account.reported_balance_paisa = outcome.reported_balance_paisa
            account.reported_balance_at = outcome.checked_at or utc_now()


def _mask(api_key: str) -> str:
    """``****abcd``. Non-reversible, and enough to tell two accounts apart."""
    return f"****{api_key[-4:]}" if len(api_key) > 4 else "****"


async def resolve_account_by_webhook_token(
    session: AsyncSession, *, provider: str, token: str
) -> CourierAccount | None:
    """Find the courier account a callback URL points at, across tenants.

    A provider callback arrives with no session and no tenant, so this is a
    deliberate, narrow cross-tenant read — one row, by an unguessable token,
    for the sole purpose of discovering which tenant to become. The bypass is
    logged, as every bypass is.

    The token is not a credential: finding an account by it grants nothing. The
    account's own webhook secret still has to verify the delivery, and a token
    that matches nothing returns ``None``, which the route answers identically
    to a token that matches an unconfigured account — a callback URL must not
    become an oracle for which shops exist.
    """
    cleaned = (token or "").strip()
    if not cleaned:
        return None

    with allow_cross_tenant(f"{provider} webhook account resolution"):
        result = await session.execute(
            sa.select(CourierAccount).where(
                CourierAccount.provider == provider,
                CourierAccount.webhook_token == cleaned,
            )
        )
        return result.scalar_one_or_none()


class BookableReason(StrEnum):
    """Why a courier cannot be booked with right now.

    Stable codes rather than sentences, because the mobile and web clients
    branch on them and each renders its own copy in the seller's language. Every
    one of these is provider-independent: it is derived from the capability
    manifest, the credential declaration, the account's state and provider
    health, never from a provider name.
    """

    #: The shop has not been given this courier.
    NOT_ENABLED = "NOT_ENABLED"
    #: No verified create exists for this provider yet (RedX today).
    NO_VERIFIED_CREATE = "NO_VERIFIED_CREATE"
    #: No credentials stored.
    NOT_CONNECTED = "NOT_CONNECTED"
    #: Credentials were rejected repeatedly; a person must re-enter them.
    NEEDS_RECONNECT = "NEEDS_RECONNECT"
    #: Connected, but a mandatory pickup store has not been chosen. Pathao
    #: rejects every create without one, so this is not a cosmetic gap.
    NEEDS_PICKUP_STORE = "NEEDS_PICKUP_STORE"
    #: The circuit breaker is open: we already know the provider is failing.
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class BookableCourier:
    """Whether one courier can take a booking from this shop right now."""

    provider: str
    display_name: str
    bookable: bool
    reason: BookableReason | None = None
    #: Whether this provider needs a pickup store at all, so the client can
    #: offer the fix rather than only reporting the problem.
    requires_store: bool = False
    store_name: str | None = None
    #: Whether the booking form may offer a delivery-type choice for this
    #: courier. The booking sheet reads this instead of checking a provider
    #: name, which is what keeps the generic order UI provider-independent.
    supports_delivery_type: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "display_name": self.display_name,
            "bookable": self.bookable,
            "reason": str(self.reason) if self.reason else None,
            "requires_store": self.requires_store,
            "store_name": self.store_name,
            "supports_delivery_type": self.supports_delivery_type,
        }
