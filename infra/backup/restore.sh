#!/usr/bin/env bash
# Restore an ecomsbd backup into a *separate* database, then verify it.
#
# The verification step is the point. Master spec section 48: "do not claim
# backup safety unless restore has been tested." A dump that restores without
# error but is missing a day of ledger entries is a failed backup that looks
# like a successful one, so `verify_restore.py` re-checks the money invariants
# on the restored copy before this script reports success.
#
# It refuses to restore over a database whose name does not look like a restore
# target. Pointing a drill at production is the one mistake that turns a
# rehearsal into an outage.
#
# Usage:
#   RESTORE_URL=postgresql://user:pass@host:5432/ecomsbd_restore_20260910 \
#   infra/backup/restore.sh /var/backups/ecomsbd/ecomsbd-20260910T030000Z.dump.age
set -euo pipefail

DUMP="${1:?usage: restore.sh <dump-file>}"
: "${RESTORE_URL:?RESTORE_URL is required}"

case "${RESTORE_URL}" in
  *_restore*|*_drill*|*_staging*|*_test*) : ;;
  *)
    echo "ERROR: RESTORE_URL must name a restore target " >&2
    echo "       (its database name must contain _restore, _drill, _staging or _test)." >&2
    echo "       Refusing to restore over '${RESTORE_URL##*/}'." >&2
    exit 2
    ;;
esac

if [ -f "${DUMP}.sha256" ]; then
  echo "==> verifying checksum"
  sha256sum --check "${DUMP}.sha256"
fi

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

PLAIN="${DUMP}"
if [ "${DUMP%.age}" != "${DUMP}" ]; then
  echo "==> decrypting"
  : "${AGE_IDENTITY:?AGE_IDENTITY is required to decrypt this backup}"
  PLAIN="${WORK}/restore.dump"
  age --decrypt --identity "${AGE_IDENTITY}" --output "${PLAIN}" "${DUMP}"
fi

echo "==> restoring into ${RESTORE_URL##*@}"
START="$(date -u +%s)"
pg_restore \
  --dbname="${RESTORE_URL}" \
  --clean --if-exists \
  --no-owner --no-acl \
  --jobs=4 \
  "${PLAIN}"
ELAPSED="$(( $(date -u +%s) - START ))"
echo "==> restore finished in ${ELAPSED}s"

echo "==> verifying the restored database"
DATABASE_URL="${RESTORE_URL}" python infra/backup/verify_restore.py

echo "==> RESTORE DRILL PASSED (${ELAPSED}s)"
echo "    Record the date, the dump timestamp and this duration in"
echo "    docs/runbooks/DATABASE_RESTORE.md."
