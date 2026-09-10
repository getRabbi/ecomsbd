# Runbook — credential leak

**Master spec section 47.** A secret has been exposed: committed to a
repository, pasted into a chat, screenshotted by a seller, or found in a log.

Order matters. **Revoke first, investigate second.** Every minute spent working
out how it leaked is a minute the credential still works.

---

## Which credential?

| Leaked | Blast radius | Revoke by |
|---|---|---|
| `CREDENTIAL_ENCRYPTION_KEY` | Every stored courier and billing credential across every shop. **The worst case.** | Rotate the key, re-encrypt with a new key version. |
| `JWT_SIGNING_KEY` | Every session, and every refresh-token and IP hash derived from it. | Rotate. Every session dies, which is the intended effect. |
| `PHONE_SEARCH_HMAC_KEY` | Lets an attacker confirm whether a given number is a customer, given database access. | Rotate and recompute the search hashes. |
| `OTP_HASH_SECRET` | Lets an attacker verify OTP guesses offline, given the challenge rows. | Rotate. In-flight challenges become unusable, which is acceptable. |
| A seller's courier API key | That one shop's courier account. | The seller re-issues it at the courier and reconnects. |
| `ADMIN_API_TOKENS` entry | The ops console, at `SUPERADMIN`. | Remove from configuration and redeploy; provision per-operator rows instead. |
| A `platform_admins` token | The ops console at that row's role. | Set `revoked_at`. Takes effect on the next request. |
| Play service account / bKash merchant secret | Billing verification. | Rotate in the provider console. |

---

## Immediate containment

### Any admin token

```sql
-- Effective on the next request; the token is checked against this row
-- on every call.
UPDATE platform_admins
   SET revoked_at = now(),
       revoked_reason = 'leaked: pasted into a shared channel 2026-09-10'
 WHERE id = '<admin-id>';
```

A bootstrap token from `ADMIN_API_TOKENS` cannot be revoked in the database —
it lives in configuration. Remove it and redeploy. That is a reason to provision
per-operator rows and keep the bootstrap list empty in normal operation.

### Any application secret

1. Generate a replacement:

   ```bash
   python -c "import secrets;print(secrets.token_urlsafe(48))"          # text secrets
   python -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())"  # AES key
   ```

2. Deploy the new value. `Settings` fails fast on a placeholder, so a
   half-applied rotation refuses to boot rather than running insecurely.

3. **`CREDENTIAL_ENCRYPTION_KEY` needs a re-encryption pass, not just a swap.**
   Envelopes carry a key version (`v1|nonce|ciphertext`), so old and new
   coexist: deploy the new key, re-encrypt every stored credential, then retire
   the old version. Skipping the re-encryption leaves everything readable with
   the leaked key.

### A seller's courier credential

```bash
curl -X POST -H "X-Admin-Token: $ADMIN_TOKEN" \
  "$API/v1/admin/repairs/revoke_courier_credential" \
  -d '{"reason":"seller posted a screenshot containing their API key",
       "tenant_id":"<tenant>"}'
```

> Today this returns `UNAVAILABLE`: no courier credential storage exists yet
> (Phase C is blocked). That is deliberate — an honest refusal beats a silent
> no-op that leaves an operator believing a leaked key was revoked. Until then,
> the seller must re-issue the key at the courier's portal, which revokes the
> leaked one at the source. Tell them to do that, and confirm they have.

---

## Investigate

Only after revocation.

```bash
# Did it ever reach the repository?
gitleaks detect --source . --config .gitleaks.toml

# Which admin token was used, and for what?
```

```sql
SELECT action, actor_label, entity_type, entity_id, reason, created_at
  FROM audit_logs
 WHERE action LIKE 'admin.%'
   AND created_at > now() - interval '30 days'
 ORDER BY created_at DESC;

-- Was any PII read while the credential was live?
SELECT tenant_id, entity_id, reason, actor_label, created_at
  FROM audit_logs
 WHERE action = 'privacy.phone_revealed'
    OR action = 'admin.pii_revealed'
 ORDER BY created_at DESC;

-- Was anything exported?
SELECT tenant_id, entity_id, context, created_at
  FROM audit_logs
 WHERE action IN ('data.export_requested', 'data.export_downloaded')
   AND created_at > now() - interval '30 days'
 ORDER BY created_at DESC;
```

Every sensitive admin action is audited, which is what makes this answerable
rather than a guess.

---

## Seller impact

| Rotated | What sellers experience |
|---|---|
| `JWT_SIGNING_KEY` | Everyone is signed out. Offline data survives; the outbox drains after they sign in again. Tell them beforehand if you can. |
| `CREDENTIAL_ENCRYPTION_KEY` | Nothing, if the re-encryption pass completes. Courier features break if it does not, which is why the pass is not optional. |
| `PHONE_SEARCH_HMAC_KEY` | Customer lookup by number fails until the hashes are recomputed. Run it as one migration, not lazily. |
| A courier credential | That shop cannot book until they reconnect. Everything else, including manual mode, is unaffected. |

---

## Verification

- The old credential is confirmed rejected — test it, do not assume.
- `gitleaks` is clean on the full history.
- No `admin.*` audit entries attributable to the leaked token after revocation.
- If PII may have been read: identify which shops, and follow the disclosure
  obligation. Do not decide alone that nobody needs to be told.
- A support case records what leaked, when, what was revoked and what was
  accessed. Next time starts from that, not from memory.

---

## Prevention, checked in

- `gitleaks` runs on every CI build over the full history, and CI refuses a
  committed `backend/.env`.
- `Settings` refuses to start a deployed environment with a placeholder secret.
- Logs, audit rows and Sentry events are redacted centrally in the formatter,
  so a new module cannot leak a secret by forgetting to scrub it.
- The admin console never loads a credential, so it cannot display one.
- Support has no route that accepts a courier password (section 102).
