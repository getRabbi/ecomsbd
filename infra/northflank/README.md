# Production deployment

Project: `ecomsbd`; repository: `getRabbi/ecomsbd`; branch: `main`.
Public API: `https://api.scalemyprints.com`.
Android application ID: `com.smply.app` (release signing is unchanged).

## Resources

Reuse existing resources by name before creating anything. The adjacent JSON
files are non-secret Northflank API request bodies:

| File | POST endpoint | Purpose |
| --- | --- | --- |
| `redis.json` | `/v1/projects/ecomsbd/addons` | One private Redis addon |
| `api.json` | `/v1/projects/ecomsbd/services/combined` | Build GitHub `main` and run FastAPI |
| `worker.json` | `/v1/projects/ecomsbd/services/deployment` | Run ARQ from the API's image |

Services intentionally start with **zero instances** until runtime configuration
and the controlled migration are complete. Then scale each to one instance.
Keep automatic builds/deployments linked to `main`; the worker follows the
API build service. Verify these settings in Northflank after resource creation.
Northflank manages restarts; only the API exposes a public HTTP port.
The API uses Northflank's default build plan. Redis uses the supported `7.2.14`
version and the platform's minimum `4096` MB storage allocation.

The build context is `backend`, with Dockerfile
`/infra/docker/backend.Dockerfile`. Local equivalent, from the repository root:

```bash
docker build -f infra/docker/backend.Dockerfile -t ecomsbd-backend:production backend
```

The API binds `0.0.0.0:$PORT` (configured as `8000`). Probes use curl inside the
container to access `/health/live` and `/health/ready` on `127.0.0.1:8000`, which
preserves the production Host allow-list without trusting arbitrary pod IPs.
Keep the probe port, public port and `PORT` aligned if changing them.
The worker command is `arq app.worker.main.WorkerSettings`;
it has no public port or HTTP probe.

## Secrets and migration

Reuse `ecomsbd-production`. Nonblank runtime settings and the database URL have
been copied from the ignored local production configuration. Four cryptographic
secrets were generated directly in Northflank; preserve them on subsequent runs.
Deployment-admin tokens are **not** runtime variables. The group is restricted
and is attached to the API and worker.

The Redis addon's `REDIS_MASTER_URL` is linked as `REDIS_URL` in this group;
Northflank manages the connection secret rather than a copied static value.
Keep the following flags; missing provider credentials must not be replaced with
fake values or bypassed by disabling the production checks:

```dotenv
EMAIL_PASSWORD_AUTH_ENABLED=true
GOOGLE_AUTH_ENABLED=true
APPLE_AUTH_ENABLED=true
PHONE_OTP_LOGIN_ENABLED=false
STEADFAST_WEBHOOK_ENABLED=false
```

Run migrations once per release from the same image as a controlled one-off job,
with `DATABASE_URL` injected securely. Use `postgresql+asyncpg://` and TLS for
Supabase; never pass the connection string on the command line or log it.

```bash
alembic -x database-only=true upgrade head
```

The explicit database-only mode validates the PostgreSQL driver and does not
load unrelated OAuth/email settings. Normal API/worker startup still applies
all production configuration checks. Migrations never run on container boot.
For a local one-off container, with `DATABASE_URL` already securely exported:

```bash
docker run --rm --env DATABASE_URL ecomsbd-backend:production alembic -x database-only=true upgrade head
```

Deployment checkpoint (2026-09-13): the supplied Supabase database was confirmed
empty, then migrated once from the built production container over TLS. Alembic
head is `c41f8b2ad7e5`; the post-migration connection check succeeded. No migration
version files were changed. Container build, non-root/start-command inspection,
Android `:app:preDebugBuild` configuration check, package-reference consistency,
and focused migration-environment Ruff checks passed. No full test suites ran.

## Domain, storage and remaining credentials

Northflank billing is now configured. `ecomsbd-api`, `ecomsbd-worker` and
`ecomsbd-redis` are created; the GitHub `main` backend build succeeded and both
services follow that image. Redis is running, and both services' effective
environments contain its linked URL and the existing cryptographic secrets.
Deployment-admin tokens remain excluded. No migration or full test suite was
rerun during this continuation.

API and worker remain at zero instances until the real provider credentials
below are supplied. The production validator rejects the incomplete settings;
the auth flags and safety checks have not been disabled. Both API health probes
currently return `503`, so no traffic cutover has been made.

Domain ownership is verified and the `api` subdomain is registered in Northflank.
Cloudflare contains Northflank's ownership TXT record. Subdomain verification,
service assignment and API DNS/HTTPS cutover remain pending readiness.
The exact generated API hostname is also in the Host allow-list, so it can be
used for pre-cutover health checks without allowing arbitrary hosts.

Do not replace the existing `api.scalemyprints.com` DNS record or remove its old
Worker route until the new API passes readiness. Then coordinate the cutover:
use Northflank's returned DNS target, verify the `api` subdomain, assign it to
the API's `http` port, and confirm custom-domain HTTPS and health. Northflank
refuses service assignment before subdomain verification. Remove only the
superseded production API Worker/route after the replacement is healthy.
Leave staging, crawler, Pages, D1 and unrelated account resources untouched unless
their retirement is separately established.

R2 is enabled and private bucket `ecomsbd-production` is created. Its public
development URL is disabled and it has no custom domains. `R2_BUCKET` and
`R2_ENDPOINT_URL` are stored in Northflank. Cloudflare rejected scoped credential
creation with `403` / `9109`: the deployment token lacks token-creation access.
Either grant [Account API Tokens Write](https://developers.cloudflare.com/api/resources/accounts/subresources/tokens/methods/create/)
or create [R2 Object Read & Write credentials](https://developers.cloudflare.com/r2/api/tokens/)
scoped only to this bucket and supply the two keys directly to Northflank.
Never substitute the deployment-admin token as a runtime storage credential.

Remaining external runtime values:

- `GOOGLE_CLIENT_ID_WEB` (the server audience; register Android with `com.smply.app`)
- `APPLE_CLIENT_ID`
- `EMAIL_API_KEY`
- `EMAIL_FROM_ADDRESS`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
