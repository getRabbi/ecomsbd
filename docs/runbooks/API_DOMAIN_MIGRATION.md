# Runbook — moving the API to its own domain (V4 WP7.3)

From `https://api.scalemyprints.com` to `https://api.ecomsbd.com` (or any new origin) without
breaking an app build, a provider callback or a link already sent. The server has one canonical
origin (`PUBLIC_BASE_URL`) and serves the earlier ones listed in `PUBLIC_BASE_URL_ALIASES` for as
long as anything still calls them.

## What the code does

- Every URL the API builds (OAuth redirects, webhook and callback URLs, email links, unsubscribe
  links, the website API base) comes from `PUBLIC_BASE_URL`. No API host is hard-coded in backend
  code. `python -m app.core.public_urls` prints every externally registered URL for the primary
  origin and each alias (`app/core/public_urls.py`; tests keep it equal to what each integration
  builds).
- Both hosts answer: when `TRUSTED_HOSTS` is set, the Host allow-list is `TRUSTED_HOSTS` plus the
  hosts of `PUBLIC_BASE_URL` and every alias (`Settings.effective_trusted_hosts`). An empty
  `TRUSTED_HOSTS` still means "the proxy in front checks Host" — then the proxy must route both.
- Production refuses an `http://` alias; the production check refuses an alias equal to the primary
  and reports how many API hosts are served.
- Clients: the phone app reads `ECOMSBD_API_BASE_URL` at build time (`env/production.json`); the
  Codemagic guard accepts exactly `https://api.scalemyprints.com` and `https://api.ecomsbd.com`. The
  web app reads `NEXT_PUBLIC_API_BASE_URL`; the Chrome extension `ECOMSBD_API_BASE_URL` at build time.
- Not covered by this runbook: the web/legal host (`scalemyprints.com` pages, the app's
  `webDashboardHost`, privacy-policy links) and Supabase Site URL — a separate web-domain move.

## Order of work (dual-serve period)

1. **DNS + TLS.** Point `api.ecomsbd.com` at the same API service as the old host (Cloudflare →
   Northflank, `infra/northflank/README.md`); a valid certificate for the new host. Check
   `curl https://api.ecomsbd.com/health/live` → `{"status":"ok"}` while the old host still serves.
2. **Server config (API + worker secret group).** `PUBLIC_BASE_URL=https://api.ecomsbd.com`,
   `PUBLIC_BASE_URL_ALIASES=https://api.scalemyprints.com`; add `api.ecomsbd.com` to `TRUSTED_HOSTS`
   if it is set (the code adds both API hosts anyway). If a proxy checks Host, add the new host
   there. Run `python -m app.check_production_config` → `URLS / CORS` OK with "2 API host(s)".
   Deploy; both hosts answer.
3. **Provider registrations — add the new URL, keep the old one.** From
   `python -m app.core.public_urls`:
   - Meta app: Facebook Login → Valid OAuth Redirect URIs: add `meta_oauth_redirect` for the new
     origin (keep the old one until step 6); Webhooks (Page + WhatsApp Business Account): change the
     callback to the new `meta_webhook` (Meta holds one callback per object; the old host keeps
     serving anything in flight), same verify token.
   - Google / Supabase OAuth: add any redirect that names the API host (today the API host is not a
     Google redirect; Supabase redirects name the web host).
   - Shopify / WooCommerce (launch-gated today): add the new `shopify_oauth_redirect`. Existing
     Shopify webhooks are re-registered at the new `store_webhook` URL by the hourly health check
     (it compares the address); WooCommerce webhooks are checked by id only and keep the old URL
     until the store is reconnected — reconnect each before the old host is retired.
   - Courier callbacks (`courier_webhook`): sellers pasted these into courier panels. They keep
     working on the old host; ask each seller with a callback to re-paste it from Couriers → Settings
     (the app shows the new URL) before the old host is retired.
   - Emails already sent (verify, reset, unsubscribe) carry the old host; they expire within days,
     unsubscribe links do not — the old host must answer them for as long as it exists.
4. **Apps.** Phone: set `ECOMSBD_API_BASE_URL=https://api.ecomsbd.com` in `env/production.json`,
   bump the version, release (the Codemagic guard accepts it). Web: `NEXT_PUBLIC_API_BASE_URL` →
   redeploy. Extension: rebuild with `ECOMSBD_API_BASE_URL`, publish an update.
5. **Wait.** Old phone builds keep calling the old host until they update. Watch the share of
   requests on the old host (proxy / Cloudflare analytics by Host) and the app versions in use. When
   the old host's traffic is only stragglers, make the new build the minimum: Play Console in-app
   update priority / "required" release, App Store phased release complete. (The API has no
   min-version gate today; adding one is a separate change.)
6. **Retire.** Only when old-host traffic is ~0 for 30 days and no courier callback still uses it:
   remove the old Meta redirect, remove `PUBLIC_BASE_URL_ALIASES`, keep the old DNS name redirecting
   (301) to the new one for unsubscribe links, deploy.

## Detection

- `python -m app.check_production_config` → `URLS / CORS`.
- 400 "Invalid host header" on either host: the host is missing from `TRUSTED_HOSTS` / the alias list
  or the proxy.
- Meta: webhook deliveries failing in the Meta app dashboard; Integrations Hub `WEBHOOK_FAILING`.
- OAuth "URL blocked" / `redirect_uri` mismatch on connect: the new redirect is not registered (step 3).

## Rollback

Set `PUBLIC_BASE_URL` back to the old origin and remove the alias (or swap them); deploy. Nothing
is stored with a host in it except provider-side registrations (Meta webhook callback — set it back;
Shopify webhooks re-register on the next health check) and URLs sellers pasted, which work on both
hosts while both are served.

## Verification (staging first)

1. Staging with `PUBLIC_BASE_URL=<new staging origin>` and the old one as alias: `GET /health/live` on
   both hosts → 200; another Host → 400.
2. `python -m app.core.public_urls` lists both bases; register the new Meta redirect + webhook on the
   staging Meta app; connect a test Page (`docs/CHAT_TO_ORDER.md`) and receive one message.
3. A phone build against the new origin signs in, loads orders, books nothing new by itself.
