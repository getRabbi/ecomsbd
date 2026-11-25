# সেলার অনবোর্ডিং — ecomsbd (লঞ্চ, ২০২৬-১১)

বাংলা আগে, ইংরেজি নিচে। স্ক্রিনের নাম অ্যাপে যেমন দেখায় তেমনই লেখা (বাংলা / English)।
যেসব ফিচার এখনো সব দোকানে চালু নয়, সেগুলো "অনুরোধে" বা "শীঘ্রই" লেখা আছে —
সর্বশেষ অবস্থা `docs/launch/LAUNCH_STATUS.md`-এ।

## বাংলা

### ১. সাইন আপ (২ মিনিট)
1. ওয়েবে `https://app.ecomsbd.com/sign-in` খুলুন → **অ্যাকাউন্ট তৈরি করুন**, অথবা Google দিয়ে চালিয়ে যান। Android অ্যাপেও একই অ্যাকাউন্ট চলে।
2. দোকানের নাম আর ব্যবসার ধরন দিন। লঞ্চের সময় সব ফিচার বিনামূল্যে, কার্ড লাগে না।

### ২. Steadfast যুক্ত করুন
1. **সেটিংস → কুরিয়ার** (ওয়েবে বাম মেনু **কুরিয়ার**) → **Steadfast যুক্ত করুন**।
2. Steadfast প্যানেল থেকে আপনার নিজের API Key আর Secret Key কপি করে বসান → সেভ। ecomsbd একবার ব্যালান্স পড়ে যাচাই করে; ভুল key হলে সেভ হয় না।
3. Pathao / RedX-ও একইভাবে যুক্ত করা যায়।

### ৩. ডেলিভারি চার্জ সেট করুন
1. **সেটিংস → ডেলিভারি নিয়ম**।
2. ঢাকার ভেতরে, শহরতলি, ঢাকার বাইরে — তিনটি চার্জ দিন; ডিফল্ট কুরিয়ার Steadfast বেছে নিন → সেভ।
3. এরপর চ্যাটের মেসেজ থেকে বানানো অর্ডারে (পেস্ট, শেয়ার, অটো শনাক্ত) পণ্যের দামের সাথে জোনের চার্জ COD-তে নিজে যোগ হয়। চ্যাটে কাস্টমার নিজে টাকা বললে সেটা বদলায় না, অমিল হলে সতর্ক করে।

### ৪. ফোন লিংক করুন (অটো অর্ডার শনাক্ত, Android ৩.১)
1. Android অ্যাপ → **সেটিংস → অটো অর্ডার শনাক্ত** → "কী পড়ে, কী পাঠায়" পড়ে নিন → **এই ফোন লিংক করুন**।
2. Notification Access চালু করুন ("ecomsbd order detect")।
3. কোন চ্যাট অ্যাপ দেখবে বেছে নিন (WhatsApp Business আর Business Suite আগে থেকেই চালু)।
4. কাস্টমার অর্ডারের মতো মেসেজ দিলে "order mone hocche" নোটিফিকেশন আসে → **Create** চাপলেই অর্ডারের খসড়া হয়; না চাপলে কিছুই পাঠানো হয় না।
5. অন্য অ্যাপ থেকেও মেসেজ সিলেক্ট করে **Share → ecomsbd order** দিলে কুইক অর্ডার খোলে।

### ৫. কনফার্মেশন কিউ
1. অর্ডার বানানোর সময় **কনফার্ম করতে কাস্টমারকে কল করুন** (Confirm & call customer) বেছে নিন; চ্যাটে কাস্টমার আগেই কনফার্ম করলে **চ্যাটেই কনফার্ম হয়েছে**।
2. **কল তালিকা** (Call queue) খুলুন → কাস্টমারকে নিজের ফোন থেকে কল করুন → **কনফার্ম**, **বাতিল** বা **ধরেনি** চাপুন।
3. রাত ৯টা থেকে সকাল ৯টা কল তালিকা চুপ থাকে (ডেলিভারি নিয়মে বদলানো যায়)।
4. SMS কনফার্ম লিংক (কাস্টমার নিজে Confirm/Cancel চাপেন): **অনুরোধে** দোকান অনুযায়ী চালু হয় — সাপোর্টে বলুন।

### ৬. কুরিয়ারে বুক করুন
1. **অর্ডার** → কনফার্ম হওয়া অর্ডার সিলেক্ট → **কুরিয়ারে বুক করুন** (একটা বা একসাথে অনেকগুলো)।
2. বুকিং শুধু আপনি চাপলেই হয়; অর্ডার সেভ করলে কুরিয়ারে কিছু যায় না।
3. Steadfast-এ বুকিং API দিয়ে বাতিল করা যায় না — দরকার হলে Steadfast প্যানেল থেকে বাতিল করুন।

### ৭. প্রিন্ট
1. অর্ডার খুলে **চালান প্রিন্ট করুন** (A4) বা **লেবেল প্রিন্ট করুন** (৪×৬, বারকোডসহ)।
2. অনেক অর্ডার সিলেক্ট করে একসাথে প্রিন্ট করা যায়। বাংলা লেখা ঠিকমতো আসে।

### ৮. COD ট্র্যাকিং
1. Steadfast-এর স্ট্যাটাস প্রতি ~১০ মিনিটে আপডেট হয় (Steadfast-এর webhook নেই)।
2. **টাকার সারসংক্ষেপ** → কোন কুরিয়ারের কাছে কত COD পাওনা, কতটা দেরি।
3. পেআউট এলে কুরিয়ারের স্টেটমেন্ট (CSV/Excel) আপলোড করে মিলিয়ে নিন।
4. কাস্টমারকে SMS-এ অর্ডারের আপডেট: **মেসেজ** (Messaging) → চ্যানেলে SMS চালু করলে তবেই যায়।

### ৯. Chrome এক্সটেনশন (শীঘ্রই Chrome Web Store-এ)
ওয়েবে **সেটিংস → ব্রাউজার এক্সটেনশন** থেকে ৬ সংখ্যার কোড → এক্সটেনশনের সাইড প্যানেলে বসান → WhatsApp Web / Messenger-এ চ্যাট খুলে **Capture**।

## English (short)

1. **Sign up** at `https://app.ecomsbd.com/sign-in` → Create account (or Google). Free during launch.
2. **Couriers** → **Connect Steadfast** → paste your own API key + secret (checked with a balance read).
3. **Settings → Delivery rules**: three zone charges + default courier Steadfast. Orders made from a chat message get items + zone charge as COD.
4. **Android 3.1 → Settings → Auto order detect → Link this phone** → Notification access → pick chat apps. Tap **Create** on the "order mone hocche" alert to make a draft; nothing is sent otherwise. Share-to-ecomsbd opens a quick order.
5. **Call queue**: create orders with **Confirm & call customer**, call from your phone, mark confirmed / cancelled / Dhoreni. SMS confirm link: per shop, on request.
6. **Orders → Book with courier** (single or bulk). Saving an order never books. Steadfast bookings cannot be cancelled through the API.
7. **Print invoice** (A4) / **Print label** (4×6, barcode), single or bulk.
8. **COD**: Steadfast status every ~10 min (no webhook); **Money overview** shows receivables; upload payout statements to reconcile. SMS order updates go out only after you switch SMS on in **Messaging → Channels**.
9. **Browser extension** (coming to the Chrome Web Store): Settings → Browser extension → 6-digit code → Capture in WhatsApp Web / Messenger.
