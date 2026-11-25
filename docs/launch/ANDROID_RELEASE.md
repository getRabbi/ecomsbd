# Android 3.1.0 release kit (launch run 12, 2026-11-25)

App `com.ecomsbd.app`, version `3.1.0+311` (`apps/mobile_flutter/pubspec.yaml`, PR #47). Signing, upload
and the Play Console forms are the operator's (the release key is not readable on the build machine).
Claims below come only from what is live on the server (`docs/launch/LAUNCH_STATUS.md`).

## 1. Build values checklist (before building)

| Value | Where | Must be | 2026-11-25 |
|---|---|---|---|
| `ECOMSBD_API_BASE_URL` | `apps/mobile_flutter/env/production.json` (git-ignored, CRLF) | `https://api.ecomsbd.com` | OK |
| `SUPABASE_URL` / `SUPABASE_ANON_KEY` | same | production project | set |
| `GOOGLE_CLIENT_ID_WEB` / `_IOS` | same | production OAuth clients | set |
| `PHONE_OTP_LOGIN_ENABLED` | same | `false` (no OTP SMS provider) | `false` |
| `ECOMSBD_SENTRY_DSN` | same | the Sentry project DSN, or empty = no crash reports | **empty** → Data safety "Crash logs: not collected" for this build |
| `version` | `pubspec.yaml` | `3.1.0+311` (higher than the live versionCode) | OK |
| Signing | `android/key.properties` + upload keystore | release key | operator |

## 2. Exact build command (operator's machine with the release key)

```powershell
cd E:\ecomsbd
git pull                       # main, after the launch PRs are merged
cd apps\mobile_flutter
E:\flutter\flutter\bin\flutter.bat clean
E:\flutter\flutter\bin\flutter.bat pub get
E:\flutter\flutter\bin\flutter.bat build appbundle --release --dart-define-from-file=env/production.json
# output: build\app\outputs\bundle\release\app-release.aab
```

On an 8 GB machine add `--android-skip-build-dependency-validation` only if Gradle asks, and keep
`org.gradle.jvmargs=-Xmx2g` in `android/gradle.properties` for the build (do not commit that change).

## 3. Play "What's new" (≤ 500 characters each)

**English**

```
New in 3.1:
• Auto order detect: give Notification access and order-like chats from WhatsApp Business or Business Suite become an order draft when you tap Create.
• Share a chat message to ecomsbd to open a quick order.
• Confirmation call queue: call new orders and mark the answer.
• Print invoices and 4×6 courier labels.
• Zone delivery charges added to the COD of orders made from a chat.
Free during launch.
```

**বাংলা**

```
৩.১-এ নতুন:
• অটো অর্ডার শনাক্ত: Notification access দিলে WhatsApp Business বা Business Suite-এর অর্ডারের মতো চ্যাট থেকে Create চাপলেই অর্ডারের খসড়া।
• চ্যাটের মেসেজ ecomsbd-এ শেয়ার করে কুইক অর্ডার।
• কনফার্মেশন কল কিউ: নতুন অর্ডারে কল করে উত্তর চিহ্নিত করুন।
• ইনভয়েস ও ৪×৬ কুরিয়ার লেবেল প্রিন্ট।
• চ্যাট থেকে তৈরি অর্ডারের COD-তে জোনের ডেলিভারি চার্জ যোগ হয়।
লঞ্চের সময় বিনামূল্যে।
```

## 4. Store listing

**Short description (≤ 80):**
EN: `Orders, courier booking, COD and profit for Bangladesh's online sellers.`
BN: `বাংলাদেশের অনলাইন সেলারদের অর্ডার, কুরিয়ার বুকিং, COD আর লাভের হিসাব।`

**Full description (EN):**

```
ecomsbd helps Bangladesh's online and F-commerce sellers run the whole order: from the customer's message to the money in your account. Free during launch.

ORDERS
• Create, confirm and follow every order in one list.
• Paste or share a customer's message and ecomsbd reads the phone, address and items.
• Auto order detect (optional): with Notification access, the app spots order-like chats from WhatsApp Business or Meta Business Suite on your phone and offers to make the order. Nothing is sent until you tap Create.
• Repeat-order warning and customer history.

CONFIRM BEFORE YOU SHIP
• Confirmation call queue: call each new order from your phone and mark confirmed, cancelled or no answer.

DELIVERY
• Connect your own Steadfast, Pathao or RedX account and book parcels from the order.
• Delivery charges by zone (inside Dhaka, suburbs, outside Dhaka) added to the COD of orders made from a chat message.
• Print invoices and 4×6 courier labels with Bangla text and a barcode.
• Follow delivery status on every order.

MONEY
• COD receivables by order and by courier, payouts against what was expected, and contribution profit after courier charges and returns.

The app works with the ecomsbd web workspace at app.ecomsbd.com: same shop, same orders.
Privacy policy: https://ecomsbd.com/privacy-policy
```

**Full description (BN):**

```
ecomsbd বাংলাদেশের অনলাইন ও এফ-কমার্স সেলারদের পুরো অর্ডার সামলাতে সাহায্য করে: কাস্টমারের মেসেজ থেকে টাকা হাতে আসা পর্যন্ত। লঞ্চের সময় বিনামূল্যে।

অর্ডার
• এক তালিকায় প্রতিটি অর্ডার তৈরি, কনফার্ম ও অনুসরণ।
• কাস্টমারের মেসেজ পেস্ট বা শেয়ার করুন, ecomsbd ফোন, ঠিকানা ও পণ্য বের করবে।
• অটো অর্ডার শনাক্ত (ঐচ্ছিক): Notification access দিলে ফোনেই WhatsApp Business বা Meta Business Suite-এর অর্ডারের মতো চ্যাট চিনে অর্ডার বানানোর প্রস্তাব দেয়। Create না চাপা পর্যন্ত কিছুই পাঠানো হয় না।
• রিপিট-অর্ডার সতর্কতা ও কাস্টমারের ইতিহাস।

পাঠানোর আগে কনফার্ম
• কনফার্মেশন কল কিউ: নিজের ফোন থেকে প্রতিটি নতুন অর্ডারে কল করে কনফার্ম, বাতিল বা ধরেনি চিহ্নিত করুন।

ডেলিভারি
• নিজের Steadfast, Pathao বা RedX অ্যাকাউন্ট যুক্ত করে অর্ডার থেকেই পার্সেল বুক।
• জোন অনুযায়ী ডেলিভারি চার্জ (ঢাকার ভেতরে, শহরতলি, ঢাকার বাইরে) চ্যাট থেকে তৈরি অর্ডারের COD-তে নিজে থেকে যোগ হয়।
• বাংলা লেখা ও বারকোডসহ ইনভয়েস ও ৪×৬ কুরিয়ার লেবেল প্রিন্ট।
• প্রতিটি অর্ডারে ডেলিভারির অবস্থা।

টাকা
• অর্ডার ও কুরিয়ার অনুযায়ী COD পাওনা, প্রত্যাশার সাথে পেআউট মিলানো, কুরিয়ার চার্জ ও রিটার্নের পরে লাভ।

অ্যাপটি app.ecomsbd.com-এর ওয়েব ওয়ার্কস্পেসের সাথে কাজ করে: একই দোকান, একই অর্ডার।
গোপনীয়তা নীতি: https://ecomsbd.com/privacy-policy
```

Do not claim: SMS confirm link (switched on per shop on request), Chrome extension (store review),
Autopilot, voice calls, Meta official connection, iPhone app.

## 5. Play Console order (operator)

1. Upload `app-release.aab` to **Closed testing** with the notes above.
2. App content → Privacy policy `https://ecomsbd.com/privacy-policy`; Data deletion `https://ecomsbd.com/account-deletion`.
3. App content → Data safety: answers in `docs/launch/PLAY_DATA_SAFETY.md` (crash logs "not collected" for a build with an empty DSN).
4. Review notes: the Notification access paragraph in `PLAY_DATA_SAFETY.md`.
5. After closed testing passes: Production → staged rollout 20 %, watch crashes / ANRs 2 days, then 100 %.
