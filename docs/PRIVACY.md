# Privacy and data retention

**Master spec sections 33, 51, 100, 101, 130.**

This document says what ecomsbd stores about people, who can see it, what
happens when a seller leaves, and — the part most policies leave vague — what
is *not* deleted and why.

Everything here is enforced in code and covered by tests. Where a promise
depends on something not yet built, it says so.

---

## Whose data is this

Two different people, with different rights:

| | The seller | Their customer |
|---|---|---|
| Relationship | Our user. Signs in, pays us. | **Not our user.** They bought something from a shop; we hold their details on that shop's behalf. |
| Consequence | They control their own account and their own data. | We store the minimum needed for the shop to fulfil the order, and never use it for anything else. |

**There is no cross-seller blacklist and never will be** (section 100). A
customer flagged by one shop is that shop's private note. A shared "bad
customer" database across sellers would be a defamation engine built out of
other people's disputes.

---

## What is stored about a customer

| Field | How it is stored | Why it exists |
|---|---|---|
| Phone number | **AES-GCM encrypted**, bound to a per-purpose context. Plus a keyed HMAC for exact-match lookup and a stored mask (`01712****78`). | The courier needs it to deliver. The HMAC lets the shop find a repeat customer without the database holding a searchable phone list. |
| Name | Plain text | Printed on the parcel. |
| Address | Plain text, with the seller's **original wording preserved** | Delivery. A courier's normalisation never overwrites what the seller wrote. |
| Order and delivery counts | Integers | The shop's own trading history with that person. |
| Flag and reason | Enum plus free text, **shop-private** | The seller's own note. Advisory only; nothing is enforced. |

**Never stored:** national ID, date of birth, location history, contacts,
anything from the seller's phone. The Android manifest requests `INTERNET` and
nothing else, and each permission that is absent is listed with the feature that
would justify it.

---

## Who can see a phone number

| Who | What they see |
|---|---|
| The shop's Owner | The full number. It is their customer. |
| Manager / Packer | The full number where their role allows customer access — they pack the parcels. |
| Accountant / Viewer | Masked. |
| **Platform support** | **Masked, always, by default.** |
| Logs, audit rows, Sentry, metrics, support bundles | Masked, centrally, in the formatter. |

Support can reveal exactly one customer's number at a time, and only with:

- the `PII_REVEAL` permission, which only a `SUPERADMIN` holds;
- a stated reason of at least eight characters;
- an audit entry written **and committed before the number is returned**;
- a client-side re-hide timeout.

There is no bulk variant and no way to reach it from a listing. A test asserts
that an `OPS` admin is refused.

---

## Encryption

| | |
|---|---|
| At rest, application level | AES-GCM with a versioned key id. Envelopes are `version|nonce|ciphertext`, so a key rotation can run alongside the old key rather than requiring a flag day. |
| Context binding | Ciphertext is bound to a purpose (`customer.phone`, `user.phone`, `otp.phone`). A blob moved between tables will not decrypt. |
| Phone search | Keyed HMAC with a **separate** secret. Leaking the search key does not decrypt anything; leaking the encryption key does not make the table searchable. |
| Refresh tokens, admin tokens, export links, push tokens, purchase tokens | Stored hashed. Never recoverable, only comparable. |
| In transit | HTTPS. Production refuses to start with a non-https `PUBLIC_BASE_URL`. |

---

## Retention

| Data | Kept | Why |
|---|---|---|
| Orders, consignments, receivables, payouts, ledger, profit snapshots | **For the life of the account, and after deletion in anonymised form** | The record of money that moved between a seller, a courier and a customer. Section 81 makes the ledger append-only; deleting entries would break the invariants every figure in the product rests on. |
| Customer contact details | Life of the account. **Anonymised on deletion.** | Needed for fulfilment; not needed afterwards. |
| OTP challenges | 5 minutes live; rows retained short-term for abuse investigation | Codes are hashed and never stored in clear text. |
| Audit log | Indefinitely | It is the record of who did what. An audit log with a retention policy is an audit log with a horizon over which nothing can be answered. |
| Export files | 15 minutes by default, then the content is dropped | A generated CSV is a copy of the shop's data behind a token. The **record** of who exported what survives the file. |
| Payout source statements | Life of the account | Section 81.4. It is what lets support explain a settlement months later. |
| Logs | Per the deployment's retention; redacted at write time | — |

---

## Account deletion

A seller can request deletion from the app. The workflow is section 100's, in
this order:

1. **Ownership is re-verified** against an active `OWNER` membership — not read
   from the caller's token claims.
2. **Scheduled, not immediate.** A 14-day cooling-off window, during which
   **access continues normally**. A half-dismantled shop is the worst thing to
   hand someone who changes their mind on day three. The seller can cancel.
3. After the window: subscriptions cancelled, sessions revoked, devices
   revoked and their push tokens dropped, provider integrations disabled.
4. **Anonymisation.** Customer names, phone ciphertext, search hashes, masks,
   notes and addresses are destroyed. Every customer becomes `Customer 1`,
   `Customer 2`. The shop name becomes `Deleted shop <prefix>`. The owner's
   own contact details are anonymised **unless they own another shop**, which
   is left alone — deleting one business is not a request to delete another.
5. **Financial records are retained, anonymised.** See below.
6. A final audit entry records every step and its counts.

### What is not deleted, and why

The ledger, receivables, payouts, consignment outcomes and profit snapshots
stay. After anonymisation they contain **no personal data** — no name, no
number, no address — only amounts, dates and internal identifiers.

They stay because:

- they record money that moved between **three** parties, and one of them
  asking us to forget does not settle the other two;
- section 81 makes the ledger append-only, and a correction is a reversal
  rather than a deletion. Removing rows would break the reconciliation
  invariants that make every figure in the product trustworthy;
- the seller may need them. A courier dispute six months later is answered from
  these records.

This is stated to the seller **before** they confirm, in `GET
/v1/account/privacy`, in the words the code actually implements.

---

## A seller's own data is never held hostage

Section 51, and it is the rule the plan model is built around.

A subscription that lapses restricts **automation, volume, collaboration and
advanced analysis**. It does not restrict reading. A shop that drops to Free
keeps full access to its orders, customers, products, payouts, reconciliation
cases, money summary and profit history.

There is a test for each of those endpoints by name, and it runs on every build.

Export is available on Starter and Pro. A Free shop that wants a copy can
upgrade for one month, export, and downgrade — and their data will still be
there.

---

## Subprocessors

| | Status |
|---|---|
| Hosting / database | Operator's choice (VPS or Supabase). **Not yet provisioned.** |
| Cloudflare R2 | `R2_CREDENTIALS_REQUIRED`. Statements and exports live in the database until then. |
| Google Play | `PLAY_SERVICE_ACCOUNT_REQUIRED`. Would receive purchase tokens, never customer data. |
| bKash | `BKASH_MERCHANT_SETUP_REQUIRED`. Would receive the seller's payment details, never their customers'. |
| SMS gateway | `SMS_PROVIDER_REQUIRED`. Would receive the seller's number for OTP and, if SMS notifications are enabled, customers' numbers for order updates. |
| FCM | `FCM_CREDENTIALS_REQUIRED`. Push tokens only. |
| Sentry | `SENTRY_CONFIGURATION_REQUIRED`. Configured with `send_default_pii=False` and a redaction hook. |

**This list must be published to sellers before the first paying customer, and
updated whenever a subprocessor is added.** A privacy policy that does not name
who else touches the data is not one.
