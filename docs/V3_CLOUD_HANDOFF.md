# V3 cloud-session handoff

Short state note for the next session. Git is the source of truth; check it first.

## Where things are

- Completed phase: **V3.1 Integrations Hub**, merged to `main` as `99dd98e` (PR #1).
- Current branch: **`v3.2-sync`** (V3.2 advanced two-way sync). PR #2 `v3.2-sync → main`
  is open and **not merged**; merge only with explicit approval.
- V3.2 status: code complete. Local SQLite suites, web build and mobile tests pass.
  The PostgreSQL run is PR #2's CI; read `gh pr checks 2` before trusting it.
- Latest V3.2 commit: `git log -1 origin/v3.2-sync` (the commit adding this file).
- Migration head: **`a32001`**, one head, no branches (`a31001` = V3.1, `a32001` = V3.2).

## Production

- Prod DB was last migrated to `a23007` (V2.3). **Not migrated to `a31001`/`a32001`.**
- Northflank auto-deploys `main`. `/health/ready` needs DB == code head, so the V3.1
  image stays unready and the old container keeps serving until prod is migrated.
  Migrating prod needs the owner's explicit approval; this session did not do it.

## External gates (code is done, these are not)

- Shopify: `SHOPIFY_CLIENT_ID` / `SHOPIFY_CLIENT_SECRET` from an approved Shopify app,
  protected-customer-data access, app redirect and compliance webhook URLs.
  V3.2 features ask for extra scopes and prompt a reconnect.
- Meta/Messenger: `META_APP_ID`, `META_APP_SECRET`, `META_WEBHOOK_VERIFY_TOKEN` and
  App Review (`pages_messaging`). V2 messaging has no Messenger channel: health only.
- WooCommerce: verified against a fake store in tests; **live-store verification pending**.

## Next phase (after V3.2 merges)

- **V3.3 Messaging & Campaigns.**

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
