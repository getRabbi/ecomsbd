# ecomsbd Web/Desktop — Full Product Parity Master Specification

**Purpose:** Turn the existing `apps/web` Next.js application into the primary full-featured browser workspace for real sellers who work mainly from laptop/desktop, while keeping the Flutter mobile apps as the best companion for push notifications, quick checks, field work, and on-the-go actions.

**Canonical repository:** `getRabbi/ecomsbd`  
**Primary branch:** `main`  
**Web app:** `apps/web` (Next.js App Router + TypeScript)  
**Mobile app:** `apps/mobile_flutter` (Flutter)  
**Business API:** FastAPI under `/v1`  
**Seller auth:** Supabase Auth  
**Business database:** PostgreSQL through backend only  
**Current production API:** `https://api.scalemyprints.com/v1`  
**Launch policy:** `FREE_LAUNCH_MODE=ON`, `BILLING_ENABLED=OFF`

> This is an implementation specification, not a request to rebuild the project. The repository already contains a large desktop web application, complete backend domains, and mature mobile UX. The job is to close parity gaps, make browser workflows complete, unify design, and deploy the existing architecture safely.


---

# 0. REUSE-FIRST DIRECTIVE — THIS IS NOT A REBUILD

This is the most important rule in this specification.

**Do not rebuild ecomsbd for the web from zero.** The product already has three mature layers:

1. **Flutter mobile UI** with proven seller workflows and visual language.
2. **Next.js web app** with many desktop routes, components and API bindings already present.
3. **FastAPI backend** containing the actual business rules, persistence, integrations, RBAC, money logic, automation, reconciliation, courier logic and other domain behavior.

The web project is therefore a **reuse + parity + desktop-optimization project**, not a new application.

## 0.1 What is reused directly

| Existing asset | Required treatment |
|---|---|
| FastAPI business logic | Reuse as-is wherever possible |
| Existing `/v1` API endpoints | Reuse before considering any new endpoint |
| PostgreSQL data model / ledgers / state machines | Reuse; never duplicate in frontend |
| Supabase Auth | Reuse |
| Existing Next.js routes/pages | Modify in place; do not recreate equivalent pages |
| Existing web API/session/access/i18n/money helpers | Extend/reuse; do not create parallel replacements |
| Existing web shared components | Extend/reuse when suitable |
| Flutter workflows and field semantics | Use as the behavioral reference for parity |
| Flutter design language | Port visually into desktop patterns |
| Flutter widget source code | **Do not attempt to reuse directly inside Next.js** |
| Flutter Web build | **Do not introduce as a second browser client** |

## 0.2 Flutter is the product reference, not the browser framework

The Flutter application already answers many important product questions:

- which fields a seller needs
- which validations matter
- which status wording is understandable
- which quick actions are important
- how the seller moves between Home, Orders, Inbox, Money, Insights and More
- what the current visual identity looks like
- how courier, customer, inventory and money flows are presented

Use that knowledge. Do **not** mechanically translate each Flutter widget into React.

Parity means **same business capability and truth**, not pixel-for-pixel identical screens.

Example:

```text
Flutter mobile Order screen
        |
        | same backend endpoints + same business semantics
        v
Next.js desktop Orders workspace

Mobile: stacked cards / bottom sheets
Desktop: table / filters / bulk bar / right-side detail drawer
```

Both are the same feature. Only the interaction model differs.

## 0.3 Existing web must be upgraded, not replaced

Before changing any feature, inspect the current implementation in `apps/web`.

For each capability:

1. If it is already complete, **preserve it** and only polish when needed.
2. If it is partial, **finish the missing behavior in the existing route/components**.
3. If it is missing, implement it using the existing web architecture and backend API.
4. If the backend capability already exists, do not recreate it client-side.
5. If the mobile screen contains useful UX, use it as reference rather than making a second product design from scratch.

Creating a new page that duplicates an existing page is a regression unless there is a documented reason.

## 0.4 Existing web primitives to prefer

Before creating new infrastructure, reuse the current web foundations where appropriate, including:

- `apps/web/components/shell.tsx`
- `apps/web/components/ui.tsx`
- `apps/web/components/DataTable.tsx`
- existing feature components such as integration, CRM, workflow, forecasting and procurement components
- `apps/web/lib/api.ts`
- `apps/web/lib/useApi.ts`
- `apps/web/lib/session.tsx`
- `apps/web/lib/access.ts` / `access-context.tsx`
- `apps/web/lib/i18n.ts`
- `apps/web/lib/money.ts`
- existing feature-specific web libs and string files
- `apps/web/app/globals.css` design tokens unless a deliberate shared token refactor is justified

Do not create `api2`, `session2`, a second i18n system, a second money formatter, or a second design system merely because a new web phase begins.

## 0.5 Expected code-reuse ratio

The expensive part of the product is already built. The implementation should maximize reuse roughly in this order:

- **Backend domain/business logic:** almost entirely reused
- **Database/storage/integration infrastructure:** entirely reused unless a real gap is found
- **Next.js existing pages/libs/components:** reused and extended heavily
- **Flutter product/UX knowledge:** heavily reused as reference
- **New code:** primarily desktop presentation, missing browser interactions, and only genuinely absent API surface

If a proposed plan requires rebuilding order logic, customer identity logic, stock logic, courier logic, reconciliation, money calculation, forecasting, risk rules or automation in React, the plan is wrong.

## 0.6 No unnecessary mobile changes

This browser expansion should not modify Flutter merely to make web development easier.

Only change Flutter when:

- a true shared product contract must change, or
- a separately requested mobile issue exists.

Web-only work must not trigger Android/iOS rebuilds.

# 1. Product decision

## 1.1 Primary product split

### Browser / desktop = full operations workspace

The browser version is the seller's most capable workspace and should expose the complete usable product surface:

- daily order operations
- bulk operations
- courier booking and management
- products and inventory
- customers and CRM
- customer risk context
- COD, payouts, receivables and reconciliation
- expenses and cashflow
- returns / RTO
- analytics / insights
- imports
- procurement
- forecasting
- notifications
- campaigns
- automations
- team / RBAC
- shop settings
- developer / public API tools
- connection status
- Chat-to-Order review workflow when the integration is enabled later

Desktop must not be a stretched mobile UI. It should use tables, split panes, drawers, bulk selection, keyboard-friendly controls, dense information, and wide-screen comparisons.

### Mobile = companion and fast operational surface

Flutter remains first-class, especially for:

- FCM push notifications
- quick order checks
- order status actions away from desk
- courier status
- customer lookup
- risk review
- quick stock updates
- quick expense entry
- Inbox review
- seller alerts
- portable use

The two clients share the same backend truth. A seller can begin an action on one and continue on the other without duplicated business logic.

---

# 2. Non-negotiable architecture

Do not introduce a second business architecture for web.

```text
Browser / Next.js
      |
      | Supabase seller session JWT
      v
FastAPI /v1
      |
      +--> PostgreSQL / Supabase
      +--> Redis / ARQ worker
      +--> R2
      +--> courier/provider adapters
      +--> FCM / external services
```

## Rules

1. `apps/web` must use the existing FastAPI business APIs.
2. The browser must never query business tables directly through Supabase.
3. Supabase remains authentication only.
4. Backend RBAC, tenant isolation, feature flags, idempotency and audit rules remain authoritative.
5. Money remains integer paisa.
6. Existing ledgers remain authoritative.
7. Do not duplicate mobile business rules in TypeScript.
8. Do not create web-only order/customer/stock logic.
9. Do not replace Next.js with Flutter Web.
10. Do not change backend contracts unless a genuine missing browser capability cannot be implemented through an existing safe endpoint.
11. If a backend endpoint is added, it must serve the same business domain rather than a web-specific duplicate.
12. Preserve `FREE_LAUNCH_MODE=ON` and hidden/disabled paid purchase flows.

---

# 3. Current repository reality

The repository already contains substantial desktop routes:

- `/dashboard`
- `/orders`
- `/customers`
- `/products`
- `/couriers`
- `/money`
- `/reconciliation`
- `/returns`
- `/insights`
- `/network`
- `/notifications`
- `/messaging`
- `/campaigns`
- `/automation`
- `/forecasting`
- `/procurement`
- `/imports`
- `/integrations`
- `/order-sources`
- `/team`
- `/settings`
- `/developers`
- billing/plan routes
- legal/support/marketing public routes

The backend already contains domains for:

- account/auth
- shops/tenancy/RBAC
- orders
- chat orders
- customers/CRM
- products/inventory
- couriers and consignments
- money/payouts/reconciliation
- imports
- returns/RTO
- insights
- forecasting
- procurement
- messaging/campaigns
- automation
- risk/network
- notifications
- integrations
- team
- developers/public API
- billing

Therefore the implementation must start with a **gap matrix**, not a rewrite. The gap matrix must compare three sources of truth at once: **current Next.js behavior, current Flutter behavior, and current FastAPI capability**. It must identify only the delta that still needs work.

A missing browser screen does **not** imply a missing product feature if the backend already provides the capability. In that case the work is primarily a web presentation/client task. Conversely, a pretty existing web screen backed by incomplete behavior is still partial.

For every browser route, classify each mobile/backend capability as:

- `WEB_COMPLETE`
- `WEB_PARTIAL`
- `WEB_MISSING`
- `MOBILE_ONLY_BY_DESIGN`
- `COMING_SOON_BY_LAUNCH_POLICY`

Only then implement gaps.

---

# 4. Launch policy for external integrations

For the current launch, preserve the already-agreed public UX gate.

The following must remain **Coming Soon / শীঘ্রই আসছে** and non-interactive in both public browser and mobile seller UX:

- Shopify
- WooCommerce
- Custom Website
- Messenger
- WhatsApp

Existing backend implementation, stored connections, credentials, Meta work, WooCommerce work, custom website work and provider code must remain intact for later re-enable.

Do not delete it.

Do not expose:

- Connect
- Reconnect
- Setup
- credential forms
- approval errors
- provider failure errors

for these five providers while the launch gate is active.

They must not pollute summary or attention counts.

### Couriers remain active

Keep fully operational:

- Steadfast
- Pathao
- RedX

including:

- connect
- reconnect
- account management
- pickup-store handling where relevant
- booking
- tracking/status
- bulk booking
- courier comparison
- COD/payment related downstream operations

Courier functionality must never be hidden by the integrations Coming Soon gate.

---

# 4.1 Feature-porting method

When bringing a mobile capability to desktop, use this sequence:

1. Find the Flutter feature/screen that represents the current seller workflow.
2. Identify the providers/repositories/API calls that supply its real data.
3. Identify the corresponding FastAPI endpoint/service and business constraints.
4. Check whether `apps/web` already has a route/component/lib for the same domain.
5. Extend the existing web route first.
6. Reuse existing web API/session/access/i18n/money utilities.
7. Build a desktop-native interaction pattern around the same backend truth.
8. Add only the smallest missing backend API if no safe existing endpoint can support the browser capability.
9. Add targeted tests around the changed path.

This sequence prevents a common failure mode: reading the Flutter screen, then reimplementing its business logic in TypeScript without noticing that the backend already owns it.

## 4.2 What should *not* be ported literally from mobile

Do not reproduce mobile-only interaction patterns when desktop can do better:

- bottom navigation → persistent grouped sidebar
- repeated bottom sheets → side drawer / modal / inline panel
- one-card-at-a-time lists → dense tables
- long stacked detail screens → split view / tabs / side-by-side sections
- one-order-at-a-time actions → safe bulk actions where backend supports them
- phone-sized quick-action cards → compact toolbar/command menu

Do preserve:

- terminology
- status labels
- field validation
- business rules
- warning semantics
- data-quality caveats
- role restrictions
- state transitions

---

# 5. Design system: desktop that clearly belongs to the mobile app

The browser must feel like the same ecomsbd product as Flutter without copying a phone layout.

## 5.1 Brand language

Reuse the mobile visual identity:

- primary orange: approximately `#FF4500`
- dark ink/navy surfaces
- clean white cards
- cool light-grey workspace background
- muted greys for secondary text
- green / amber / red status semantics
- rounded surfaces
- restrained shadows
- Bangla-first-friendly typography
- high contrast
- no artificial neon/saturated "AI generated" feel

The existing web CSS already uses this family. Improve it rather than replacing it.

## 5.2 Desktop layout

Recommended large-screen frame:

- left sidebar: ~236–256 px
- sticky top bar: ~60–68 px
- content workspace with 12-column grid
- max-width only where useful; data tables may use full available width
- right-side drawers for fast edit/review
- detail pages for deep workflows
- sticky bulk-action bars when rows are selected

### Top bar should contain

- current shop / shop switcher
- global search / command trigger
- notifications
- locale switcher
- account/profile menu
- optional sync/connection indicator when useful

Do not bury shop switching in a deep settings page.

## 5.3 Navigation

Desktop navigation should be grouped by seller workflow, not by engineering domain.

### DAILY
- Dashboard
- Orders
- Inbox / Chat-to-Order
- Customers
- Products
- Couriers

### MONEY
- Money overview
- Receivables
- Payouts
- Reconciliation
- Expenses

### GROW
- Insights
- Returns / RTO
- Forecasting
- Procurement
- Campaigns
- Automation
- Risk / Network

### MANAGE
- Notifications
- Imports
- Connections
- Team
- Settings
- Developer / API

Hide plan/billing navigation while free launch mode says paid UI is off.

Use RBAC to hide inaccessible destinations where existing access data supports it.

## 5.4 Desktop interaction patterns

Prefer:

- tables over repeated tall cards
- drawers for quick review/edit
- bulk row selection
- column sorting
- explicit filters
- search
- saved filter state where easy
- sticky table headers
- keyboard-friendly forms
- clear empty/loading/error states
- copy buttons for IDs/refs
- inline status chips
- confirmation dialogs only for destructive/irreversible actions

Avoid:

- enormous mobile-sized tiles
- vertical pages requiring excessive scrolling for simple CRUD
- hover-only critical controls
- hidden business state
- decorative animation that slows operations

## 5.5 Responsive behavior

Desktop is primary, but web must remain usable on tablet/smaller browser widths.

Breakpoints should collapse intelligently:

- wide desktop: sidebar + multi-column content
- medium/tablet: compact/collapsible sidebar
- narrow: stacked content and overflow-safe tables

Do not attempt to replace the native mobile app with a perfect mobile-web clone.

---

# 6. Global browser features

## 6.1 Global search

Implement a browser-wide search entry point using existing safe APIs.

Search should prioritize:

- order number
- customer name/phone-safe lookup
- product/SKU
- consignment/tracking reference where supported

Results should route directly to the canonical detail surface.

If a single global backend search endpoint does not exist, compose existing search endpoints client-side only when safe and bounded. Do not fetch entire datasets.

## 6.2 Command/quick action surface

Desktop sellers benefit from immediate actions.

Expose:

- New order
- Paste/parse order
- Add product
- Stock adjustment
- Add expense
- Book courier / open confirmed orders
- Import file
- Risk check

This may be a header quick-action menu or command palette.

## 6.3 Unified notifications

Web Notification Centre must use the same backend notification truth as mobile.

Support:

- unread count
- filters
- mark read
- deep link into relevant entity
- alert context
- empty/error/loading states

Do not invent browser notifications as a replacement for mobile FCM.

Browser Notification API may be considered later, not required for parity.

## 6.4 Language

Keep both:

- বাংলা
- English

No page should hardcode one language if its adjacent product area already uses the translation system.

Bangla strings must remain natural seller language, not literal machine translations.

---

# 7. Authentication, session and onboarding

## 7.1 Browser authentication

Web should support the production seller auth methods already enabled for the product:

- email/password
- Google
- Apple where supported on browser by current Supabase configuration

Do not resurrect retired custom token/OTP auth.

Requirements:

- secure Supabase session
- FastAPI JWT usage
- sign out
- expired-session handling
- correct shop membership resolution
- shop switching
- role-aware route handling

## 7.2 First-run onboarding

Desktop must no longer tell the seller to finish important shop configuration on the phone.

Bring shop setup to web:

- shop name
- business category
- pickup contact
- pickup address
- district/area where supported
- timezone
- currency
- order prefix where existing rules permit edit
- onboarding completion state

Reuse backend validation.

Mobile and web must display the same resulting shop state.

---

# 8. Dashboard / Home

The web dashboard should be the desktop equivalent of the strongest parts of mobile Home plus browser density.

## Hero summary

Use a dark branded hero panel consistent with Flutter.

Show:

- gross sales today
- orders today
- delivered today
- COD outstanding
- contribution profit
- estimate/quality caveat when necessary

Never render an estimate as exact if backend marks it estimated/missing.

## Quick actions

- New order
- Paste order
- Book courier / confirmed orders
- Add expense

## Today's operational progress

- delivered
- returned
- delivery success
- realized revenue
- COD outstanding
- expected/unforecast COD wording according to backend truth

## Needs attention

Show actionable items only:

- courier reconnect/pickup issue
- booking unknown
- payout/reconciliation discrepancy
- overdue COD
- low/out stock
- failed import
- open risk/review issue
- chat-order draft later when provider gate is lifted

## Recent work

Dense list/table:

- recent orders
- recent courier status changes
- recent money events
- important notifications

## Business pulse

30-day compact trend and links to full Insights.

Respect existing ADR/no-chart-package approach unless repository policy changes: use existing SVG/CSS chart patterns rather than adding a heavy chart dependency.

---

# 9. Orders — full desktop operations

Orders are the highest-priority browser workflow.

## 9.1 Orders table

Support:

- search
- status filters
- date range
- courier filter where data exists
- source filter
- customer filter
- pagination
- sortable useful columns
- multi-select

Columns should include relevant available data:

- order number
- customer
- phone masked
- total/COD
- status
- courier/consignment
- tracking/reference
- source
- age/date
- risk/attention marker
- payment/COD state where useful

## 9.2 Bulk actions

Where backend APIs permit:

- confirm
- pack
- courier booking
- dispatch
- export
- other existing batch action

Never simulate bulk action by silently issuing unsafe parallel client requests if a canonical backend bulk endpoint exists.

## 9.3 Create order

Desktop new-order flow must support everything available in mobile:

- customer/name
- phone
- address
- item/product selection
- variant
- quantity
- COD/price
- notes
- duplicate warning
- customer context
- product availability
- validation
- save

Use side drawer or full editor depending on complexity.

## 9.4 Paste/parse order

Bring the mobile Inbox manual fallback into web.

Seller pastes Bangla/Banglish order text.

Show parsed:

- name
- phone candidates
- selected phone
- address
- product
- variant
- quantity
- COD
- warnings
- uncertain fields

Then open canonical review/create order flow.

Use current parser; no paid AI.

## 9.5 Order detail

Full detail should expose:

- lifecycle/status
- customer snapshot
- item snapshots
- totals
- duplicate context
- courier status
- booking state
- tracking
- COD receivable
- payout/reconciliation linkage
- profit/data-quality snapshot where permitted
- audit/relevant activity
- cancellation/return implications

Actions must obey state machine and RBAC.

---

# 10. Inbox / Chat-to-Order

Create a first-class `/inbox` desktop route even though the public external-provider connections are launch-gated.

Why keep the route:

1. manual Paste → Detect remains useful now
2. server-side chat-order domain already exists
3. provider draft review can become visible later without redesign

## Launch behavior

While Messenger/WhatsApp are Coming Soon:

- show clear Coming Soon status for live capture
- retain manual Paste → Detect
- do not expose provider connection controls here
- if existing draft data exists from development, do not destroy it; owner/admin may still inspect if product policy allows

## Future-ready draft table

Structure should support:

- source
- customer/external identity
- received/updated time
- missing fields
- matched customer
- matched/ambiguous product
- status
- review/ignore

Review opens normal order composer prefilled.

Do not build a chat-bubble clone.

---

# 11. Products & inventory

Desktop should be stronger than mobile for catalogue management.

## Product list

- search
- SKU
- active/archive filter
- stock status
- category where supported
- price
- cost
- stock
- low-stock threshold
- variant indicator

## Product create/edit

Support existing backend fields:

- name
- SKU
- default selling price
- cost
- low-stock threshold
- variants
- active/archive state

## Inventory

- stock adjustment
- reason
- allow-negative only when backend rules allow
- movement history
- balance-after display
- low-stock/out-of-stock filters

Never "set stock" directly if the domain uses append-only stock movements.

## Variant UX

Desktop should make variant editing clear and compact:

- variant name
- SKU
- price override
- active
- current stock where represented

---

# 12. Customers & CRM

## Customer list

- search
- segment
- status/flag
- order count
- delivered/returned context
- last order
- masked phone

Segments should reuse existing backend-supported semantics, e.g. repeat/inactive/high-value where available.

## Customer detail

Show:

- safe identity/contact
- addresses
- order history
- success/RTO history
- customer flag and reason
- CRM notes/records
- relevant risk context
- channel/source identity when later enabled

## Privacy-sensitive phone reveal

Plaintext reveal must:

- require existing permission
- require reason
- call the audited backend endpoint
- never reveal automatically in tables
- never store revealed phone in browser persistence

## CRM

Bring existing CRM records/actions to web rather than making users return to mobile.

---

# 13. Risk & network

Web must expose existing risk capabilities in a serious operational layout.

Include:

- individual risk check
- external-risk/provider result where configured
- seller's own customer history
- network-derived information only as backend permits
- clear explanation of sample/quality/availability
- no defamatory labels

Risk information is advisory and should not silently block an order unless the backend explicitly owns such a rule.

---

# 14. Couriers — active launch feature

Courier UX is a launch-critical desktop capability.

## Courier accounts

For Steadfast, Pathao, RedX:

- provider status
- Connect
- Manage
- Reconnect
- masked credential/account identifier
- pickup-store requirement
- test connection where supported
- provider availability
- honest errors

Credentials remain backend encrypted.

Never display stored plaintext secrets.

## Courier compare

Bring mobile courier comparison to web.

Comparison should use real backend data only:

- available provider
- price/quote where verified
- service/booking eligibility
- seller-relevant status

Never invent provider rates.

## Booking

Order detail and bulk order flow should support:

- courier selection
- booking
- pickup store
- shipment/reference
- booking unknown state
- retries only when safe according to backend semantics

## Tracking/status

Show canonical consignment status and meaningful timestamps.

Do not implement a second courier state machine in web.

---

# 15. Money workspace

Desktop should be the strongest money/reconciliation experience.

Create a clear Money information architecture.

## `/money` overview

- COD outstanding
- collectible
- received
- overdue
- in transit
- expected/unforecast
- payout summary
- reconciliation discrepancies
- cashflow summary

## Receivables

Either route or tab:

- expected
- collectible
- partially settled
- settled
- aging buckets
- courier
- amount
- related order/consignment

## Payouts

- payout list
- statement upload
- parse/mapping status
- payout detail
- lines
- applied/unapplied
- adjustments
- source file
- relevant evidence

## Reconciliation

Desktop optimized:

- unmatched
- candidate matches
- score/evidence
- exact/soft signals
- discrepancy type
- match
- unmatch/reverse
- manual reason
- cases
- close case with note

Shadow mode remains backend-owned.

## Financial truth

Web must preserve:

- append-only ledger
- reversal instead of deletion/edit
- oversettlement rules
- settled vs expected distinction
- quality labels

---

# 16. Expenses and cashflow

Web currently needs parity with mobile expense flows.

Provide:

- expense list
- date/category filters
- create expense
- amount
- category
- note
- allocation status where exposed
- relevant profit impact explanation
- cashflow view

Use existing backend expense/allocation semantics.

Do not make fixed cost allocations look accounting-grade if backend intentionally does not.

---

# 17. Returns / RTO

Full desktop RTO workspace:

- return counts
- RTO rate
- reasons
- courier comparison
- product comparison
- area comparison
- direct loss
- outward/return charges
- stock restoration state
- sellable/write-off context
- missing reason count

Order/consignment actions remain backend-state-driven.

---

# 18. Insights / analytics

Existing web Insights is already substantial. Preserve and extend it only for parity gaps.

Core views:

- revenue
- contribution profit
- data quality
- orders
- delivery
- RTO
- receivables
- overdue
- discrepancies
- product performance
- courier performance
- customer metrics
- inventory/slow moving
- cash
- reconciliation

Support:

- 7 / 30 / 90 days
- custom range
- explanation of "what changed"
- links to operational pages

Never fabricate metrics with insufficient samples.

---

# 19. Procurement & inventory purchasing

Desktop is ideal for procurement.

Provide complete workflow around existing backend:

- suppliers
- purchase orders
- new PO
- PO detail
- stock receipt
- payables
- status
- expected quantities/costs
- product links

Use current `/procurement` routes and close any mobile/backend parity gaps.

---

# 20. Forecasting

Desktop forecasting should expose existing backend models without overselling precision.

Include:

- overview
- cash forecast
- item forecast
- accuracy/history where implemented
- confidence/data availability language
- unforecast values rather than invented dates

Keep provider settlement assumptions empirical, not hardcoded guesses.

---

# 21. Messaging, campaigns and automations

These are separate from the launch-gated external inbound integrations.

## Messaging

Expose currently supported channels/capabilities honestly.

Do not imply full Messenger/WhatsApp conversation UI.

## Campaigns

Complete web campaign management if backend allows:

- list
- create
- audience/segment
- template
- schedule/send semantics
- status
- metrics/receipts
- consent restrictions

## Automation

Desktop is the best place for workflow configuration.

Use existing automation builder/routes:

- list
- create
- edit
- enable/disable
- trigger
- conditions
- actions
- safe validation
- activity/result where available

Do not invent unsupported triggers/actions.

---

# 22. Imports

Keep/improve the existing desktop import wizard.

Support existing backend workflow:

- file upload
- detect
- mapping
- preview/dry-run
- row-level errors
- commit
- duplicate-file prevention
- products
- orders

Desktop should make large error tables easy to inspect/download if backend supports export.

Do not silently coerce bad data.

---

# 23. Notifications and operational alerts

Use server notification truth.

Views:

- all
- unread
- actionable
- category/type
- created time
- linked entity

Alert classes can include existing business conditions:

- unpaid COD
- discrepancy
- booking unknown
- low stock
- provider/courier issue
- weekly summary

No duplicate client-side alert logic.

---

# 24. Team and RBAC

Browser must provide complete team administration for authorized roles.

Support existing backend:

- members
- invitations/add-member flow if implemented
- roles
- permission-aware actions
- remove/deactivate where supported
- current user's access summary

Never rely on hidden UI as security. Backend RBAC remains final authority.

---

# 25. Settings — make browser self-sufficient

Current desktop Settings has historically been partially read-only. Full desktop parity requires real management flows.

Bring in:

## Shop
- shop name
- category
- pickup address/contact
- timezone
- supported preferences

## Account & security
- account identity
- auth/security actions supported by Supabase/backend
- sign out / session management where available

## Notifications
- existing notification preferences

## Data & privacy
- privacy policy
- account deletion
- data-related controls already implemented

## Courier accounts
Can link into courier management.

## Connections
Keep launch-gated five providers Coming Soon; courier connections remain active elsewhere.

## Team
Link/manage based on role.

## Developer/API
Link where relevant.

Do not create fake editable fields if backend does not support changing them. Add a canonical endpoint only when genuine product requirement exists.

---

# 26. Developer / Public API

Preserve existing developer tooling and make it operationally clear.

Possible current capabilities:

- public API key management
- custom website/API information
- webhooks
- developer status
- copyable IDs/URLs
- security guidance

However, because Custom Website seller integration is Coming Soon for launch, any user-facing activation path must respect that gate.

Developer diagnostics for platform operators may remain if already authorized separately.

Never expose secrets after one-time display.

---

# 27. Billing / plans during free launch

Current product policy:

- `FREE_LAUNCH_MODE=ON`
- `BILLING_ENABLED=OFF`
- current features free
- paid plan data preserved for future re-enable

Therefore public seller web must not show:

- buy plan
- upgrade pressure
- checkout
- subscription gating that contradicts free launch

It may show a neutral "Free launch" explanation if existing policy already does.

Do not delete billing architecture.

---

# 28. Support and legal

Keep public, no-login pages available:

- marketing
- support
- privacy policy
- account deletion

New ecomsbd domain should eventually host canonical equivalents.

Existing App Store URLs under `scalemyprints.com` must not be broken during migration.

---

# 29. New ecomsbd domain architecture

The exact purchased Cloudflare domain must be discovered from the user's actual DNS/account/config or explicitly supplied. **Do not guess the TLD.**

Recommended architecture once exact domain is known:

```text
https://<ECOMSBD_DOMAIN>
    public marketing / product site

https://app.<ECOMSBD_DOMAIN>
    authenticated full seller web workspace

https://api.<ECOMSBD_DOMAIN>/v1
    eventual canonical API origin
```

However, API migration is optional and should not be coupled to first web-parity launch.

## Safer staged rollout

### Stage 1
Attach:

- root domain to public site
- `app.` subdomain to existing `ecomsbd-web` Vercel project

Keep mobile and browser business calls on:

`https://api.scalemyprints.com/v1`

This avoids breaking:

- mobile releases
- OAuth callbacks
- webhook callbacks
- integrations
- existing CORS assumptions

### Stage 2
Only after the web app is stable:

- add `api.<ECOMSBD_DOMAIN>` as additional API hostname
- configure certificates/DNS
- update trusted hosts/CORS
- verify health
- keep `api.scalemyprints.com` as compatibility alias
- migrate clients deliberately

Do not remove the old API hostname until installed mobile versions no longer require it.

## Public pages compatibility

Keep these current URLs working until store metadata is deliberately changed:

- `https://scalemyprints.com/ecomsbd`
- `https://scalemyprints.com/ecomsbd-support`
- privacy policy URL
- account deletion URL

Redirect only when tested and safe.

## Cloudflare/Vercel rules

- use the existing Vercel project rather than creating a duplicate
- follow Vercel's exact DNS target for the domain
- do not guess A/CNAME values
- do not change API Cloudflare routes during web-only work
- verify HTTPS
- verify redirects
- verify canonical URLs
- verify no redirect loops

---

# 30. Web route target map

This is the target information architecture. Reuse existing routes when they already exist; do not churn URLs without value.

| Area | Target route |
|---|---|
| Dashboard | `/dashboard` |
| Orders | `/orders` |
| Inbox / Chat-to-Order | `/inbox` |
| Customers | `/customers` |
| Products | `/products` |
| Couriers | `/couriers` |
| Money | `/money` |
| Receivables | `/money/receivables` or Money tab |
| Payouts | `/money/payouts` or Money tab |
| Expenses | `/expenses` or Money tab |
| Reconciliation | `/reconciliation` |
| Returns/RTO | `/returns` |
| Insights | `/insights` |
| Procurement | `/procurement` |
| Forecasting | `/forecasting` |
| Campaigns | `/campaigns` |
| Automation | `/automation` |
| Risk/Network | `/network` and/or dedicated risk flow |
| Notifications | `/notifications` |
| Imports | `/imports` |
| Connections | `/integrations` or renamed UI label while preserving route |
| Team | `/team` |
| Settings | `/settings` |
| Developer/API | `/developers` |

A subroute should be created only when a tab/drawer would become overcrowded.

---

# 31. Feature parity rule

For every mobile feature, choose one:

### A. Full web equivalent
Default for business operations.

### B. Better desktop equivalent
Use when web can be materially more efficient, e.g.:

- data tables
- bulk actions
- side-by-side reconciliation
- inventory grids
- analytics
- procurement
- automation builder

### C. Mobile-only by design
Allowed only for genuinely device-native behavior:

- FCM device token registration
- native push handling
- app-store-specific UI
- device-only deep link behavior

Do not classify a feature as mobile-only merely because the current web page has not been built yet.

---

# 32. Desktop-specific productivity improvements

These add value without changing the business model.

## Keyboard and selection

Where appropriate:

- `/` focuses search
- `N` can open New Order only if it does not interfere with form typing
- Escape closes drawer/modal
- shift/click multi-select
- Enter opens selected row only if accessibility remains correct

Avoid obscure shortcut-only actions.

## Bulk views

Orders, products, customers and reconciliation should support batch selection where backend operations support it.

## Export

Expose existing safe export capability when backend provides it. Do not generate huge browser-side exports by fetching all pages.

## Split view

For high-volume workflows:

- left list/table
- right detail drawer

Good candidates:

- orders
- customers
- reconciliation
- notifications

---

# 32.1 Migration vs. recreation decision rule

Before implementing any browser feature, answer these questions in order:

1. **Does the backend already support it?**
   - Yes → use the existing endpoint/domain.
   - No → confirm it is truly required before adding backend work.

2. **Does `apps/web` already expose it?**
   - Yes and complete → leave it intact unless polish is needed.
   - Yes but partial → extend it in place.
   - No → add the smallest route/component needed.

3. **Does Flutter already define the seller workflow?**
   - Yes → match behavior/terminology, but redesign interaction for desktop.

4. **Is the behavior launch-gated?**
   - Yes → do not implement/enable public setup just to achieve superficial parity.

5. **Is the requested behavior genuinely device-native?**
   - Yes → it may remain mobile-only by design.

A new browser feature is considered successfully ported only when it uses the same canonical backend truth and does not create a parallel state machine.

---

# 33. Performance requirements

Real sellers may have thousands of records.

Rules:

1. Server pagination; never fetch entire tables for convenience.
2. Debounce search.
3. Abort stale requests when filters change if current API helper supports it.
4. Avoid refetch storms.
5. Do not run every dashboard endpoint every few seconds.
6. Cache only non-sensitive safe state.
7. Do not persist plaintext sensitive data in localStorage.
8. Lazy-load expensive secondary panels.
9. Use table virtualization only if real measured need exists; do not add complexity preemptively.
10. Keep bundle size controlled.
11. Avoid heavy chart packages if current ADR forbids them.

---

# 34. Security and privacy

All existing security constraints remain.

## Never put in the browser bundle

- Supabase service role
- DB URL/password
- courier credentials
- Meta app secret
- Apple private key
- admin tokens
- R2 secret
- provider service accounts

## Browser storage

May contain only what existing auth/client design safely permits.

Never persist:

- revealed full phone numbers
- one-time API keys
- provider access tokens
- courier keys

## PII

- tables show masked phone by default
- reveal is explicit + audited
- do not put raw message bodies/PII into analytics or Sentry
- never log provider secrets

## Tenant/RBAC

Web must rely on backend checks and also provide a clean role-aware UX.

---

# 35. Accessibility and quality

Minimum expectations:

- semantic buttons/links
- keyboard navigation
- visible focus
- labelled form controls
- table headers
- no color-only state
- reasonable contrast
- reduced motion respected
- Bangla text does not clip
- destructive actions named explicitly
- money values use tabular formatting where useful

---

# 36. Error handling

Use existing stable backend errors.

UI should distinguish:

- validation problem
- permission problem
- expired auth
- connection/provider problem
- retryable network problem
- conflict/version problem

Never show:

- raw stack trace
- raw provider body
- secret-containing exception
- generic "Something went wrong" when stable code already explains it

Use retry actions only where safe.

---

# 37. Implementation phases

The project is large. Do not attempt a blind rewrite in one commit.

## Phase W0 — inventory and gap matrix

Read only:

- this master spec
- current `apps/web`
- mobile feature directory/map
- backend route map
- relevant docs for features that are actually being touched

The goal is **not** to discover how to rebuild the product. The goal is to discover exactly what is already usable on web and what is still missing.

For each major domain record:

- existing Next.js route/component
- existing web API helper/client module
- corresponding Flutter feature reference
- corresponding backend endpoint/domain
- parity state
- exact missing browser behavior

Output an internal checklist/gap matrix.

Do not perform another historical V3 audit.
Do not scaffold replacement pages for routes that already exist.
Do not create a new frontend architecture.

## Phase W1 — shell/design/auth/onboarding

Upgrade the existing web shell rather than replacing it. Reuse current shell/session/access/i18n primitives.

Complete:

- desktop design-token alignment with Flutter visual identity
- existing sidebar/top bar refinement
- responsive behavior
- shop switcher
- locale
- account menu
- global quick actions/search shell
- auth/session polish
- first-run shop setup
- remove any desktop copy that tells a seller to manage an important setting on the phone when the backend already supports editing it

## Phase W2 — daily operations

Complete parity for:

- dashboard
- orders
- manual Paste/Detect
- customers/CRM
- products/inventory
- couriers

For every item above, first preserve working existing web functionality, then add only the missing mobile/backend capabilities and desktop productivity improvements. Do not replace mature routes wholesale.

This is the first major usable web milestone.

## Phase W3 — money

Complete:

- money overview
- receivables
- payouts
- reconciliation
- cases
- expenses
- cashflow

## Phase W4 — insight and growth

Complete:

- insights
- returns/RTO
- risk/network
- forecasting
- procurement

## Phase W5 — messaging/automation/admin

Complete:

- notifications
- campaigns
- automation
- imports
- team
- settings
- developer/API
- Inbox shell/manual parser

Keep external five integrations Coming Soon.

## Phase W6 — domain and production

- exact new domain discovered/provided
- Vercel domains
- Cloudflare DNS
- production env/origin updates
- auth redirect allowlists if required
- CORS/trusted host only if origin changes demand it
- smoke test

## Phase W7 — final RC

Run the comprehensive web/affected-backend test pass only once after implementation converges.

---

# 38. Testing strategy — credit safe

Do not repeatedly run huge suites.

## During a phase

Run:

- targeted affected web tests
- TypeScript/type checks relevant to changed code
- lint for touched workspace
- targeted backend tests only when backend changed

## At phase boundary

Run a web build if enough structural work changed to justify it.

## Final RC only

Run:

- web test suite
- web production build
- lint/typecheck
- relevant backend suite if backend was changed during web parity
- migration check only if a migration was added
- secret scan
- production smoke

Do not rerun Flutter suites for web-only changes.

Do not rebuild Android/iOS for web-only changes.

---

# 39. Git safety

Before work:

- inspect `git status`
- identify user/unrelated modifications
- preserve stashes/untracked files
- no `git reset --hard`
- no `git clean`
- no force push

Work in normal branch/PR flow.

Do not bundle unrelated files.

Prefer coherent phase commits.

---

# 40. Deployment rules

## Web-only change

Deploy Vercel only.

Do not redeploy backend unnecessarily if CI/deployment architecture allows it.

If repository automation automatically deploys backend on merge, verify health but do not treat unchanged backend as modified.

## Backend change

Only when required by a real parity gap:

- targeted tests
- migration if necessary
- deploy API + worker as appropriate
- readiness check
- verify migration revision
- check recent errors

## Domain

Do not switch API/mobile domains as part of UI work.

---

# 41. Release acceptance checklist

The desktop web is ready when a real seller can use a laptop to do the whole daily business workflow without being told "do this on your phone."

Must pass:

### Identity
- sign in
- sign out
- shop switch
- RBAC

### Setup
- shop configuration editable
- courier accounts manageable

### Orders
- create
- paste/parse
- search/filter
- detail
- lifecycle actions
- duplicate warning
- bulk operations
- courier booking

### Products
- create/edit/archive
- variant management
- stock adjustment/history

### Customers
- search/detail
- order history
- CRM
- audited phone reveal
- risk context

### Money
- receivables
- payouts
- reconciliation
- cases
- expenses
- cashflow

### Operations
- returns/RTO
- imports
- notifications

### Planning
- insights
- forecasting
- procurement

### Growth/admin
- campaigns
- automation
- team
- settings
- developer/API

### Launch gate
- Shopify Coming Soon
- WooCommerce Coming Soon
- Custom Website Coming Soon
- Messenger Coming Soon
- WhatsApp Coming Soon
- couriers active

### Domain
- new public origin works
- new app origin works
- existing API compatibility intact
- legal/support routes intact

---

# 42. Things the implementation must NOT do

- Do not rebuild the web app from zero.
- Do not scaffold a second Next.js app.
- Do not introduce Flutter Web as another production browser client.
- Do not duplicate existing routes under new names without a documented migration reason.
- Do not throw away working web components merely to match the mobile screen visually.
- Do not rewrite the backend.
- Do not make Next.js talk directly to business DB tables.
- Do not replace Supabase Auth.
- Do not re-enable paid billing.
- Do not re-enable the five launch-gated integrations.
- Do not delete their implementation.
- Do not duplicate courier logic.
- Do not invent provider prices/status semantics.
- Do not convert money to floating point.
- Do not weaken tenant/RBAC controls.
- Do not expose secrets.
- Do not create a second reconciliation engine.
- Do not create a second parser.
- Do not create a second customer identity system.
- Do not introduce paid AI.
- Do not run repeated full audits.
- Do not rebuild mobile for web-only changes.
- Do not guess the new Cloudflare domain/TLD.
- Do not switch API host until compatibility is proven.
- Do not break current App Store support/privacy URLs.

---

# 43. Recommended first implementation output

Before writing substantial code, Claude Code should return a concise plan derived from the live repo:

1. current `main` SHA
2. exact new-domain value if discoverable, otherwise `NEW_DOMAIN_VALUE_REQUIRED`
3. current web production URL
4. current route inventory
5. parity matrix summary:
   - complete
   - partial
   - missing
6. first phase files to change
7. whether backend changes are needed for Phase W1
8. risks/blockers
9. planned targeted tests

Then immediately begin Phase W1 unless a genuine external/manual blocker prevents it.

Do not stop after the plan.

---

# 44. Definition of done

The browser is not done because pages exist.

It is done when:

- each exposed page uses real backend data
- actions persist through canonical APIs
- errors are honest
- role restrictions work
- page refresh preserves server truth
- two shops cannot cross-read
- no important daily workflow requires the phone
- no browser page says “manage on phone” for a capability the backend already supports
- no business rule has been duplicated into a new TypeScript-only implementation
- existing working web routes were preserved rather than needlessly recreated
- desktop workflows are faster than doing the same task on mobile
- the new ecomsbd domain is safely attached
- mobile remains a strong companion rather than the only complete client

**Target product experience:** a Bangladesh seller can sit at a laptop all day and run the business in ecomsbd, then leave the desk and keep receiving notifications/checking urgent items from the native app.


---

# 45. Claude Code execution contract

Use this section when beginning implementation. It intentionally reinforces the reuse-first rule so the project is not accidentally rebuilt.

```text
Continue the existing ecomsbd repository from CURRENT main.

Canonical spec:
docs/ECOMSBD_WEB_DESKTOP_MASTER_SPEC.md

This is NOT a new web-app build.
This is NOT a Flutter-Web migration.
This is NOT a backend rewrite.

The existing product already has:
- mature Flutter seller workflows
- substantial Next.js apps/web routes/components
- production FastAPI business logic and APIs

Your job is to upgrade the EXISTING Next.js web app to full desktop/browser parity by reusing what already exists.

FIRST, do a short reuse/gap pass only:
1. git status + current main SHA
2. map existing apps/web routes/components/libs
3. map corresponding Flutter feature areas
4. map corresponding backend endpoints
5. classify each area: WEB_COMPLETE / WEB_PARTIAL / WEB_MISSING / MOBILE_ONLY_BY_DESIGN / COMING_SOON_BY_LAUNCH_POLICY
6. identify the exact delta only

Do not stop after the matrix. Begin W1 immediately unless a true external blocker exists.

REUSE RULES:
- existing FastAPI business logic: reuse
- existing /v1 endpoints: reuse first
- existing Next.js pages: modify in place
- existing web api/session/access/i18n/money utilities: reuse
- Flutter workflows: behavior/design reference only
- Flutter widget code: do not port literally
- no Flutter Web production client
- no duplicate frontend business rules
- no second state machine for orders/stock/money/couriers/reconciliation/risk/automation

DESKTOP RULE:
Match mobile product semantics and brand, but use desktop-native UX:
sidebar, dense tables, filters, bulk actions, drawers, split views, keyboard-friendly forms and wide analytics.

LAUNCH GATE:
Keep Shopify, WooCommerce, Custom Website, Messenger and WhatsApp as Coming Soon and non-interactive. Preserve their implementation.
Keep Steadfast, Pathao and RedX fully active.

FREE LAUNCH:
Preserve FREE_LAUNCH_MODE=ON and BILLING_ENABLED=OFF. Do not add upgrade pressure or paid checkout.

DOMAIN:
The user has purchased a new ecomsbd domain through Cloudflare. Discover the exact value safely if present; otherwise report NEW_DOMAIN_VALUE_REQUIRED. Do not guess the TLD. Use the staged domain rollout from the master spec and keep api.scalemyprints.com compatibility during the initial web rollout.

GIT SAFETY:
No reset --hard, no clean, no force push, preserve unrelated files/stashes.

TEST SAFETY:
Targeted web tests during development. Backend tests only if backend changes. No Flutter suites or mobile rebuilds for web-only work. Full web RC checks once after phases converge.

For W1, prefer editing/reusing current shell.tsx, globals.css, ui.tsx, session/access/i18n helpers and existing auth/settings/onboarding routes rather than replacing them.

Return briefly before coding:
- current SHA
- current web URL
- exact new domain or NEW_DOMAIN_VALUE_REQUIRED
- complete/partial/missing summary
- W1 files to change
- backend change needed YES/NO
- blockers

Then implement W1.
```
