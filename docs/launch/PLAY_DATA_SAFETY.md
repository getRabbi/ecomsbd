# Play Console: Data safety answers + in-app disclosure (Android 3.1.x)

Written 2026-11-25 (launch run 12) from the code on `main`, not from memory. App `com.ecomsbd.app`.
Re-check this page whenever a feature that sends data off the phone changes.

Sources read: `apps/mobile_flutter/android/app/src/main/AndroidManifest.xml` (permissions),
`lib/core/crash_reporting.dart` (Sentry), `lib/l10n/intake_strings.dart` (disclosure),
`backend/app/api/v1/intake.py` (`CaptureBatch` / `ManualCapture`: what a capture uploads),
`backend/app/intake/coverage.py` + migration `a41001` (default chat apps),
`backend/app/product_events/` (first-party milestones), `backend/app/confirmations/links.py`
(SMS link), `backend/app/sms_providers/alpha_sms.py` (what Alpha SMS receives).

## Store URLs

| Field | Value |
|---|---|
| Privacy policy | `https://ecomsbd.com/privacy-policy` (Bangla: `/privacy-policy/bn`) |
| Account deletion (web resource) | `https://ecomsbd.com/account-deletion` |
| In-app deletion path | Settings → Your data and privacy → Closing your account (14-day cooling-off, cancellable) |

## Data safety form

**Does your app collect or share any of the required user data types?** Yes.
**Is all of the user data collected by your app encrypted in transit?** Yes (HTTPS only).
**Do you provide a way for users to request that their data is deleted?** Yes (in app + the URL above).

### Data types collected

"Collected" = leaves the phone to ecomsbd's backend. All are processed for the seller's own shop.
Nothing is sold or used for advertising.

| Play category → type | Collected | Shared | Optional? | Purposes | What it is in ecomsbd |
|---|---|---|---|---|---|
| Personal info → Name | Yes | No | Required | App functionality, Account management | Seller's display name; customer names the seller enters or captures |
| Personal info → Email address | Yes | No | Required | Account management, App functionality | Seller sign-in email (Supabase Auth); customer email if the seller adds one for messaging |
| Personal info → Phone number | Yes | No | Required | App functionality, Fraud prevention / security | Customer and supplier phones the seller enters, pastes or captures; pickup contact |
| Personal info → Address | Yes | No | Required | App functionality | Customer delivery addresses, shop pickup address |
| Personal info → User IDs | Yes | No | Required | Account management, App functionality | Account / shop identifiers, linked Google sign-in identifier |
| Financial info → Other financial info | Yes | No | Required | App functionality | COD amounts, courier charges, payouts, expenses the seller records (not payment credentials) |
| Messages → Other in-app messages | Yes | No | **Optional** | App functionality | Chat text the seller pastes or shares, and — with Auto order detect — a chat's notification text when the seller taps **Create** |
| App activity → App interactions | Yes | No | Required | Analytics | First-party milestones per shop (shop created, first order, first courier booking, first confirmation call…), no customer details |
| App activity → Other user-generated content | Yes | No | Required | App functionality | Order notes, tags, follow-ups, templates |
| Files and docs | Yes | No | Optional | App functionality | Spreadsheet / statement files the seller chooses to upload |
| App info and performance → Crash logs, Diagnostics | **Only if the build has `ECOMSBD_SENTRY_DSN`** | No | Required when on | App functionality (stability) | Sentry: error type, stack trace, app/device version; phones masked, message text dropped, `sendDefaultPii` off |
| Device or other IDs | Yes | No | Required | App functionality | Firebase Cloud Messaging push token, app installation id |

Not collected: precise or approximate location, contacts, SMS / call log, photos / videos, audio,
calendar, health, web browsing, installed-apps list, advertising id.

**Crash logs row:** `apps/mobile_flutter/env/production.json` has `ECOMSBD_SENTRY_DSN` **empty** on
2026-11-25, so a 3.1.x build made from it sends no crash reports: answer "not collected" for that
build. If you put a DSN in before building, answer "collected" as in the table.

### Why "Shared: No" everywhere

Play's definition excludes (a) service providers processing on our behalf and (b) transfers the
user starts and expects. That covers every recipient today:

- **Couriers** (Steadfast, Pathao, RedX; Carrybee when switched on): receive recipient name, phone,
  address, COD, items **only when the seller books a parcel** — user-initiated, expected.
- **Alpha SMS (sms.net.bd)**: service provider. Receives the customer's phone and the SMS text when a
  shop sends a confirm link or has SMS order updates turned on.
- **Supabase, Northflank, Cloudflare, Firebase Cloud Messaging, Sentry (if on)**: service providers.

If a feature ever sends data to a third party for its own purposes, this answer changes.

### Security practices

- Data encrypted in transit: **Yes**.
- Users can request deletion: **Yes**.
- Committed to Play Families policy: **No** (not for children).
- Independent security review: **No**.

## Notification access (Auto order detect)

The app declares no runtime permission for it: the listener service is guarded by
`BIND_NOTIFICATION_LISTENER_SERVICE`, and the seller grants **Notification access** in Android settings
(Settings → Auto order detect → Turn on Notification Access, "ecomsbd order detect"). Manifest
permissions are only `INTERNET` and `POST_NOTIFICATIONS` (+ WorkManager's merged ones). No
Accessibility service, no `SYSTEM_ALERT_WINDOW`, no `READ_SMS`, no `READ_CALL_LOG` (spec rule L6).

What it does (from the code):

- Reads notifications only from the chat apps the seller switches on. Defaults: **WhatsApp Business**
  and **Meta Business Suite** on; **WhatsApp** and **Messenger** off until the seller turns them on.
  The list comes from the server rule set and can only be changed there.
- Reads the chat name and message text; keeps them on the phone, encrypted, up to 25 minutes, then
  deletes them.
- Scores on the phone. An order-like chat shows a heads-up "order mone hocche"; the lock-screen text
  shows no name or number.
- Uploads **only when the seller taps Create** (`ManualCapture`, source `DEVICE_NOTIFICATION`): the
  message texts + send times, the app package, the chat name (stored as a keyed hash + encrypted
  value) and the sender name. Automatic upload (`CaptureBatch`) exists only for shops that turned
  on Autopilot, which is off for every shop in this launch.
- Stop: turn off Notification access, or Unlink in the app.

### In-app disclosure (shown before linking; copy exactly as shipped)

English (`intake.disclosure*`):

> **What this reads and sends**
> Reads: notifications from the chat apps you turn on below — the chat name and the message text. Nothing else on your phone.
> Keeps: those messages stay on this phone, encrypted, for up to 25 minutes so an order typed over several messages is read together. Then they are deleted.
> Sends: nothing by itself. When a chat looks like an order (a phone number plus order words), you get a notification. Only when you tap Create is that chat sent to ecomsbd to make a draft.
> Stop: turn off Notification Access for ecomsbd in phone settings, or tap Unlink below.

Bangla:

> **কী পড়ে, কী পাঠায়**
> পড়ে: নিচে যে চ্যাট অ্যাপগুলো চালু করবেন, শুধু সেগুলোর নোটিফিকেশন — চ্যাটের নাম আর মেসেজের লেখা। ফোনের আর কিছু না।
> রাখে: মেসেজগুলো এই ফোনেই এনক্রিপ্ট করে সর্বোচ্চ ২৫ মিনিট থাকে, যাতে কয়েকটা মেসেজে লেখা অর্ডার একসাথে পড়া যায়। তারপর মুছে যায়।
> পাঠায়: নিজে থেকে কিছুই না। কোনো চ্যাট অর্ডারের মতো দেখালে (ফোন নম্বর আর অর্ডারের কথা) আপনি একটা নোটিফিকেশন পাবেন। শুধু Create চাপলে সেই চ্যাট ড্রাফট বানাতে ecomsbd-তে যায়।
> বন্ধ করতে: ফোনের সেটিংসে ecomsbd-এর Notification Access বন্ধ করুন, অথবা নিচে আনলিংক চাপুন।

### Text for Play review notes (closed testing → production)

> ecomsbd is a seller tool for Bangladeshi online shops. The optional "Auto order detect" feature uses
> Android Notification access (NotificationListenerService) to read notifications from the chat apps
> the seller chooses (default: WhatsApp Business, Meta Business Suite) so the app can show a heads-up
> when a customer's message looks like an order. Detection runs on the device. Nothing is uploaded
> unless the seller taps "Create" on that alert; then the message text is sent to the seller's own
> shop to prefill an order draft. A disclosure screen explains this before the seller links the
> phone; the seller can unlink or revoke Notification access at any time. The app requests no SMS,
> call-log, accessibility, overlay or location permission.
