# Runbook — provider outage

**Master spec sections 45, 49, 75.** A provider being down must degrade *the
action that needs it*, never the app. A seller whose courier API is unreachable
should still be able to read their money screen, record a dispatch by hand and
close their day.

Applies to: couriers, SMS, push, billing providers, the risk data source.

---

## Detection

| Signal | Where |
|---|---|
| `provider_health.state` becomes `DEGRADED` or `DOWN` | `GET /v1/admin/ops/provider-health` |
| Breaker opened | same, `breaker_state = OPEN` |
| `ops.provider_health_changed` audit entries clustering | `GET /v1/admin/ops/audit?action=ops.provider_health_changed` |
| Seller reports "booking keeps failing" | support |

Health is derived from a *pattern*, not one request: five consecutive failures
open the breaker, two mark `DEGRADED`. A single timeout changes nothing, which
is deliberate — see `app/common/provider_health.py`.

**`NEEDS_RECONNECT` is not an outage.** It means the provider rejected the
credentials, which will not fix itself. Go to the credential section below.

---

## Immediate containment

1. **Confirm the scope.** Health is per `(provider, capability, scope)`.

   ```bash
   curl -H "X-Admin-Token: $ADMIN_TOKEN" \
     "$API/v1/admin/ops/provider-health"
   ```

   A row with a tenant scope is **one shop's problem** — almost always their
   own credentials. A platform-scoped row is the provider.

2. **Turn the capability off** rather than letting every request queue behind a
   timeout. This is what the flag exists for (section 45): a provider outage
   must be survivable without shipping an app version.

   ```bash
   curl -X POST -H "X-Admin-Token: $ADMIN_TOKEN" \
     "$API/v1/admin/repairs/disable_provider_capability" \
     -d '{"reason":"pathao returning 500s platform-wide since 14:05",
          "params":{"flag":"pathao_enabled","enabled":false}}'
   ```

3. Check what the client does next. `GET /v1/couriers/providers` returns
   capabilities and flag state, and the UI reacts to capability rather than to
   the provider's name — so a disabled courier renders as manual mode, not as a
   broken button.

---

## Feature flags

| Flag | Turns off |
|---|---|
| `steadfast_enabled` / `pathao_enabled` / `redx_enabled` | One courier. Manual dispatch stays available and is unaffected — it has no flag, deliberately, because it is what everything else degrades *to*. |
| `play_billing_enabled` / `bkash_web_billing_enabled` | One billing channel. `GET /v1/billing/channel` stops offering it and the client shows "not available right now". |
| `risk_provider_enabled` | The risk check. Order cards read "Not checked". |
| `ai_parse_enabled` | The AI parse layer. The deterministic parser is unaffected. |

---

## Data queries

```sql
-- Who is affected, and how badly.
SELECT provider, capability, scope_key, state, breaker_state,
       consecutive_failures, last_error_code, last_success_at
  FROM provider_health
 WHERE state <> 'HEALTHY'
 ORDER BY consecutive_failures DESC;

-- Work that piled up while the provider was gone.
SELECT topic, status, COUNT(*), MIN(created_at) AS oldest
  FROM outbox_events
 WHERE status IN ('PENDING', 'FAILED', 'DEAD')
 GROUP BY topic, status;
```

---

## Seller impact

| Capability down | What still works |
|---|---|
| Courier booking | Everything else. Manual dispatch records the parcel; the money core runs on it and does not care whether a provider was involved. |
| Courier status | Parcels stay in their last known state. The alerts still fire from the shop's own data. |
| Payout API | Statement upload and manual entry, which are the working paths anyway. |
| Push | Nothing is lost. The notification centre holds everything; push is a second copy (section 94). |
| SMS | Order confirmations are not sent. The seller can see which ones did not go: `notification_deliveries` with `state <> 'SENT'`. |
| Billing | Existing subscriptions are unaffected. New purchases are refused with a clear reason; nothing is granted unverified. |

---

## Recovery

1. Confirm the provider is actually back — from their status page or a manual
   call, not from our own breaker, which is closed only because it timed out.
2. Reset the breaker so the next request is attempted:

   ```bash
   curl -X POST -H "X-Admin-Token: $ADMIN_TOKEN" \
     "$API/v1/admin/repairs/refresh_provider_health" \
     -d '{"reason":"pathao status page reports resolved at 16:20",
          "params":{"provider":"pathao"}}'
   ```

3. Re-enable the flag with the same repair action, `"enabled": true`.
4. Drain the backlog. The outbox dispatcher runs every 15 seconds; dead-lettered
   events need `retry_outbox_event` per event, which is deliberate — an event
   that failed twelve times should be looked at, not blindly requeued.

---

## Verification

- `provider_health` returns to `HEALTHY` after real successes, not after a reset.
  A reset sets `UNKNOWN`; only a successful call sets `HEALTHY`.
- The outbox backlog returns to near zero.
- No new `ops.provider_health_changed` entries for that provider for 30 minutes.
- Spot-check one seller's affected action end to end.

---

## Credential failures (`NEEDS_RECONNECT`)

Different problem, different fix. The provider is up; *this shop's* credentials
were rejected.

1. Confirm it is scoped to one tenant. If every tenant shows
   `NEEDS_RECONNECT`, the provider changed something and it is an outage after
   all.
2. Ask the seller to reconnect the account in the app. **Never ask for their
   API key over chat** — section 102 is explicit, and support has no route that
   would accept one.
3. If the credential is believed compromised, follow
   [CREDENTIAL_LEAK.md](CREDENTIAL_LEAK.md).
