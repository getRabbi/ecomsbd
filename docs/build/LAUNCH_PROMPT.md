# ecomsbd launch run: test, turn on, release (credit-safe)

Ei file ta repo-te `docs/build/LAUNCH_PROMPT.md` hishebe rakho. Duita prompt ache:

- **Prompt A** prothom session-e **ekbar** paste korba. Eta ledger-e "Launch log" ar queue likhe rakhe.
- **Prompt B** porer protita session-e paste korba (2 line).
**Kaj kivabe hobe:** Claude Code shob server-side check nije korbe: API call, worker job, flag, log, web deploy. Phone-e kichu korte hole o thambe ar **"WAITING: …"** line-e thik ki korte hobe likhe dibe. Tumi sheta kore Prompt B-er sheshe `Operator: done — <ki korle>` likhe pathabe.

## Shuru korar age tomar kaj (ekbar)

1. **Admin token:** Admin console-e login kore ekta admin token nao. Claude Code je machine-e chole, shekhane env-e rakho: `ECOMSBD_ADMIN_TOKEN=…`. File-e ba repo-te rakhbe na.
2. **Test shop:** Tomar nijer ekta test shop-er id nao (`TEST_SHOP_ID`). Prompt-e boshabe.
3. **Phone:** Ekta Android phone (Android 9+), ar customer shajar jonno arekta phone ba number.
4. **Steadfast:** Test shop-e tomar Steadfast-er live API key + secret diye account connect kore rakho.
5. **Alpha SMS:** Portal theke API key nao. Claude Code bolle admin console-e boshabe.
6. **Dump:** Pre-migration dump ta encrypt koro ba muche dao. Eta na kora porjonto Claude Code thambe.
---

## Prompt A: prothom session (ekbar paste korbe)

```text
ecomsbd LAUNCH run 12. Goal: test every launch-ready V4 feature live in production on one
test shop, widen to all shops, prepare the releases, write the launch kit. Spec
docs/V4_MASTER_BUILD_SPEC.md §1, ledger docs/build/PROGRESS.md, turn-on order
docs/build/V4_RELEASE_PR.md "Staging turn-on order". TEST_SHOP_ID = <PUT_ID_HERE>.

STARTUP
1. Read spec §1 only, the top of PROGRESS.md through "Operator actions waiting", and the
   "Staging turn-on order". Read a runbook only when its item starts.
2. Add a "## Launch log" table to PROGRESS.md (time BDT | change | scope | check |
   result | rollback). Every production change gets one row BEFORE you move on.
3. Replace "## Now" with the QUEUE below (run 12), then work it in order.

PRODUCTION ACCESS RULES (hard)
- Flags and provider accounts: only through the admin API, with the bearer token in env
  ECOMSBD_ADMIN_TOKEN. Never print the token. Never SQL-write feature_flags or any table.
  Read-only SELECTs on the production DB are allowed for checks (DATABASE_URL from the
  git-ignored env file, never printed).
- Order of scope for every flag: test-shop tenant row → check passes → global. Never
  global first. A failed check = set that flag off again at once, log it, stop the item,
  report.
- Never run migrations, edit secrets, deploy the API or worker, touch Northflank /
  Supabase / Meta / Play / Cloudflare DNS. Web deploys (npm run cf:deploy from a clean
  main checkout) are allowed only in L4, with the previous version id logged first as
  the rollback.
- Never create a real courier parcel or send SMS/calls to anyone except the operator's own
  test numbers. Never touch real sellers' data. Test orders: test shop only, customer
  name "LAUNCH TEST", deleted or cancelled at the end of each item.
- Never write live_verification into a courier manifest without a real parcel's status
  coming back through the poller (see L2.4).
- When an item needs the operator (phone, console, key, build), print one line
  "WAITING: <exact steps, numbered, ≤ 5>" and stop. Do not poll for the operator.

QUEUE run 12 (fixed order; mark each DONE / SKIPPED(reason) / FAILED(rolled back))

L0 Safety + infra (no feature change)
 0.1 Read-only inventory: every feature_flags row (global + tenant), whether Resend email
     and Sentry (backend) are configured (presence only). Table in the ledger.
 0.2 Dump: check the pre-migration dump in .env.backup-pre-a48001/ is encrypted (.age)
     or gone. If a plain .dump is there → WAITING (operator encrypts or deletes it).
 0.3 Pool: from config.py + db/session.py, values for DATABASE_POOL_SIZE,
     DATABASE_MAX_OVERFLOW, WORKER_DATABASE_POOL_SIZE so 2 API pods + 1 worker ≤ 12
     connections on the :5432 session pooler (stay on :5432; asyncpg prepared statements
     are not disabled). → WAITING (operator sets them + restarts). Then check
     /health/ready on api.ecomsbd.com and api.scalemyprints.com.
 0.4 Backend dependency lock: add a pinned constraints/lock file generated from the
     versions CI tested (sentry-sdk stays <3), make the Dockerfile install with it.
     Branch chore/backend-lock, PR to main, CI green. → WAITING (operator merges, turns
     Northflank CI/CD back on, sets the worker source back to main). Then check both hosts
     ready, migration a48001, worker jobs running (read-only: last runs of
     poll_courier_statuses).

L1 Legal + copy, before any flag (one PR: chore/launch-copy, CI green, operator merges)
 1.1 Privacy policy EN + BN (apps/web/app/privacy-policy): add only what the features
     being turned on do. SMS through Alpha SMS (sms.net.bd) gets the customer's phone and
     message. On-device notification reading for auto order detect (read on the phone;
     chat text sent only when the seller taps Create). The Chrome extension captures only
     the selected / open chat on a seller's action. Sentry crash reports with phones
     masked. Carrybee as a courier. Factual, no new data practice. Update "Last updated".
 1.2 Public site copy: remove any claim the product does not do today (Apple sign-in is
     not on Android; couriers = "Steadfast" live, others "connect when available" until
     their flags are on). Add short EN + BN sections for the features this run turns on:
     auto order detect (Android), confirmation call queue + SMS confirm link, invoice /
     label print, delivery charge rules, Chrome extension. Keep "Free during launch".
 1.3 docs/launch/PLAY_DATA_SAFETY.md: the exact Data safety answers + the in-app
     disclosure text for the notification listener, from the code (what is read, where,
     what leaves the phone).

L2 Test shop: turn on and test, one flag at a time (tenant row = TEST_SHOP_ID)
 2.1 product_events_enabled (global is fine: no seller-visible change) → GET
     /v1/admin/ops/funnel shows events after one test action.
 2.2 delivery_rules_enabled → set 3 zone charges + default courier Steadfast on the test
     shop via API → create a LAUNCH TEST order → COD = items + zone charge.
 2.3 order_documents_enabled → fetch the invoice and 4×6 label HTML for that order:
     Bangla text present, barcode present. → WAITING (operator prints both from web and
     from the phone).
 2.4 Steadfast (operator decision: treat as live for seller-initiated booking).
     a) python -m app.provider_smoke steadfast --account-id <test shop's account>
        (read-only: balance, location lookup). Pass → steadfast_enabled on for the test
        shop.
     b) Do NOT book a parcel yourself. Do NOT write live_verification. Autopilot stays off.
     c) Add a ledger watch item: "first real Steadfast parcel by any shop → when the poller
        records a provider status for it, write live_verification (date + consignment id
        masked + status seen) into manifests/steadfast.yaml in a PR".
     Pathao / RedX: only if the operator has connected an account on the test shop; same
     read-only smoke; else SKIPPED.
 2.5 confirmation_engine_enabled → a new LAUNCH TEST order (customer = operator's second
     number) gets an order_confirmations row and appears in the call queue (API). →
     WAITING (operator opens the call queue on web + phone, calls, marks confirmed).
     Check the state change.
 2.6 Alpha SMS → WAITING (operator saves the Alpha SMS account in the admin console). Then
     alpha_sms_enabled + confirmation_sms_link_enabled for the test shop → trigger the
     link SMS for a LAUNCH TEST order to the operator's second number → WAITING (operator
     opens the link, confirms). Check: confirmation CONFIRMED, attempt cost recorded,
     no UNKNOWN. Then the shop's SMS messaging channel on → one status change → tracking
     SMS arrives (operator confirms).
 2.7 intake_device_enabled → WAITING (operator installs the 3.1.0 internal-testing build
     with ECOMSBD_API_BASE_URL=https://api.ecomsbd.com, links the phone in Settings → Auto
     order detect, grants Notification access, sends a WhatsApp order message from the
     second phone). Check: a capture + chat draft on the test shop; operator taps Create →
     order. Also check share-to-ecomsbd (WP1.5 Android).
 2.8 extension_enabled (needs 2.7's intake_device_enabled) → build apps/extension with
     production URLs → WAITING (operator loads it unpacked, pairs from web Settings →
     Browser extension, captures a selected test message). Check the capture and the
     /quick-order flow.
 2.9 ai_parse_enabled: only if backend/tests/fixtures/parse_holdout/holdout.yaml has ≥ 30
     masked real messages → run the live eval; ≥ 90% → on for the test shop, else keep
     the rule parser (SKIPPED with the score).
 2.10 Reconciliation: keep reconciliation_shadow_mode; report the shadow results so far.
     auto_match stays off (SKIPPED: needs real statements).
 NOT in this run (log as SKIPPED with reason): autopilot (needs live_verification + G4),
 voice (no provider), meta_official_path (App Review), bdcourier (written OK + policy),
 manual_payments / referrals (Free Launch Mode, billing off), carrybee (no live
 credentials), Shopify / WooCommerce / website (launch gate).

L3 Widen to every shop
 3.1 Only flags whose L2 check passed: turn each on globally, one at a time, 10 minutes
     apart. After each: /health/ready on both hosts, API 5xx + worker failures from logs
     (read-only), DB connections < 12, SMS UNKNOWN = 0. Anything off → that flag off, log,
     stop.
 3.2 Clean up: cancel or delete every LAUNCH TEST order and capture; unlink nothing the
     operator still uses.

L4 Releases (operator does the signing and the store consoles)
 4.1 Android 3.1.x: release notes EN + BN (what sellers can do now), Play listing short +
     full description update (honest, from L1.2), checklist of the build values
     (API base, Sentry DSN, version). → WAITING (operator builds with the release key,
     uploads to closed testing, updates privacy policy + account deletion URLs + Data
     safety from docs/launch/PLAY_DATA_SAFETY.md, then production 20% staged rollout).
 4.2 Web: after the L1 PR is merged, deploy main (npm run cf:deploy), log the new and the
     rollback version ids, check 12 key URLs (EN + BN site, privacy, app sign-in, orders).
 4.3 Extension: zip for the Chrome Web Store + listing text + privacy answers in
     docs/launch/CHROME_STORE.md. → WAITING (operator submits).
 4.4 Registrations still open from the domain move (Supabase Site URL, Meta URLs): print
     the exact values from API_DOMAIN_MIGRATION.md as one checklist. → WAITING.

L5 Launch kit (docs/launch/, no code)
 5.1 LAUNCH_STATUS.md: what is live, what is not and why, known limits (e.g. Steadfast has
     no webhook: status every 10 min; booking cannot be cancelled through the API).
 5.2 SELLER_ONBOARDING.md (Bangla first): sign up → connect Steadfast → delivery charges →
     link phone → confirmation queue → book → print → COD tracking, with the exact screen
     names.
 5.3 SUPPORT_PLAYBOOK.md: the 10 most likely seller problems, how to check each with the
     admin API, which flag to turn off.
 5.4 MARKETING_KIT.md: 3 Facebook post drafts (Bangla), one 30-second demo video script
     following the flow above, the Play listing text, one WhatsApp message to invite pilot
     sellers. Claims only from LAUNCH_STATUS.md.
 5.5 Metrics to watch daily from the funnel (signup → first order → first booking →
     first confirmation), with the admin API calls.

VERIFY / CREDIT
- Targeted tests + ruff/format/mypy on changed files for PRs; no full suite (CI runs it).
- Checkpoint after every 3 queue items or before context gets long: ledger + Launch log
  updated, commit + push the branch, print "Checkpoint saved — paste Prompt B." and stop.
- Chat: 3 lines per item (id, result, next). Full detail goes in the ledger only.
FINISH: final summary at the top of PROGRESS.md (what is live, for whom, since when), all
docs pushed via one PR (operator merges), report ≤ 12 lines.

Start with STARTUP.
```

---

## Prompt B: porer protita session

```text
Continue ecomsbd LAUNCH run 12. Read spec §1, the top of docs/build/PROGRESS.md through
"Operator actions waiting", and the Launch log + "Now" queue only. Same rules as recorded
there. Resume at the first item that is not DONE / SKIPPED. Checkpoint every 3 items.
```

WAITING-er kaj shesh hole Prompt B-er sheshe ekta line jog koro:

| Obostha | Jog korar line |
|---|---|
| Phone ready | `Operator: done — 3.1.0 internal build installed, phone linked, test message sent.` |
| Alpha SMS boshano hoyeche | `Operator: done — Alpha SMS account saved in the admin console.` |
| Print check hoyeche | `Operator: done — invoice + label printed from web and phone, Bangla OK, barcode scans.` |
| Kono check fail | `Operator: <ki dekhecho, ek line-e>.` |
| Kono step bad dite chao | `Operator: skip <item id> for now.` |

## Shesh hole ki pabe

- **Seller-der jonno live:** order dhora (phone notification + extension), call queue, SMS-e confirm link, Steadfast booking + status + payment, invoice/label print, delivery charge, tracking SMS. Shob Free Launch-e free.
- **Pore:** Autopilot (prothom asol parcel ar 14 diner pilot-er por), voice, Meta official, Shopify/WooCommerce, BD Courier.
- **`docs/launch/`-e:** onboarding guide, support playbook, marketing kit ar daily metrics.
