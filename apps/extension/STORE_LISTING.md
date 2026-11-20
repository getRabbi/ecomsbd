# Chrome Web Store listing — ecomsbd order capture

Build: `cd apps/extension && npm ci && ECOMSBD_API_BASE_URL=<api> ECOMSBD_WEB_BASE_URL=<web> npm run build`,
then zip the **contents** of `dist/` and upload it. Version = `package.json` `version`.

## English

**Name:** ecomsbd order capture

**Short description (≤ 132 chars):**
Turn a Messenger or WhatsApp Web chat into an ecomsbd order: tap Capture, check the order, save.

**Description:**
ecomsbd order capture is for sellers who use ecomsbd to manage their online shop.

- Link the extension to your shop with a 6-digit code from ecomsbd on the web (Settings → Browser extension).
- Open a customer's chat in Messenger, Meta Business Suite or WhatsApp Web and tap **Capture** in the side panel.
- The extension shows what it found (phone, address, items) and opens the order in ecomsbd, where you check the name, COD amount and items and save it.
- Phone numbers in the open chat are highlighted.
- Where the page cannot be read, select the customer's message and tap Capture.

The extension reads only the chat you have open, and only when you tap Capture. It never reads other chats, never runs in the background on your chats, and never sends or replies to messages.

**Category:** Productivity · **Language:** English, Bangla

## বাংলা

**নাম:** ecomsbd order capture

**ছোট বিবরণ:**
Messenger বা WhatsApp Web-এর চ্যাট থেকে ecomsbd অর্ডার: Capture চাপুন, দেখে নিন, সেভ করুন।

**বিবরণ:**
ecomsbd দিয়ে অনলাইন শপ চালান এমন সেলারদের জন্য।

- ওয়েবে ecomsbd-এর Settings → Browser extension থেকে ৬ সংখ্যার কোড নিয়ে এক্সটেনশনটি আপনার শপের সাথে যুক্ত করুন।
- Messenger, Meta Business Suite বা WhatsApp Web-এ কাস্টমারের চ্যাট খুলে সাইড প্যানেলে **Capture** চাপুন।
- এক্সটেনশন যা পেয়েছে (ফোন, ঠিকানা, পণ্য) দেখায় এবং ecomsbd-এ অর্ডারটি খোলে; সেখানে নাম, COD ও পণ্য দেখে সেভ করুন।
- খোলা চ্যাটের ফোন নম্বর হাইলাইট হয়।
- পেজ পড়া না গেলে কাস্টমারের মেসেজ সিলেক্ট করে Capture চাপুন।

এক্সটেনশন শুধু আপনার খোলা চ্যাটটি পড়ে, আর শুধু Capture চাপলেই। অন্য চ্যাট পড়ে না, ব্যাকগ্রাউন্ডে চ্যাট পড়ে না, কোনো মেসেজ পাঠায় বা উত্তর দেয় না।

## Privacy disclosure (Developer Dashboard → Privacy practices)

**Single purpose:** capture an order from the chat the seller has open into the seller's own ecomsbd shop.

**Permission justifications:**

| Permission | Why |
|---|---|
| `storage` | Keeps this browser's link to the shop (a capture-only key), the detection rules and the panel language. No message text is stored. |
| `sidePanel` | The Capture panel. |
| `alarms` | Refreshes the detection rules (daily) and tells the shop the extension is still linked (every 6 h). |
| Host `https://<api host>/*` | Sends a capture to the shop's ecomsbd account. |
| Content scripts on `business.facebook.com`, `www.messenger.com`, `web.whatsapp.com` | Read the open chat when the seller taps Capture, and highlight phone numbers in it. |

**Data collected** (sent only when the seller taps Capture): the open chat's recent messages or the selected
text, and the chat's display name — "Personally identifiable information" and "Personal communications".
Used only to make the seller's order draft in their own shop. Not sold, not used for advertising or
credit-worthiness, not shared with third parties. Remote code: none (all code ships in the package; the
server sends only data — detection rules and page selectors).

**Privacy policy URL:** the ecomsbd privacy policy (same as the apps).

**বাংলা সারাংশ:** Capture চাপলেই শুধু খোলা চ্যাটের সাম্প্রতিক মেসেজ বা সিলেক্ট করা লেখা আর চ্যাটের নাম আপনার নিজের
ecomsbd শপে যায়, অর্ডারের ড্রাফট বানাতে। বিক্রি বা বিজ্ঞাপনে ব্যবহার হয় না, কাউকে দেওয়া হয় না।

## Screenshots to take (1280×800)

1. Side panel, linked, beside a WhatsApp Web chat with a highlighted number (test data only, `017000000xx`).
2. After Capture: "What was found" + Open in ecomsbd.
3. ecomsbd web quick-order page with the composer open.
4. The selection fallback notice.
