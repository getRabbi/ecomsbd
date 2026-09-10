#!/usr/bin/env bash
# Logical PostgreSQL backup for ecomsbd (master spec section 48).
#
# Produces a custom-format dump, which is what `pg_restore` needs to restore
# selectively and in parallel. A plain SQL dump would be simpler to read and
# far slower to restore, and restore speed is the number that matters at 3am.
#
# The dump is encrypted at rest with age, because it contains every seller's
# customer ciphertext, their phone search HMACs and their money history. An
# unencrypted dump on object storage is the same incident as a database leak.
#
# Usage:
#   DATABASE_URL=postgresql://user:pass@host:5432/db \
#   BACKUP_DIR=/var/backups/ecomsbd \
#   AGE_RECIPIENT=age1... \
#   infra/backup/backup.sh
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL is required}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/ecomsbd}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TARGET="${BACKUP_DIR}/ecomsbd-${STAMP}.dump"

mkdir -p "${BACKUP_DIR}"

echo "==> dumping to ${TARGET}"
# --no-owner / --no-acl: the restore target is a fresh database whose roles do
# not match production's, and a restore that fails on a missing role is a
# restore that does not happen.
pg_dump \
  --dbname="${DATABASE_URL}" \
  --format=custom \
  --compress=9 \
  --no-owner \
  --no-acl \
  --file="${TARGET}"

SIZE="$(wc -c < "${TARGET}")"
if [ "${SIZE}" -lt 4096 ]; then
  # A dump this small is an empty or failed one. Failing loudly here beats
  # discovering it during a restore.
  echo "ERROR: dump is only ${SIZE} bytes; refusing to treat it as a backup" >&2
  exit 1
fi
echo "==> dump complete: ${SIZE} bytes"

if [ -n "${AGE_RECIPIENT:-}" ]; then
  echo "==> encrypting"
  age --recipient "${AGE_RECIPIENT}" --output "${TARGET}.age" "${TARGET}"
  rm -f "${TARGET}"
  TARGET="${TARGET}.age"
else
  echo "WARNING: AGE_RECIPIENT is not set. The dump contains customer" >&2
  echo "         ciphertext and money history and is NOT encrypted." >&2
fi

sha256sum "${TARGET}" > "${TARGET}.sha256"
echo "==> checksum written"

if [ -n "${BACKUP_REMOTE:-}" ]; then
  echo "==> uploading to ${BACKUP_REMOTE}"
  # R2_CREDENTIALS_REQUIRED until the bucket is provisioned.
  aws s3 cp "${TARGET}" "${BACKUP_REMOTE}/" --endpoint-url "${R2_ENDPOINT_URL:?}"
  aws s3 cp "${TARGET}.sha256" "${BACKUP_REMOTE}/" --endpoint-url "${R2_ENDPOINT_URL:?}"
fi

echo "==> pruning local backups older than ${RETENTION_DAYS} days"
find "${BACKUP_DIR}" -name 'ecomsbd-*.dump*' -mtime "+${RETENTION_DAYS}" -delete

echo "==> done: ${TARGET}"
