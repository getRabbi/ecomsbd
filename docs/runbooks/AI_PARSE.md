# Runbook: model-assisted order parsing (V4 WP0.3)

`POST /v1/orders/parse` always runs the deterministic parser first. With the
`ai_parse_enabled` flag on for a shop, the fields that did not come from an
explicit label (name, address, items, COD) are completed by a model:

- **Trigger.** The model is asked unless every non-phone field has confidence
  ≥ 0.8, which the deterministic parser gives only to labelled fields. An empty
  field always asks.
- **Merge.** Phones stay deterministic; a model phone is added only if it is a
  valid Bangladeshi mobile that is literally in the text. A labelled value is
  kept; anything else takes the model's value. A labelled value the model reads
  differently stays and is listed in `uncertain_fields`.
- **Prefill.** With the address still empty and a phone that belongs to an
  existing customer of the shop, the customer's last delivery address is
  filled in and `address` is listed in `uncertain_fields`.
- **Safety.** The request declares no tools; the answer must match a JSON
  schema and is validated again (any extra key or out-of-range value discards
  it). The message is sent inside `<customer_message>` delimiters as untrusted
  data. Nothing in the answer is executed; the seller confirms every field.

Every model call writes one `ai_usage` row: provider, model, tokens, latency,
outcome (`ok` / `invalid` / `error`), error code, and `cost_micro_usd` — an
**estimate** from the configured per-token prices (NULL when prices are unset).
No message text, phone, name or answer is stored or logged.

## Switches

| Switch | Where | Default |
|---|---|---|
| `ai_parse_enabled` | `feature_flags` row (global or per shop), admin console | **off** |
| `AI_PARSE_API_KEY`, `AI_PARSE_MODEL` | backend env (secret / model id, e.g. `claude-haiku-5-5`) | empty: no model calls |
| `ai_parse_monthly` | plan entitlement (Free 5, then 100 / 500) | per plan |
| `AI_PARSE_TIMEOUT_SECONDS` / `AI_PARSE_MAX_INPUT_CHARS` | backend env | 8 s / 6000 chars |
| `AI_PARSE_PRICE_INPUT_PER_MTOK_USD` / `..._OUTPUT_...` | backend env, estimate | unset: cost unknown |

Answers are cached per shop for 24 h by SHA-256 of the normalised text, so a
re-parse of the same paste costs no call and no quota.

## Detection

- Parse responses carrying the warning `AI_PARSE_UNAVAILABLE` (the model could
  not help: not configured, quota exhausted, text over the cap, timeout, HTTP
  error, refusal or a schema-breaking answer).
- `ai_usage` by outcome for the last hour:
  `SELECT outcome, error_code, count(*) FROM ai_usage WHERE created_at > now() - interval '1 hour' GROUP BY 1, 2;`
  A rise in `error` (`timeout`, `rate_limited`, `http_5xx`) is a provider
  problem; a rise in `invalid` means the model or schema changed.
- Logs: `ai_parse: model unavailable` (WARNING, with `code` and `provider`) and
  `ai_parse: answer broke the schema` (WARNING). Neither carries message text.
- Cost: `SELECT tenant_id, sum(cost_micro_usd) FROM ai_usage WHERE created_at > date_trunc('month', now()) GROUP BY 1 ORDER BY 2 DESC;`

## Containment

- Turn `ai_parse_enabled` off (globally or for one shop). Parsing falls back to
  the deterministic parser immediately; order entry never depended on the model.
- A leaked key: revoke it at the provider, clear `AI_PARSE_API_KEY`, restart.
  Calls stop at once and every parse continues deterministically.
- Runaway cost from one shop: turn the flag off for that shop; the monthly quota
  already caps it.

## Recovery

- Provider outage: nothing to do — every parse in the outage returned the
  deterministic result plus `AI_PARSE_UNAVAILABLE`. Calls resume when the
  provider answers. Failed calls still spend one quota unit (the call was made).
- A schema change on the provider side (many `invalid`): keep the flag off,
  update `app/orders/parser/ai_client.py` / `llm_schema.py`, run the tests and
  `python -m app.orders.parser.eval --live --set holdout` before turning it on.

## Verification

- Backend: `python -m pytest tests/test_ai_parse.py tests/test_parse_corpus.py tests/test_parser.py -q`.
- Accuracy (operator, costs money, never in CI):
  `python -m app.orders.parser.eval --live --set holdout` prints deterministic-only
  and merged accuracy on the held-out messages
  (`backend/tests/fixtures/parse_holdout/holdout.yaml`). Gate G0.3: merged
  phones + address + items ≥ 90 %. The holdout is never used to tune the parser.
- Staging: flag on for one shop, paste an unlabelled message, check the
  response has `parser: "enhanced"` and one new `ai_usage` row with outcome `ok`.
