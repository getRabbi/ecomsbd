"""Own API domain (V4 WP7.3): one configurable origin, earlier ones still served."""

from __future__ import annotations

import types
import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.check_production_config import Status, _check_urls
from app.core.config import AppEnv, Settings
from app.core.public_urls import main, registered_urls
from tests.integrations_fakes import live_settings
from tests.test_config import production_settings

NEW = "https://api.ecomsbd.com"
OLD = "https://api.scalemyprints.com"


def _local(**values: Any) -> Settings:
    return Settings(_env_file=None, app_env=AppEnv.LOCAL, **values)  # type: ignore[call-arg]


def test_aliases_read_as_a_comma_list_or_json_and_default_empty() -> None:
    assert _local().public_base_url_aliases == []
    assert _local(public_base_url_aliases="").public_base_url_aliases == []
    listed = _local(public_base_url_aliases=f"{OLD}, https://api.old.example")
    assert listed.public_base_url_aliases == [OLD, "https://api.old.example"]
    assert _local(public_base_url_aliases=f'["{OLD}"]').public_base_url_aliases == [OLD]


def test_production_refuses_an_alias_without_https() -> None:
    with pytest.raises(ValueError, match="PUBLIC_BASE_URL_ALIASES must use https"):
        production_settings(public_base_url=NEW, public_base_url_aliases=["http://api.old"])
    ok = production_settings(public_base_url=NEW, public_base_url_aliases=[OLD])
    assert ok.api_hosts == ["api.ecomsbd.com", "api.scalemyprints.com"]


def test_the_host_allow_list_adds_the_apis_own_hosts_only_when_one_is_set() -> None:
    moving = _local(
        public_base_url=NEW, public_base_url_aliases=[OLD], trusted_hosts=["admin.ecomsbd.com"]
    )
    assert moving.effective_trusted_hosts == [
        "admin.ecomsbd.com",
        "api.ecomsbd.com",
        "api.scalemyprints.com",
    ]
    # Listed already: not twice.
    listed = _local(public_base_url=NEW, trusted_hosts=["api.ecomsbd.com"])
    assert listed.effective_trusted_hosts == ["api.ecomsbd.com"]
    # Empty means a proxy in front checks Host; that stays exactly as it was.
    assert _local(public_base_url=NEW, public_base_url_aliases=[OLD]).effective_trusted_hosts == []


async def test_both_domains_are_served_during_the_move_and_nothing_else() -> None:
    from app.main import create_app

    settings = _local(
        public_base_url=NEW, public_base_url_aliases=[OLD], trusted_hosts=["api.ecomsbd.com"]
    )
    app = create_app(settings)
    transport = ASGITransport(app=app)
    for origin, expected in ((NEW, 200), (OLD, 200), ("https://api.evil.example", 400)):
        async with AsyncClient(transport=transport, base_url=origin) as client:
            response = await client.get("/health/live")
        assert response.status_code == expected, origin


def test_the_url_list_matches_what_each_integration_builds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.integrations import custom_website, meta, service, shopify, woocommerce

    monkeypatch.setattr(live_settings(), "public_base_url", f"{NEW}/")
    urls = registered_urls(NEW + "/")
    assert urls["meta_oauth_redirect"] == meta.redirect_uri()
    assert urls["shopify_oauth_redirect"] == shopify.redirect_uri()
    assert urls["woocommerce_callback"] == woocommerce.callback_url()
    assert urls["website_api"] == custom_website.api_base_url()
    conn = types.SimpleNamespace(
        provider="SHOPIFY", webhook_token="tok", id=uuid.uuid4(), config={}
    )
    assert service.webhook_url(conn) == urls["store_webhook"].format(
        provider="shopify", token="tok"
    )  # type: ignore[arg-type]
    returned = service.return_url(conn, "ok")  # type: ignore[arg-type]
    if not (live_settings().public_web_url or ""):
        assert returned.startswith(urls["integration_return"])
    assert all(url.startswith(f"{NEW}/") for url in urls.values())
    assert urls["meta_webhook"] == f"{NEW}/v1/webhooks/integrations/meta"


def test_the_production_check_counts_the_hosts_and_refuses_a_repeated_primary() -> None:
    moving = production_settings(
        public_base_url=NEW, public_base_url_aliases=[OLD], trusted_hosts=["api.ecomsbd.com"]
    )
    result = _check_urls(moving)
    assert result.status == Status.OK
    assert "2 API host(s)" in result.detail
    assert "ecomsbd.com" not in result.detail, "the check prints no configured value"
    repeated = production_settings(
        public_base_url=NEW, public_base_url_aliases=[NEW], trusted_hosts=["api.ecomsbd.com"]
    )
    assert _check_urls(repeated).status == Status.BLOCKING


def test_the_operator_command_prints_every_base_and_no_secret(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = live_settings()
    monkeypatch.setattr(settings, "public_base_url", NEW)
    monkeypatch.setattr(settings, "public_base_url_aliases", [OLD])
    monkeypatch.setattr(settings, "trusted_hosts", ["api.ecomsbd.com"])
    main()
    out = capsys.readouterr().out
    assert "API hosts served: api.ecomsbd.com, api.scalemyprints.com" in out
    assert f"[primary] {NEW}" in out and f"[alias] {OLD}" in out
    assert f"{OLD}/v1/webhooks/couriers/{{provider}}/{{token}}" in out
    assert "Host allow-list: api.ecomsbd.com, api.scalemyprints.com" in out
