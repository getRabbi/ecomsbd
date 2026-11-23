"""Every API URL that is registered somewhere outside ecomsbd (V4 WP7.3).

Moving the API to a new domain (``api.scalemyprints.com`` → ``api.ecomsbd.com``)
means changing ``PUBLIC_BASE_URL`` and then updating each place that holds one
of these URLs: Meta's app settings, Shopify / WooCommerce, courier panels where
a seller pasted a callback, links in emails already sent. The old host stays in
``PUBLIC_BASE_URL_ALIASES`` (and is served) until each one has moved.

This module is the list, built for any base, so the runbook
(``docs/runbooks/API_DOMAIN_MIGRATION.md``) never goes stale; the tests check it
against what each integration actually builds. Run it to print the list for
the primary base and every alias (no secret and no shop data is printed;
per-connection tokens show as ``{token}``)::

    python -m app.core.public_urls
"""

from __future__ import annotations

from app.core.config import Settings, get_settings

__all__ = ["main", "registered_urls"]


def registered_urls(base: str) -> dict[str, str]:
    """What each provider or recipient holds, for an API at ``base``."""
    root = base.rstrip("/")
    return {
        # Meta app → Facebook Login → Valid OAuth Redirect URIs.
        "meta_oauth_redirect": f"{root}/v1/integration-callbacks/meta",
        # Meta app → Webhooks (Page and WhatsApp Business Account) callback.
        "meta_webhook": f"{root}/v1/webhooks/integrations/meta",
        # Shopify app → allowed redirection URL.
        "shopify_oauth_redirect": f"{root}/v1/integration-callbacks/shopify",
        # WooCommerce one-click key callback (sent per connect, not registered).
        "woocommerce_callback": f"{root}/v1/integration-callbacks/woocommerce",
        # Shopify / WooCommerce order webhooks, registered per connection.
        "store_webhook": f"{root}/v1/webhooks/integrations/{{provider}}/{{token}}",
        # A provider's sign-in page returning to the app (no web dashboard).
        "integration_return": f"{root}/v1/integration-callbacks/return",
        # Custom website order API given to the seller's developer.
        "website_api": f"{root}/public/v1",
        # Courier callbacks a seller pasted into the courier's panel.
        "courier_webhook": f"{root}/v1/webhooks/couriers/{{provider}}/{{token}}",
        # Links in emails already sent (verify, reset) and unsubscribe links.
        "email_verify": f"{root}/auth/email/verify",
        "password_reset": f"{root}/auth/password/reset",
        "unsubscribe": f"{root}/v1/messaging/unsubscribe/{{token}}",
    }


def _bases(settings: Settings) -> list[tuple[str, str]]:
    return [("primary", settings.public_base_url)] + [
        ("alias", alias) for alias in settings.public_base_url_aliases
    ]


def main() -> None:
    settings = get_settings()
    print(f"API hosts served: {', '.join(settings.api_hosts)}")
    if settings.trusted_hosts:
        print(f"Host allow-list: {', '.join(settings.effective_trusted_hosts)}")
    else:
        print("Host allow-list: off (a proxy in front must check Host)")
    for kind, base in _bases(settings):
        print(f"\n[{kind}] {base}")
        for name, url in registered_urls(base).items():
            print(f"  {name:24} {url}")


if __name__ == "__main__":  # pragma: no cover - operator command
    main()
