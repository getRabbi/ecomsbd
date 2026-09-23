"""What each courier asks a seller for, and how it becomes a credentials object.

V1 had one provider, so ``api_key`` + ``secret_key`` could be hard-coded from
the connect form all the way to the client. V2.1 has three, and they do not
agree: Steadfast wants a static key pair, Pathao wants OAuth client credentials
plus a pickup store, RedX wants its own shape again.

The obvious fix — branch on the provider name where the difference shows up —
puts that branch in the API layer, in the account service, in the mobile form
and in the web form, and they drift. So the shape is declared **once, here**,
and everything else reads the declaration:

*   the account service validates and builds credentials from it;
*   the API publishes it, so the mobile and web credential forms are generated
    rather than hand-written per provider;
*   adding RedX means adding a spec, not editing four screens.

Two storage facts this module encodes rather than documents:

*   **Exactly two encrypted credential slots exist** on a courier account, plus
    one for a webhook secret. A provider's ``primary`` field goes to the first,
    its ``secondary`` to the second. A provider needing a third secret would
    need a migration, and that is deliberate — secrets should be countable. A
    provider with a *single* secret (RedX's API token) declares no secondary;
    its second slot then holds an encrypted empty value, so every account
    keeps the same two-slot shape and the same "has credentials" rule.
*   **Config is not credentials.** A pickup store id and a sandbox flag are not
    secret, are shown back to the seller, and live in ``metadata_json``. A
    value declared ``secret`` is encrypted and never returned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.couriers.pathao.client import PathaoCredentials
from app.couriers.redx.client import RedxCredentials
from app.couriers.redx.contract import WEBHOOK_TOKEN_PARAM
from app.couriers.steadfast.client import SteadfastCredentials

__all__ = [
    "CREDENTIAL_SPECS",
    "CredentialField",
    "ProviderCredentialSpec",
    "build_credentials",
    "spec_for",
]


@dataclass(frozen=True, slots=True)
class CredentialField:
    """One input on a provider's connect form."""

    name: str
    label_en: str
    label_bn: str
    #: Secret fields are encrypted at rest and never returned to a client.
    secret: bool = True
    required: bool = True
    help_en: str | None = None
    help_bn: str | None = None
    #: ``password`` masks the input; ``text`` does not.
    input_type: str = "password"

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label_en": self.label_en,
            "label_bn": self.label_bn,
            "secret": self.secret,
            "required": self.required,
            "help_en": self.help_en,
            "help_bn": self.help_bn,
            "input_type": self.input_type,
        }


@dataclass(frozen=True, slots=True)
class ProviderCredentialSpec:
    """How one provider's connect flow works."""

    provider: str
    display_name: str
    fields: tuple[CredentialField, ...]
    #: The field stored in the first encrypted slot, and masked for display.
    primary: str
    #: The field stored in the second encrypted slot. ``None`` for a provider
    #: whose whole credential is one secret.
    secondary: str | None
    #: Whether the provider has a sandbox a seller may choose.
    supports_sandbox: bool = False
    #: Whether a pickup store must be chosen before booking. When true, the
    #: connect flow is two steps: save credentials, then choose a store.
    requires_store: bool = False
    #: Whether a pickup store *may* be chosen, where choosing one is optional.
    #: Implied by :attr:`requires_store`.
    supports_store: bool = False
    #: Whether every booking must name a delivery area picked from the
    #: provider's own area list. Read by the booking sheet, so it shows an area
    #: picker without checking a provider name.
    requires_delivery_area: bool = False
    #: Whether this provider sends webhooks ecomsbd can verify, which means the
    #: seller is given a callback URL and asked for a webhook secret.
    uses_webhook: bool = False
    #: Whether ecomsbd issues the webhook secret itself, inside the callback
    #: URL, rather than asking the seller for one from the provider's panel.
    #: RedX's callbacks authenticate by a token in the URL, so the seller has
    #: nothing to type — only a URL to paste.
    webhook_secret_generated: bool = False
    #: The callback URL's query parameter that carries a generated secret.
    webhook_secret_param: str | None = None
    #: Whether the booking form may offer a delivery-type choice. Declared here
    #: rather than decided in the UI, so the booking sheet never branches on a
    #: provider name to know which extra field to show. ``False`` means the
    #: field is hidden, not that the courier has one delivery type.
    supports_delivery_type: bool = False
    webhook_help_en: str | None = None
    webhook_help_bn: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_single_secret(self) -> bool:
        return self.secondary is None

    def field_named(self, name: str) -> CredentialField | None:
        for candidate in self.fields:
            if candidate.name == name:
                return candidate
        return None

    def as_dict(self) -> dict[str, Any]:
        """The client-facing shape. Contains no value, only the form."""
        return {
            "provider": self.provider,
            "display_name": self.display_name,
            "fields": [item.as_dict() for item in self.fields],
            "supports_sandbox": self.supports_sandbox,
            "requires_store": self.requires_store,
            "supports_store": self.supports_store or self.requires_store,
            "requires_delivery_area": self.requires_delivery_area,
            "uses_webhook": self.uses_webhook,
            "webhook_secret_generated": self.webhook_secret_generated,
            "supports_delivery_type": self.supports_delivery_type,
            "webhook_help_en": self.webhook_help_en,
            "webhook_help_bn": self.webhook_help_bn,
        }


STEADFAST_SPEC = ProviderCredentialSpec(
    provider="steadfast",
    display_name="Steadfast",
    fields=(
        CredentialField(
            name="api_key",
            label_en="API Key",
            label_bn="API Key",
            help_en="From your Steadfast merchant panel.",
            help_bn="আপনার Steadfast মার্চেন্ট প্যানেল থেকে নিন।",
            # Readable on screen. Still secret — encrypted at rest and never
            # returned — but it is the half that identifies *which* credential
            # is loaded, so masking it stops a seller checking they pasted the
            # right one without helping anyone who can see their screen.
            input_type="text",
        ),
        CredentialField(
            name="secret_key",
            label_en="Secret Key",
            label_bn="Secret Key",
            help_en="From your Steadfast merchant panel.",
            help_bn="আপনার Steadfast মার্চেন্ট প্যানেল থেকে নিন।",
        ),
    ),
    primary="api_key",
    secondary="secret_key",
    supports_delivery_type=True,
)

PATHAO_SPEC = ProviderCredentialSpec(
    provider="pathao",
    display_name="Pathao",
    fields=(
        CredentialField(
            name="client_id",
            label_en="Client ID",
            # Kept in English in Bangla too: a seller reads "Client ID" in
            # the Pathao panel, and Steadfast's Bangla labels keep "API Key"
            # for the same reason.
            label_bn="Client ID",
            help_en="Pathao merchant panel → Developer API.",
            help_bn="Pathao মার্চেন্ট প্যানেল → Developer API।",
            # Readable, for the same reason as Steadfast's API key.
            input_type="text",
        ),
        CredentialField(
            name="client_secret",
            label_en="Client Secret",
            label_bn="Client Secret",
            help_en="Pathao merchant panel → Developer API.",
            help_bn="Pathao মার্চেন্ট প্যানেল → Developer API।",
        ),
    ),
    primary="client_id",
    secondary="client_secret",
    supports_sandbox=True,
    # store_id is a mandatory field on every Pathao create and Pathao provides
    # no default, so a connected account is not a bookable account until a
    # store is chosen.
    requires_store=True,
    uses_webhook=True,
    webhook_help_en=(
        "Pathao does not offer a status lookup — parcel updates arrive only by "
        "webhook. Paste this URL and secret into your Pathao merchant panel to "
        "keep Pathao parcels up to date."
    ),
    webhook_help_bn=(
        "Pathao-তে স্ট্যাটাস লুকআপ নেই — পার্সেলের আপডেট শুধু webhook দিয়েই আসে। "
        "Pathao মার্চেন্ট প্যানেলে এই URL ও সিক্রেট বসান, তাহলে Pathao পার্সেলের "
        "অবস্থা হালনাগাদ থাকবে।"
    ),
    # Pathao does document two delivery types (48 Normal, 12 On Demand), but
    # the booking API's `delivery_type` field is Steadfast-shaped — 0 or 1 —
    # so offering the choice here would send Pathao a value it does not
    # recognise. Every Pathao booking goes out as Normal until that field is
    # made per-provider.
    supports_delivery_type=False,
)

REDX_SPEC = ProviderCredentialSpec(
    provider="redx",
    display_name="RedX",
    fields=(
        CredentialField(
            name="api_token",
            # RedX's developer page calls it the Token and sends it as the
            # API-ACCESS-TOKEN header. Kept in English in Bangla, as the other
            # couriers' labels are, because that is the word on RedX's page.
            label_en="API Token",
            label_bn="API Token",
            help_en=(
                "RedX merchant panel → Developer API → Token. Use the production "
                "token, or the sandbox token with the sandbox switched on."
            ),
            help_bn=(
                "RedX মার্চেন্ট প্যানেল → Developer API → Token। Production token দিন, "
                "অথবা sandbox চালু করে sandbox token দিন।"
            ),
        ),
    ),
    primary="api_token",
    secondary=None,
    # RedX publishes a sandbox host with its own token.
    supports_sandbox=True,
    # pickup_store_id is optional on a RedX create. A seller with more than one
    # RedX pickup store may choose which one ecomsbd books from; the charge
    # quote needs one, because RedX prices from the pickup store's area.
    supports_store=True,
    # delivery_area and delivery_area_id are required on every RedX create.
    requires_delivery_area=True,
    uses_webhook=True,
    webhook_secret_generated=True,
    # The parameter name in RedX's own example callback URL.
    webhook_secret_param=WEBHOOK_TOKEN_PARAM,
    webhook_help_en=(
        "Optional. Paste this URL into RedX → Developer API → Webhook, save it and "
        "switch the webhook on. RedX then sends parcel updates here, and ecomsbd "
        "checks the token in the URL before recording them. Parcel status is also "
        "checked with RedX regularly, so booking works without it. Keep this URL "
        "private: it contains your shop's callback token."
    ),
    webhook_help_bn=(
        "ঐচ্ছিক। এই URL টি RedX → Developer API → Webhook-এ বসিয়ে সেভ করুন এবং "
        "webhook চালু করুন। তখন RedX পার্সেলের আপডেট এখানে পাঠাবে, আর ecomsbd URL-এর "
        "টোকেন মিলিয়ে তবেই সেগুলো রেকর্ড করবে। পার্সেলের স্ট্যাটাস নিয়মিত RedX থেকেও "
        "দেখা হয়, তাই এটি ছাড়াও বুকিং চলবে। URL টি গোপন রাখুন: এতে আপনার শপের "
        "কলব্যাক টোকেন আছে।"
    ),
    # RedX's delivery types (regular, reverse, exchange, partial) describe what
    # kind of parcel it is, not a service a seller picks at booking.
    supports_delivery_type=False,
)

CREDENTIAL_SPECS: dict[str, ProviderCredentialSpec] = {
    STEADFAST_SPEC.provider: STEADFAST_SPEC,
    PATHAO_SPEC.provider: PATHAO_SPEC,
    REDX_SPEC.provider: REDX_SPEC,
}


def spec_for(provider: str) -> ProviderCredentialSpec | None:
    return CREDENTIAL_SPECS.get(provider.strip().lower())


def build_credentials(
    provider: str,
    *,
    primary: str,
    secondary: str,
    config: dict[str, Any] | None = None,
) -> Any:
    """Assemble the credentials object a provider's client expects.

    The one place that knows how the two stored slots map onto each provider's
    own credential type. Everything else passes an opaque ``creds`` through.
    """
    resolved = provider.strip().lower()
    settings = config or {}

    if resolved == "steadfast":
        return SteadfastCredentials(api_key=primary, secret_key=secondary)
    if resolved == "pathao":
        return PathaoCredentials(
            client_id=primary,
            client_secret=secondary,
            sandbox=bool(settings.get("sandbox", False)),
        )
    if resolved == "redx":
        # One secret. The second slot is an encrypted empty value and is
        # deliberately not read.
        return RedxCredentials(api_token=primary, sandbox=bool(settings.get("sandbox", False)))
    raise ValueError(f"No credential shape is declared for {provider}")
