# Chat-to-order automation (Messenger + WhatsApp) — V1

Customers keep chatting in Messenger or WhatsApp. ecomsbd reads each burst of
messages into **one draft order** for the seller to review, edit and confirm in
the app's Inbox. There is no chat client and no automatic reply.

## Flow

1. Meta sends a signed webhook to `POST /v1/webhooks/integrations/meta`
   (`X-Hub-Signature-256`, verified with `META_APP_SECRET` before anything else).
2. `app.chat_orders.capture` stores each customer message once
   (`chat_messages`, unique on connection + provider + message id) and the
   sender's identity (`chat_identities`: Page + PSID, or business number +
   WhatsApp id; the id is vault-encrypted and looked up by a keyed hash).
   Page echoes, receipts, reactions and WhatsApp `STOP` replies are not stored.
3. The worker (`process_chat_messages`, every 15 s) folds messages into the
   sender's open session: an unfinished draft whose last message is within
   25 minutes. The session transcript (chronological, at most 30 messages /
   6,000 characters) is read by the **same deterministic parser** as the
   Inbox's "paste a message" (`DeterministicOrderParser`). No AI parser.
4. Items are matched to the catalogue (`app.chat_orders.matching`):
   `MATCHED` only when exactly one product fits; ties are `AMBIGUOUS` with the
   candidates; a variant is chosen only when the size/colour leaves one.
5. Draft states: `COLLECTING` → `NEEDS_INFO` → `READY_FOR_REVIEW`, then
   `CONFIRMED`, `IGNORED` or `EXPIRED`. The first time a draft becomes ready,
   one push (`CHAT_ORDER_READY`, category `ORDERS`) is raised; later messages
   update the draft silently.
6. The seller confirms from the order form (`POST /v1/chat-orders/{id}/confirm`),
   which calls `OrderService.create` — the same path as `POST /v1/orders` — with
   the draft id as the order's `client_id`. Confirming twice returns the same
   order. An `AMBIGUOUS` item must be given a product first.
7. After confirmation the sender's identity is linked to the customer, so later
   messages are recognised. A later "cancel", "address change" or "where is my
   order" message from a known customer with an open order becomes a
   **follow-up prompt** (`chat_attention_items`); nothing is changed
   automatically.

Customer matching: WhatsApp's own number may link a unique existing customer.
Messenger is never linked on a name; a phone in the chat only *suggests* a
customer until the seller confirms. Two different customers → `AMBIGUOUS`.

## Privacy and retention

* Message text is never logged, put in an exception or sent to Sentry. Sentry
  events drop stack-frame locals; SQL errors no longer echo bound parameters.
* Attachments are never downloaded; only "an attachment was sent" is kept.
* Raw messages and follow-up excerpts are deleted after 30 days; closed drafts
  after 30 days (a confirmed one lives on as its order). Hourly job
  `chat_order_housekeeping`. Account deletion erases all chat rows of the shop.
* All four tables are tenant-owned with RLS enabled and client roles revoked.

## Meta development setup (owner's own Meta Developer account)

Production config needed on the API and worker (Northflank secret group):
`META_APP_ID`, `META_APP_SECRET`, `META_WEBHOOK_VERIFY_TOKEN`, and
`PUBLIC_BASE_URL=https://api.scalemyprints.com`. Without them the app shows
Messenger and WhatsApp as "Awaiting approval" and nothing can be connected.

### Messenger (development mode)

1. developers.facebook.com → create an app (type *Business*), add **Messenger**
   and **Facebook Login for Business**.
2. Facebook Login → Valid OAuth Redirect URIs:
   `https://api.scalemyprints.com/v1/integration-callbacks/meta`.
3. Webhooks → Page → callback `https://api.scalemyprints.com/v1/webhooks/integrations/meta`,
   verify token = `META_WEBHOOK_VERIFY_TOKEN`; subscribe the `messages` field.
4. App roles: the Page admin and the test customer account must be app
   admins/developers/testers while the app is in development mode.
5. In the app: Connections & Integrations → Messenger → Connect → Continue with
   Facebook → allow `pages_show_list`, `pages_manage_metadata`,
   `pages_messaging` for the Page → choose the Page. The server subscribes the
   app to the Page (`/subscribed_apps`).
6. From the tester account, message the Page:
   `কালো পাঞ্জাবিটা লাগবে` / `XL দুইটা` / `017XXXXXXXX` / `মিরপুর ১০ ঢাকা` →
   one draft in the Inbox → Review order → Confirm → one order.

Public sellers (outside the app's roles) need **App Review** for
`pages_messaging` and `pages_manage_metadata` (and *Advanced Access*), plus
**Business Verification**.

### WhatsApp Cloud API (test number or own WABA)

1. Same Meta app → add **WhatsApp**. API setup gives a test number, its
   **Phone number ID**, the **WhatsApp Business Account ID** and a temporary
   access token (use a System User token for anything lasting).
2. Webhooks → WhatsApp Business Account → same callback URL and verify token;
   subscribe the `messages` field.
3. Add the tester's phone as an allowed recipient (test numbers only talk to
   up to five verified numbers).
4. In the app: Connections & Integrations → WhatsApp → Connect → paste the
   three values. The server checks the number with Meta and subscribes the app
   to the WABA.
5. From the allowed phone, send the same four messages → one draft → confirm.

Production WhatsApp needs a verified business, a registered production number
and a permanent System User token; outbound templates (existing V3.3 feature)
need Meta template approval.

## API

| Method | Path | Permission |
|---|---|---|
| GET | `/v1/chat-orders?status=review\|ready\|needs_info\|collecting\|closed&provider=` | order.write |
| GET | `/v1/chat-orders/summary` | order.write |
| GET | `/v1/chat-orders/{id}` | order.write |
| POST | `/v1/chat-orders/{id}/ignore` | order.write |
| POST | `/v1/chat-orders/{id}/confirm` | order.write |
| GET | `/v1/chat-orders/attention` | order.write |
| POST | `/v1/chat-orders/attention/{id}/resolve` | order.write |

Migration: `a39001` (four tables, RLS on PostgreSQL).
