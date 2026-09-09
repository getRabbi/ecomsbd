# ADR 0005 — Glass blur degrades to an opaque fallback on low-end devices

Status: Accepted · 2026-09-09 · Phase A

## Context

The locked design is built on frosted glass: every card, pill, sheet and the
bottom navigation is a translucent surface over a gradient. In Flutter that is
`BackdropFilter`, which forces a `saveLayer` and re-blurs everything behind it
on every frame.

Master spec section 53 requires the app to stay usable on low-end Android, which
is most of this market. A Home screen with a dozen frosted cards drops frames
badly on a device from a few years ago.

## Decision

Every translucent surface goes through one widget, `GlassSurface`, which reads
an `EffectsMode` from Riverpod. In `EffectsMode.reduced` the `BackdropFilter` is
omitted and the fill alpha is raised toward opaque by 55% of its remaining
distance. Colours, borders, radii and shadows are unchanged.

The mode is lowered at startup by a device probe: Android SDK ≤ 28, or the
`android.hardware.ram.low` feature flag.

## Alternatives considered

**Ship blur everywhere.** Rejected: it makes the app feel broken on the target
device class.

**Drop glass entirely.** Rejected: the UI direction is locked, and the
appearance is the product's identity.

**Blur only the top bar and bottom nav.** Rejected as a half-measure that still
pays for a `saveLayer` on the two surfaces that composite over scrolling
content — the most expensive case.

## Consequences

- The visual difference is small: the reduced surface is slightly more opaque
  and does not pick up colour from content behind it.
- Because every surface routes through one widget, the fallback is a single
  switch rather than a sweep through every screen.
- The mode is overridable, so it can become a seller-facing setting, and widget
  tests pin it to `reduced` (blur is slow and irrelevant in the test renderer).
- The device heuristic is coarse. A frame-timing probe would be more accurate
  and is the obvious refinement if reports suggest the threshold is wrong.
