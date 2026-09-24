"""External risk providers (V3.7): a provider-neutral activation layer.

A shop may connect a licensed, documented external risk provider. Everything a
provider says is kept apart from the shop's own Risk Check, attributed to the
provider, timestamped and cached. Nothing here decides an order.

No provider adapter ships in this release: :mod:`app.risk_providers.registry`
is empty until a reviewed adapter for a real, documented contract is added.
"""
