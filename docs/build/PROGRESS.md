# V4 build ledger
Spec: docs/V4_MASTER_BUILD_SPEC.md · Started: 2026-11-17 · Branch: v4/phase-0

## Now
WP: WP0.1 — Docs truth   Step: finish IMPLEMENTATION_STATUS top, run DoD checks, commit, push v4/phase-0
- Package id `com.ecomsbd.app` in README, RELEASE_READINESS, V3_CLOUD_HANDOFF, PRODUCTION_ENV_SETUP, env/README (old id only in "History" notes)
- Rewrite top of IMPLEMENTATION_STATUS: V3.1–V3.8, chat-to-order, web app, Free Launch Mode, measured test counts, link V4 spec
- Create this ledger from spec §10
- Docs-only WP: no flag, no env var, no migration, no runbook
- DoD: backend pytest + ruff + format + mypy once at WP end

## Status
| WP | Status | Commit | Verified | Notes |
|---|---|---|---|---|
| WP0.1 | IN_PROGRESS | | | |
| WP0.2 | TODO | | | |
| WP0.3 | TODO | | | |
| WP0.4 | TODO | | | |
| WP0.5 | TODO | | | |
| WP0.6 | TODO | | | |
| WP0.7 | TODO | | | |
| WP1.1 | TODO | | | |
| WP1.2 | TODO | | | |
| WP1.3 | TODO | | | |
| WP1.4 | TODO | | | |
| WP1.5 | TODO | | | |
| WP2.1 | TODO | | | |
| WP2.2 | TODO | | | |
| WP2.3 | TODO | | | |
| WP2.4 | TODO | | | |
| WP2.5 | TODO | | | |
| WP3.1 | TODO | | | |
| WP3.2 | TODO | | | |
| WP3.3 | TODO | | | |
| WP3.4 | TODO | | | |
| WP4   | TODO | | | |
| WP5   | TODO | | | |
| WP6   | TODO | | | |
| WP7.1 | TODO | | | |
| WP7.2 | TODO | | | |
| WP7.3 | TODO | | | |
| WP7.4 | TODO | | | |

Statuses: TODO · IN_PROGRESS · DONE · BLOCKED: <CODE> · PARTIAL: <what remains>

## Gates
| Gate | Met? | Evidence |
|---|---|---|
| G0 | | |
| G1 | | |
| G2 | | |
| G3 | | |
| G4 | | |
| G5 | | |

## Operator actions waiting (things only Rabbi can do)
- [ ] ...

## Decisions made during build (spec was silent)
- 2026-11-17 setup: local `main` was 44 commits behind `origin/main`; fast-forwarded it (no local commits lost) before adding the spec, so phase branches start from current code.
- 2026-11-17 setup: the ledger is committed on `v4/phase-0`, not `main`, so `main` only carries the setup commit.
- 2026-11-17 env: `backend/.venv` points at another Windows user's Python; this machine uses `C:\Users\userw\.venvs\ecomsbd-backend` (uv, `pip install -e .[dev]`). Git runs with `-c safe.directory=E:/ecomsbd` (repo owned by another SID); no global config changed.

## Session log (newest first, one line each)
- 2026-11-17 setup: spec + run prompt committed on main (2c2783d, not pushed); v4/phase-0 created
