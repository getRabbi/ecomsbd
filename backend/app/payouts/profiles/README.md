# Courier statement profiles (V4 WP0.7)

One YAML file per real statement layout a courier sends. A profile is added
**only** from a real file the operator placed in
`backend/tests/fixtures/statements/<provider>/` (masked: no customer phones or
names); `tests/test_statement_profiles.py` fails if a profile's sample is
missing or does not parse with its mapping. No profile ships until then
(`BLOCKED: STATEMENT_SAMPLE_REQUIRED`) — the generic column detection in
`app/payouts/statements.py` is used meanwhile.

```yaml
provider: steadfast            # steadfast | pathao | redx
profile: steadfast-payment-v1  # unique
sample: steadfast/payment-2026-11.csv   # under tests/fixtures/statements/
headers: ["Consignment ID", "Invoice", "Collected Amount", ...]  # exact header row
mapping:                       # STATEMENT_ALIASES field -> column
  amount: Collected Amount
  reference: Consignment ID
```
