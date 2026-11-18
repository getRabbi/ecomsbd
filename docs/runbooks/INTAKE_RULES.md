# Runbook: intake rules config (V4 WP1.2)

Linked phones (and, from Phase 5, the Chrome extension) decide **on the device**
whether a message looks like an order (rule L4). What they look for comes from one
global, versioned config, `intake_rule_sets` (rule L5), so a change at
Meta/WhatsApp is fixed by publishing a new version, not by an app release.

- Exactly one version is **active**. Version 1 is seeded by migration `a41001`.
- A device fetches `GET /v1/intake/rules` with its ingest key. The answer is the
  active payload, the district list it references (`bd_districts_64`, served from
  the parser's gazetteer), and this shop's `enabled_packages` / `covered_packages`.
  The ETag is the SHA-256 of the body. Devices send `If-None-Match` every
  `refresh_hours` (24 in v1) and get `304` while nothing changed.
- A package is read when it is in the active rules, is **not** in the shop's
  `covered_packages` (an official connection delivers it; set by the server only,
  Phase 6), and the seller's toggle — or else `default_enabled` — says so.
  Sellers change toggles with `PATCH /v1/intake/settings` (`settings.manage`).

## Flag

| Switch | Where | Default |
|---|---|---|
| `intake_device_enabled` | `feature_flags` row (global or per shop), admin console | **off** |

With the flag off, devices get `FEATURE_DISABLED` and keep their cached rules.
The admin routes work regardless, so a version can be prepared before the flag
is turned on.

## What a version contains (schema 1)

`packages[]` (`package`, `label`, `default_enabled`), `phone` (portable regex
`patterns`, `national_pattern`, `country_prefix`, `required: true`), `intent`
keywords (`bn`, `banglish`, `en`), `address` keywords (`bn`, `latin`) +
`district_list`, `matching` (NFC, case-insensitive, latin whole word, Bangla
substring), `score` (`weights`, `max_hits`, `threshold`), `buffer_window_minutes`,
`refresh_hours`, `extension.hosts` (`business.facebook.com`, `www.messenger.com`,
`web.whatsapp.com`) → `selectors` + `verified_at`.

The server refuses a version when:

- a regex uses `\d`, `\w`, `\s`, `\b`, inline flags or named groups (they differ
  between Java, JavaScript and Python), does not compile, or matches empty text;
- the threshold can never be reached, or a phone number alone reaches it;
- a selector is set without `verified_at` (unverified selectors are not shipped);
- a package is listed twice, a field is unknown, or the payload is over 64 KiB.

Keywords are stored NFC-normalised, lower-cased and de-duplicated.

## Publish, activate, roll back

All routes take `X-Admin-Token`. Listing needs `OPS_READ`; publishing and
activating need `FEATURE_FLAG_WRITE` (OPS, SUPERADMIN).

1. `GET /v1/admin/intake-rules` — copy the active version's `payload`.
2. Edit it, then `POST /v1/admin/intake-rules` with
   `{"payload": {...}, "note": "why"}`. The new version is **inactive**; no
   device changes. A `422` names the field that failed.
3. `POST /v1/admin/intake-rules/{version}/activate` with
   `{"reason": "at least 8 characters"}`. All other versions are deactivated in
   the same transaction. Devices pick it up on their next refresh (≤ 24 h).
4. Rollback: activate the previous version the same way. Activating the version
   that is already active changes nothing and writes no audit row.

Every publish and activation writes an audit row
(`admin.intake_rules_created`, `admin.intake_rules_activated` with the previous
version and the operator label).

## Detection

- Gate G1 precision (Create ÷ (Create + Ignore)) falls after a Meta/WhatsApp app
  update, or sellers report missed orders → the rules are stale.
- Extension (Phase 5) shows "Ei page ekhon support na" on a host → its selectors
  no longer match the page.
- `GET /v1/intake/rules` answering `503 SERVICE_UNAVAILABLE` → no version is
  active (should never happen; devices keep their cache).

## Containment

- Too many heads-ups: publish a version with a higher `threshold` and activate it,
  or roll back to the last good version.
- One app misbehaving: publish a version with that package's `default_enabled`
  set to `false` (sellers' explicit toggles still win).
- Everything: turn `intake_device_enabled` off.

## Recovery and verification

- Selectors: check them in a browser on the live page, publish them with today's
  `verified_at`, activate.
- After activation, `GET /v1/intake/rules` with a test device key returns the new
  `version`, and a request with the old ETag gets `200` (not `304`).
- `GET /v1/admin/intake-rules` shows exactly one `active: true`.
