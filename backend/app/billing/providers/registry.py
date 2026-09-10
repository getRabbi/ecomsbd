"""Provider registry and distribution-channel policy.

Master spec section 27.1 forbids a Play-distributed build from steering users to
an external payment channel where Play policy disallows it. The decision of
*which* billing call-to-action a client may show is made here, once, on the
server — never by a platform check inside a widget, which is how one screen ends
up out of step with the policy.

The rule composes three inputs, in this order:

1.  **distribution channel** — which build is asking;
2.  **provider availability** — is it configured at all;
3.  **entitlement** — what the tenant already has.

A provider that is allowed in the channel but unconfigured is reported as
unavailable *with its blocker code*, so the seller sees "not available yet"
rather than a button that fails.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.billing.models import BillingProviderKind, DistributionChannel
from app.billing.providers.base import BillingProvider, ProviderAvailability
from app.billing.providers.bkash_web import BkashApiClient, BkashWebBillingProvider
from app.billing.providers.google_play import GooglePlayBillingProvider, PlayApiClient
from app.billing.providers.manual import AdminManualBillingProvider
from app.common.feature_flags import FlagKey
from app.core.config import Settings

__all__ = [
    "PROVIDER_FLAGS",
    "BillingProviderRegistry",
    "ChannelPolicy",
    "build_registry",
    "resolve_channel",
]


#: Which providers a build may *offer* for purchase. Manual is absent from
#: every channel: it is support-only and must never be reachable from the app.
_CHANNEL_PROVIDERS: dict[DistributionChannel, tuple[BillingProviderKind, ...]] = {
    # Section 27.1: a Play build sells through Play Billing and shows no
    # external payment link. Bangladesh is not in the alternative-billing
    # country list in the sources this spec was verified against.
    DistributionChannel.PLAY: (BillingProviderKind.PLAY,),
    DistributionChannel.WEB: (BillingProviderKind.BKASH_WEB,),
    DistributionChannel.DIRECT: (BillingProviderKind.BKASH_WEB,),
    # An internal build may exercise either, so QA can test both paths.
    DistributionChannel.INTERNAL_TEST: (
        BillingProviderKind.PLAY,
        BillingProviderKind.BKASH_WEB,
    ),
}

#: Kill switch per provider (master spec section 45). Public: the API needs it
#: to resolve flag state, which requires the tenant context this module lacks.
PROVIDER_FLAGS: dict[BillingProviderKind, FlagKey] = {
    BillingProviderKind.PLAY: FlagKey.PLAY_BILLING_ENABLED,
    BillingProviderKind.BKASH_WEB: FlagKey.BKASH_WEB_BILLING_ENABLED,
}


def resolve_channel(header_value: str | None, settings: Settings) -> DistributionChannel:
    """Decide the distribution channel for a request.

    A client may **narrow** the channel but never widen it. A build that says
    "I am a Play build" is believed, because that is the restrictive claim; a
    build that says "I am a web build" is not, because that claim would unlock
    an external payment CTA that Play policy may forbid.

    The server's configured channel is the ceiling. Nothing a client sends can
    raise it.
    """
    default = DistributionChannel(settings.distribution_channel)
    if header_value is None:
        return default
    try:
        claimed = DistributionChannel(header_value.strip().upper())
    except ValueError:
        return default
    if claimed is DistributionChannel.PLAY:
        return DistributionChannel.PLAY
    return default


@dataclass(frozen=True, slots=True)
class ChannelPolicy:
    """What a given build is allowed to offer right now."""

    channel: DistributionChannel
    providers: tuple[ProviderAvailability, ...]

    @property
    def purchasable(self) -> tuple[ProviderAvailability, ...]:
        """Providers a client may actually render a purchase CTA for."""
        return tuple(p for p in self.providers if p.available and p.allowed_in_channel)

    @property
    def can_purchase_in_app(self) -> bool:
        return bool(self.purchasable)

    @property
    def allows_external_payment_cta(self) -> bool:
        return self.channel.allows_external_payment_cta

    def as_dict(self) -> dict[str, object]:
        return {
            "channel": str(self.channel),
            "can_purchase": self.can_purchase_in_app,
            "allows_external_payment_link": self.allows_external_payment_cta,
            "providers": [p.as_dict() for p in self.providers],
        }


class BillingProviderRegistry:
    """Looks up providers and answers "what may this build offer?"."""

    def __init__(self, providers: dict[BillingProviderKind, BillingProvider]) -> None:
        self._providers = providers

    def get(self, kind: BillingProviderKind | str) -> BillingProvider:
        try:
            key = BillingProviderKind(kind)
        except ValueError as exc:
            raise KeyError(f"unknown billing provider '{kind}'") from exc
        provider = self._providers.get(key)
        if provider is None:
            raise KeyError(f"billing provider '{kind}' is not registered")
        return provider

    def __contains__(self, kind: object) -> bool:
        try:
            return BillingProviderKind(str(kind)) in self._providers
        except ValueError:
            return False

    def availability(self, kind: BillingProviderKind) -> ProviderAvailability:
        return self.get(kind).availability()

    def policy(
        self,
        channel: DistributionChannel,
        *,
        disabled_flags: frozenset[FlagKey] = frozenset(),
    ) -> ChannelPolicy:
        """The billing options this build may present.

        ``disabled_flags`` comes from the caller, which has the tenant context
        the flag service needs. A flag that is off produces an *unavailable*
        provider with a ``FEATURE_FLAG_DISABLED`` blocker rather than an absent
        one, so an operator can see the kill switch was the reason.
        """
        from app.billing.providers.base import ProviderBlocker

        allowed = _CHANNEL_PROVIDERS.get(channel, ())
        results: list[ProviderAvailability] = []
        for kind, provider in self._providers.items():
            state = provider.availability()
            in_channel = kind in allowed and state.allowed_in_channel
            flag = PROVIDER_FLAGS.get(kind)
            if flag is not None and flag in disabled_flags:
                state = ProviderAvailability(
                    provider=kind,
                    available=False,
                    blocker=ProviderBlocker.FEATURE_FLAG_DISABLED,
                    detail=f"The '{flag}' feature flag is off",
                    allowed_in_channel=in_channel,
                )
            else:
                state = ProviderAvailability(
                    provider=state.provider,
                    available=state.available,
                    blocker=state.blocker,
                    detail=state.detail,
                    allowed_in_channel=in_channel,
                )
            results.append(state)

        return ChannelPolicy(channel=channel, providers=tuple(results))


def build_registry(
    settings: Settings,
    *,
    play_api: PlayApiClient | None = None,
    bkash_api: BkashApiClient | None = None,
) -> BillingProviderRegistry:
    """Construct the registry.

    The two transport ports default to ``None``, which is the honest production
    state today: no Play service account and no bKash merchant contract exist,
    so both providers report themselves unavailable with a blocker code. Tests
    pass fixture clients to exercise the full verification path.
    """
    return BillingProviderRegistry(
        {
            BillingProviderKind.PLAY: GooglePlayBillingProvider(settings, api=play_api),
            BillingProviderKind.BKASH_WEB: BkashWebBillingProvider(settings, api=bkash_api),
            BillingProviderKind.MANUAL_ADMIN: AdminManualBillingProvider(),
        }
    )
