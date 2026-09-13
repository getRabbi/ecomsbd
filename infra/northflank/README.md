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
and must be explicitly attached to the API and worker once they exist.

Link the Redis addon's private connection URL as `REDIS_URL` in this group.
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

## Domain, storage and current account blockers

Northflank currently rejects service/addon creation until a default payment
method is added. The production secret group is created, domain ownership is
verified, and the `api` subdomain is registered in Northflank. Cloudflare contains
Northflank's domain-ownership TXT record; API DNS/HTTPS cutover is still pending.

Do not replace the existing `api.scalemyprints.com` DNS record or remove its old
Worker route until the new API passes readiness. Then assign the `api` subdomain
to the API's `http` port, use Northflank's returned DNS target, complete domain
verification/HTTPS, and remove only the superseded production API Worker/route.
Leave staging, crawler, Pages, D1 and unrelated account resources untouched unless
their retirement is separately established.

Cloudflare currently rejects R2 operations with code `10042`: R2 must first be
[enabled in the dashboard](https://developers.cloudflare.com/r2/api/error-codes/).
After enablement, create/reuse private bucket `ecomsbd-production` and create
bucket-scoped S3 object read/write credentials. Inject `R2_BUCKET`,
`R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`, and `R2_SECRET_ACCESS_KEY` directly into
Northflank. No public bucket access or production credentials belong in Git.
