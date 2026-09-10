# Runbook — database restore

**Master spec section 48.** *"Do not claim backup safety unless restore has been
tested."*

This runbook is both the procedure and the record. The drill log at the bottom
is the evidence; a backup with no entry there is an untested backup.

---

## What is backed up

| | |
|---|---|
| **PostgreSQL** | `pg_dump --format=custom --compress=9`, encrypted with `age`, checksummed. `infra/backup/backup.sh`. |
| **Object storage (R2)** | Bucket versioning + lifecycle. **Not yet provisioned** — `R2_CREDENTIALS_REQUIRED`. Today payout source files and export content live in the database, so the database dump covers them. |
| **Secrets** | Not in any dump. Held in the deployment's secret store and backed up separately, by the operator, offline. A dump that could decrypt itself is not a backup, it is a breach waiting for one lost laptop. |

**RPO** 24 hours from the nightly logical dump, or the managed provider's
point-in-time window where one is configured.
**RTO** 30 minutes, **estimated**. The only measured figure is 80 s for a 229 KB
test database (see the drill log), which says nothing about production scale.
Measure it at each drill and replace this number rather than trusting it.

---

## Detection

You are here because of one of:

- the primary database is unreachable or corrupt;
- a migration or a bad deploy destroyed data;
- a restore **drill** is due (monthly minimum, section 48).

---

## Immediate containment

1. **Stop writes before restoring.** A restore into a database that is still
   taking traffic produces a copy that is wrong the moment it finishes.

   ```bash
   docker compose stop backend worker
   ```

2. Put the app into a state sellers can understand. `/health/ready` already
   fails when the database is unreachable, so the load balancer will stop
   routing; that is the honest signal.

3. **Do not delete the broken database.** It is the evidence. Rename it, or
   restore alongside it.

---

## Restore

```bash
# 1. Create the restore target. Its name MUST contain _restore, _drill,
#    _staging or _test — restore.sh refuses anything else, because pointing a
#    drill at production is the mistake that turns a rehearsal into an outage.
createdb ecomsbd_restore_$(date -u +%Y%m%d)

# 2. Restore and verify in one step.
RESTORE_URL=postgresql://ecomsbd:...@localhost:5432/ecomsbd_restore_$(date -u +%Y%m%d) \
AGE_IDENTITY=/run/secrets/backup-age-key \
infra/backup/restore.sh /var/backups/ecomsbd/ecomsbd-20260910T030000Z.dump.age
```

`restore.sh` verifies the checksum, decrypts, restores with `pg_restore -j4`,
and then runs `infra/backup/verify_restore.py` against the **restored** copy.

---

## Verification

The script checks all of this and exits non-zero on any failure:

| Check | Why it is here |
|---|---|
| Migration version is at head | A restore into an older schema silently loses whatever the newest migration added. |
| Every mapped table exists | Compared against the model registry, not a list someone has to remember to update. |
| Receivables reconcile to the ledger, per tenant | Section 81.10. Two independent derivations. If the restored copy disagrees with itself, it is not usable. |
| No receivable settled beyond what was owed | Section 17.2. |
| No customer phone is stored in clear text | A dump with readable phone numbers is an incident, not a backup. |
| Row counts for the nine tables that matter | Compare against production. A restore that is 40% of the expected size restored *something*, just not everything. |

A restore that passes `pg_restore` but fails verification **has not succeeded.**

---

## Cutover

Only after verification passes:

```bash
# 1. Point the application at the restored database.
#    Rename rather than copy: a second copy is a second thing to go stale.
psql -c "ALTER DATABASE ecomsbd RENAME TO ecomsbd_broken_$(date -u +%Y%m%d)"
psql -c "ALTER DATABASE ecomsbd_restore_$(date -u +%Y%m%d) RENAME TO ecomsbd"

# 2. Start the API first, the worker second.
docker compose up -d backend
curl -fsS https://<host>/health/ready
docker compose up -d worker
```

### After cutover, in this order

1. **Check the outbox.** Events between the dump and the failure are gone.
   Anything the worker had not dispatched is not coming back.
2. **Re-run reconciliation in shadow mode** for any payout imported after the
   dump timestamp. Shadow mode writes nothing (section 112), so it is safe to
   run before you know what is missing.
3. **Reconcile billing.** `POST /v1/admin/repairs/reconcile_subscription` per
   affected shop, or wait for the `reconcile_billing` cron. Provider truth is
   authoritative; the restored subscription rows are not.
4. **Tell affected sellers what window is missing.** A seller who re-enters
   four orders knowingly is fine. One who discovers next month that Tuesday
   vanished is not.

---

## Seller impact

| Window | What a seller sees |
|---|---|
| During restore | The app cannot reach the server. Offline order creation keeps working; the outbox drains on reconnect, which recovers some of the gap for free. |
| After restore | Everything up to the dump timestamp. Work between the dump and the failure is gone unless it is still queued on a device. |
| Money figures | Correct as of the dump. Any payout imported after it must be re-imported — the statement file is the seller's, so it can be. |

---

## Drill log

Every restore, real or rehearsed, gets a row. **An empty row here means the
backup is untested and the release is blocked.**

| Date (UTC) | Engine | Dump | Restore time | Verification | Run by | Notes |
|---|---|---|---|---|---|---|
| 2026-09-10 | PostgreSQL 16.13 | 229 KB custom-format dump of a database seeded by the full test suite | 80 s | **PASSED** | Phase F build | Dump → `pg_restore -j4` into a separate database → `verify_restore.py`. 50 tables, migration head `2ded3d35eac0`, 2 tenants' receivable/ledger invariants checked, no oversettlement, no unencrypted phone. **The drill found a real bug**: the verification script had hand-typed the collectible receivable statuses and got them wrong, so it reported a false "backup unusable". It now derives them from `ReceivableStatus.is_collectible`. A verification script that cries wolf is worse than none, because the next real failure is assumed to be another false one. |
| — | **PostgreSQL (staging, production-shaped data)** | — | — | **NOT RUN** | — | **`STAGING_RESTORE_DRILL_REQUIRED`** — a release blocker. No staging environment exists. The drill above proves the toolchain and the checks; it does **not** prove restore time or behaviour at production scale, so the 30-minute RTO stated above is still an estimate. Run the commands against staging and add the row. |

### Standing schedule

- **Monthly**, minimum (section 48). Put it in the ops calendar.
- **After every migration that alters an existing table**, because that is when
  a dump and a schema are most likely to disagree.
- **Before any release that changes the money model.**
