# V3 cloud-session handoff

Short state note for the next session. Git is the source of truth; check it first.

## Where things are

- Completed phases on `main`:
  - **V3.1 Integrations Hub**: merge commit `99dd98e` (PR #1).
  - **V3.2 advanced two-way sync**: merge commit `5eef579` (PR #2).
  - **Security: `alembic_version` client-role revoke** (`a32002`): merge commit `049ee3e` (PR #3).
  - **V3.3 Messaging & Campaigns** (`a33001`): see `docs/V3_3_MESSAGING_CAMPAIGNS.md`.
- Every PR passed full CI before merging: backend lint, types, SQLite and **PostgreSQL**
  tests, PostgreSQL migration-from-empty, dependency audit, Flutter, secret scan.
- Work from **`main`**. `v3.1-integrations` and `v3.2-sync` are merged; new work goes on a new branch.
- No release tag has been created for V3.1 or V3.2.
- Migration head: **`a33001`**, one head, no branches (`a31001` = V3.1, `a32001` = V3.2,
  `a32002` = client roles lose access to `alembic_version`, `a33001` = V3.3).

## Production

- Prod DB is at `a32001` (V3.2); API and worker healthy on it.
- Northflank auto-deploys `main`. `/health/ready` needs DB == code head, so a new image
  stays unready and the old container keeps serving until prod is migrated to its head.
- Pending on prod: `a32002` (privileges only: REVOKE + ENABLE RLS on `alembic_version`) and
  `a33001` (two new tables; all other changes are additive nullable or defaulted columns). Apply
  through the session pooler:
  `DATABASE_URL='postgresql+asyncpg://...5432/postgres' alembic -x database-only=true upgrade head`.
  Cloud sessions have no production credentials; the owner runs it.
- The worker gains `run_campaigns` (every minute). It does nothing until a campaign exists.

## External gates (code is done, these are not)

- Shopify: `SHOPIFY_CLIENT_ID` / `SHOPIFY_CLIENT_SECRET` from an approved Shopify app,
  protected-customer-data access, app redirect and compliance webhook URLs.
  V3.2 features ask for extra scopes and prompt a reconnect.
- Meta/Messenger: `META_APP_ID`, `META_APP_SECRET`, `META_WEBHOOK_VERIFY_TOKEN` and
  App Review (`pages_messaging`). Messenger stays health only; marketing needs Meta's
  per-person marketing opt-in (not built).
- WhatsApp (V3.3): the same three Meta settings, plus each shop's own WABA (phone number ID,
  WABA ID, system-user token) subscribed to the ecomsbd Meta app. Embedded Signup / Tech
  Provider onboarding is not built. Only Meta-approved templates are sent; Meta bills
  marketing templates to the shop.
- Email receipts (V3.3): `EMAIL_WEBHOOK_SECRET` (Resend `whsec_…`) and a Resend webhook to
  `/v1/webhooks/messaging/resend`. Open tracking on the Resend domain is optional.
- WooCommerce: verified against a fake store in tests; **live-store verification pending**.

## Next phase

- Not defined in the repo after V3.3. Agree the next phase with the owner before starting.

## Invariants

- Reuse the V2/V3 foundations. Do not build a second CRM, public API, webhook system,
  automation engine, order domain or integration model.
- The financial ledger and the stock ledger stay authoritative. Integrations never
  overwrite them: stock changes are ledger movements, and ambiguity becomes a conflict.
- Every outbox topic needs a handler, or its events block the head of the outbox.
- Tenant isolation, encrypted provider credentials, server-side RBAC, idempotency keys.
- Official provider APIs only; no invented provider statuses or tracking links.

## Local working tree

- `apps/web/.gitignore` has an unrelated local change owned by the user.
  Do not commit, revert or reformat it.
- Root `.env.*` files, `backend/.env`, `apps/mobile_flutter/env/production.json`,
  keystore/Firebase/OAuth JSON files are local secrets/artifacts, ignored on purpose.
