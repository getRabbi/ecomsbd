"""External risk providers (V3.7): a provider-neutral activation layer.

A shop may connect a licensed, documented external risk provider. Everything a
provider says is kept apart from the shop's own Risk Check, attributed to the
provider, timestamped and cached. Nothing here decides an order.

One reviewed adapter ships (V4 WP0.6): BD Courier, :mod:`app.risk_providers.bdcourier`,
behind the ``bdcourier_enabled`` flag (default off) and the shop's own API key.
:mod:`app.risk_providers.registry` offers nothing else.
"""
