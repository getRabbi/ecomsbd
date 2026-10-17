# Supabase auth email templates

Supabase sends the sign-up email; FastAPI never does. These are the templates
pasted into the Supabase dashboard, kept here so the wording and design are
versioned with the code that depends on them.

## Confirm signup — `confirm-signup.html`

Sign-up is confirmed with the one-time code (`{{ .Token }}`, 8 digits in this
project), typed into the web sign-in page or the app's verify-email screen.
Both clients call Supabase `verifyOtp` with `type: 'email'`; the code is never
put in a URL, stored or logged.

**Where:** Supabase dashboard → Authentication → Emails → Templates →
**Confirm signup**.

| Field | Value |
|---|---|
| Subject | `{{ .Token }} — আপনার ecomsbd ভেরিফিকেশন কোড` |
| Message body | the whole of `confirm-signup.html` |

The code in the subject shows in the notification and lets iOS and Android
offer it as autofill. Drop it from the subject if that is not wanted.

### No links

The email carries the code and nothing to click: sellers distrust links in
email, and Brevo rewrites every link to its own tracking domain
(`*.sendibt3.com`), which looks like phishing. Keep it that way.

App versions older than 3.0.6 have no code box. A seller on one of those can
still confirm: sign in at `https://app.ecomsbd.com/sign-in` with the email and
password, enter the code there, then sign in on the phone.

### Prerequisites

- Custom SMTP (Brevo, sender `no-reply@ecomsbd.com`) under Authentication →
  Emails → SMTP Settings, and `ecomsbd.com` authenticated in Brevo (brevo-code
  TXT, `brevo1`/`brevo2._domainkey` CNAMEs with the Cloudflare proxy off,
  `_dmarc` TXT).
- Authentication → Sign In / Providers → Email → **Confirm email** on.
- Authentication → Rate Limits → emails per hour within Brevo's free 300/day.

### Checking a change

1. Use the dashboard's preview pane, then save.
2. Create an account on `https://app.ecomsbd.com/sign-in` with an inbox you can
   read. The email should come from `no-reply@ecomsbd.com` with the code in the
   subject and body.
3. Enter the code (Bangla digits work too). The workspace should open.

The logo is loaded from `https://ecomsbd.com/apple-icon.png`; keep that file
served by the web app.
