# Chrome Web Store submission kit — ecomsbd order capture 4.0.0 (launch run 12, 2026-11-25)

Listing text, permission justifications and the privacy disclosure are in
`apps/extension/STORE_LISTING.md` (EN + BN); this page adds the production build, the package and
the exact dashboard answers.

## Package

| | |
|---|---|
| Built from | `main` @ 24c0f0b `apps/extension` (no change since), `npm ci && npm run build`, tests 31 passed |
| Build env | `ECOMSBD_API_BASE_URL=https://api.ecomsbd.com` `ECOMSBD_WEB_BASE_URL=https://app.ecomsbd.com` |
| Zip (contents of `dist/`, `manifest.json` at the root, forward-slash paths) | `E:\ecomsbd\release\extension-4.0.0\ecomsbd-extension-4.0.0-chrome-web-store.zip` (30,882 bytes, sha256 `5b95e811eb5d3e30297998c1db68edcca82523e9df0a5525bf3ede92e21a6c19`) — git-ignored folder, not in the repo |
| Manifest | MV3, version 4.0.0, min Chrome 116; permissions `storage`, `sidePanel`, `alarms`; host `https://api.ecomsbd.com/*`; content scripts on `business.facebook.com`, `www.messenger.com`, `web.whatsapp.com` |

Rebuild the same way for any later version; bump `package.json` `version` first.

## Before submitting: unpacked check (10 min)

1. `chrome://extensions` → Developer mode → Load unpacked → `E:\ecomsbd\apps\extension\dist`.
2. ecomsbd web → Settings → Browser extension → get the 6-digit code; paste it in the side panel.
3. Open a WhatsApp Web chat with a test message (`LAUNCH TEST`, your own test number), select it, tap **Capture**.
4. "Open in ecomsbd" lands on `/quick-order?draft=…`; save or ignore the draft.
5. Unlink from the side panel.

(The server side of 2–5 was checked in run 12 with a simulated pairing: Launch log L2.8.)

## Developer Dashboard answers

- **Store listing:** name, short + full description (EN default, BN as a second language) from
  `STORE_LISTING.md`; category Productivity; 4 screenshots 1280×800 (list in `STORE_LISTING.md`, test data only).
- **Privacy practices → Single purpose:** "Capture an order from the chat the seller has open into the seller's own ecomsbd shop."
- **Permission justifications:** the table in `STORE_LISTING.md` (`storage`, `sidePanel`, `alarms`, host permission, content scripts).
- **Remote code:** No.
- **Data usage — collected:** Personally identifiable information (customer name / phone inside the captured text), Personal communications (the captured messages). Not: health, financial, authentication, location, web history, user activity, website content beyond the captured chat.
- **Certifications (tick all three):** not sold to third parties; not used or transferred for purposes unrelated to the single purpose; not used to determine creditworthiness or for lending.
- **Privacy policy URL:** `https://ecomsbd.com/privacy-policy` (section 6 describes the extension, from 2026-11-25).
- **Distribution:** Public, all regions (or Unlisted for a pilot first).

After approval: write the approval date in `docs/build/PROGRESS.md` → Gates G5, then update the public
site line "coming to the Chrome Web Store" to the store link.
