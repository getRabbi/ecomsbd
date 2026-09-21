# V2.3 external capabilities

- Messaging: email uses the existing Resend transport only when configured; each shop must enable it. WhatsApp, Messenger and SMS require official provider configuration/contracts and remain disabled.
- Resend contract: https://resend.com/docs/api-reference/emails/send-email and https://resend.com/docs/dashboard/emails/idempotency-keys (verified 2026-09-21). Delivery stops after 23 hours because provider deduplication lasts 24 hours; ambiguous expired deliveries require investigation, not resend.
