# Production deployment

Reuse project `ecomsbd`, API `ecomsbd-api`, worker `ecomsbd-worker`, Redis
`ecomsbd-redis`, and secret group `ecomsbd-production`. Do not create duplicates.
The adjacent JSON files record the existing service shape; their compute IDs
are not proof of free allocation and must not be submitted without the check below.

Before any resource creation, compute update or activation, confirm through the
Northflank API that the account is on Developer Sandbox, the selected plan is
included, and the two free service slots/one free addon slot cover these resources.
Require one instance per service, one Redis node, no volumes, HA, autoscaling or
paid upgrade. If cost is indicated or eligibility cannot be established, stop
with `BILLING_CONFIRMATION_REQUIRED`; if the free Redis slot cannot be verified,
also report `FREE_REDIS_SLOT_UNAVAILABLE`. Never infer free coverage from plan size.

Current inventory (2026-09-14): both services have zero instances; Redis is running.
All three select `nf-compute-20`. `/plans` lists a nonzero price, and the supplied
token gets 403 from team details and billing usage. Free coverage is unverified;
no resource was created or scaled during recovery. The API has
automatic builds enabled and commit-skip flags disabled, so pushing `main` also
requires preventing an unverified charged build.

Use the existing Dockerfile with context `backend` and Dockerfile path
`/infra/docker/backend.Dockerfile`. FastAPI binds `0.0.0.0:$PORT`; public port and
probes use 8000, `/health/live` and `/health/ready`. The worker shares the API image,
runs `arq app.worker.main.WorkerSettings`, uses UTC, and has no public port.
Northflank manages process restarts. No migration runs on container boot.

The secret group supplies the existing `DATABASE_URL`, generated Redis URL,
`CREDENTIAL_ENCRYPTION_KEY`, `PHONE_SEARCH_HMAC_KEY`, and runtime configuration.
`SUPABASE_URL`, `SUPABASE_ANON_KEY`, and backend-only
`SUPABASE_SERVICE_ROLE_KEY` for final account deletion are already synced. Set business-only
`EMAIL_TRANSPORT=disabled` unless real business email credentials exist.
OTP/OAuth issuer credentials are no longer production auth requirements.
Preserve `JWT_SIGNING_KEY` for existing platform-admin token hashes only;
production seller token issuance and verification through that key are disabled.
Never include deployment-admin credentials in API/worker runtime environments.

The read-only database check confirms revision `d73e9c5a1201` is already at head;
do not rerun the migration. If a newer committed migration is introduced, run
`alembic upgrade head` once with the existing database URL securely injected.
Revision `d73e9c5a1201` denies direct business-table access to
Supabase client roles and enables RLS; internal identity mapping reuses the
existing `auth_identities` unique constraints without a schema change.

Cloudflare inventory identified the old API route as `api.scalemyprints.com/*`
on `seller-intelligence-api-production`. Preserve it until the Northflank API
is healthy, the custom domain is assigned to its HTTP port, and HTTPS succeeds.
Domain ownership is already registered with Northflank. Leave root/www,
staging and crawler resources unchanged.

R2 bucket `ecomsbd-production` is private: managed public URL disabled, no custom
domains. `R2_BUCKET`, `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID` and
`R2_SECRET_ACCESS_KEY` are already in the secret group. The previous temporary
PUT/GET/DELETE passed; recovery preserved that result without repeating it.
FCM service-account JSON is also synced and matches the ignored local original.

Supabase provider, redirect and SMTP actions are listed in
[PRODUCTION_ENV_SETUP.md](../../docs/PRODUCTION_ENV_SETUP.md#5-supabase-auth).

Production migration applied once on 2026-09-13: `d73e9c5a1201`.
The post-migration database connection passed and client-role business access was denied.

Recovery on 2026-09-14 verified the cloud secret group against the saved runtime
payload and checked that `REDIS_URL` matches the existing addon credentials.
The generated Northflank HTTPS endpoint returns 503. The custom domain still
uses the old Cloudflare Worker route, so neither DNS nor that route was changed.
Google and Apple remain disabled in Supabase. The local Google OAuth credentials
exist, but a Supabase management token or Dashboard configuration is required;
Apple Services ID/signing credentials are still absent. Do not push while the
automatic Northflank build's free coverage remains unverified.
