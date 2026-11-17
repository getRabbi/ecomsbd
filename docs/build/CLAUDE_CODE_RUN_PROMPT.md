# Claude Code run prompts — ecomsbd V4

Two prompts. Paste **Prompt A once** (first session). After that, every new session
gets **Prompt B** only — a few lines, so you never pay to re-send the big brief.
All the rules live in the spec's §1.6, so both prompts behave the same.

**Before Prompt A:** copy `ECOMSBD_V4_MASTER_BUILD_SPEC.md` into the repo as
`docs/V4_MASTER_BUILD_SPEC.md` and commit it on `main`.

---

## Prompt A — first session (paste once)

```text
You are building ecomsbd V4 in this repo. The spec is docs/V4_MASTER_BUILD_SPEC.md.
Work autonomously, work package (WP) by WP, until every WP is DONE or BLOCKED.

STARTUP — do exactly this:
1. Read §1 of docs/V4_MASTER_BUILD_SPEC.md (search the "## 1." heading; read only §1,
   all of 1.1–1.6). Do not read the rest of the spec now.
2. If docs/build/PROGRESS.md does not exist, create it from §10 of the spec.
3. Read docs/build/PROGRESS.md. Take the first WP whose status is TODO, IN_PROGRESS or
   PARTIAL, in table order. Read ONLY that WP's section of the spec.
4. Check out that phase's branch (create it from main if missing).

Then follow §1.6 exactly: the per-WP loop, the hard rules and the credit discipline.
§1.2 invariants and §1.3 locked decisions are final — do not re-decide them.
Never invent a provider contract; mark BLOCKED and move on.
Never touch production, secrets or main.
Checkpoint and stop after 2 WPs or before context gets long.
Chat output: 3 lines per WP.

Start with STARTUP step 1.
```

---

## Prompt B — every next session

```text
Continue ecomsbd V4. Read §1 (1.1–1.6) of docs/V4_MASTER_BUILD_SPEC.md and
docs/build/PROGRESS.md. Resume the "Now" WP at its recorded step; read only that WP's
spec section. Follow §1.6 exactly. Checkpoint and stop after 2 WPs or before context
gets long. Chat output: 3 lines per WP.
```

Add one line at the end of Prompt B when you have new input, for example:
`Operator: Steadfast is live-verified; BD Courier docs are in docs/providers/bdcourier/ — set WP0.6 to TODO.`

---

## When to step in

| Claude Code says | You do |
|---|---|
| `BLOCKED: EXTERNAL_RISK_CONTRACT_REQUIRED` | Put the BD Courier API docs in `docs/providers/bdcourier/`, then Prompt B + "set WP0.6 to TODO" |
| `BLOCKED: VOICE_PROVIDER_CONTRACT_REQUIRED` | Put the chosen voice provider's docs in `docs/providers/voice/<provider>/` |
| `BLOCKED: TTS_PROVIDER_REQUIRED` | Give a TTS provider's docs, or record the intro MP3/WAV yourself |
| `BLOCKED: STATEMENT_SAMPLE_REQUIRED` | Add one real payout CSV per courier, phones masked |
| `BLOCKED: SMS_PROVIDER_CONTRACT_REQUIRED` | Add the SMS gateway docs + sender id |
| Gate "waiting on operator" | Do the pilot / live test the ledger lists; write the result in the Gates table |
| "Checkpoint saved" | New session → Prompt B |
| Same WP `PARTIAL` twice | Read its ledger note; add one line of direction to Prompt B |

## Order of the work (what to expect)

| Phase | WPs | Needs you for |
|---|---|---|
| 0 Core loop real | Docs, Sentry + funnel, LLM parser, delivery rules, invoice/label, fraud ratio, courier readiness | Anthropic key, BD Courier docs, Steadfast key, statement CSVs, Sentry DSN |
| 1 Auto-detect | DEVICE connections, rules config, capture API, Android listener, quick-order sheet | Play closed testing, 10 pilot sellers |
| 2 Confirmation + payment | Confirmation engine, policy, call queue, SMS link, automation hooks, bKash/Nagad TrxID | Receiving numbers |
| 3 Voice | VoiceTransport, call worker, intro audio, credits | Voice provider docs + account |
| 4 Autopilot | Gates, recipe, kill switch, Home card | 5-shop pilot |
| 5 Extension | MV3 extension | Chrome Web Store |
| 6 Meta on | Coverage switch | Meta App Review |
| 7 Reach | SMS gateway, Carrybee/Paperfly, own API domain, referral | Provider docs, DNS |
