# ADR 0003 — The type scale departs from the prototype's pixel sizes

Status: Accepted · 2026-09-09 · Phase A

## Context

The supplied HTML prototype is the locked visual source of truth. Its body copy
sits at 8–10.5 CSS px and its micro-labels at 7–8.5 px. Those sizes work in the
prototype because it is a desktop mock viewed at a scaled-down phone width.

Rendered on a real 360dp Android screen, 8.5px is below the size at which text
is comfortably legible, and below what master spec sections 52 and 124 require
("large tap targets", "scalable text").

## Decision

Preserve the prototype's **hierarchy** exactly and lift every size to a readable
floor. `EcomsbdType` defines:

| Role | Prototype | ecomsbd |
|---|---|---|
| Hero money | 37px | 36 |
| Hero / page title | 26–30px | 25–28 |
| Section title | 13–14px | 16 |
| Metric value | 15–18px | 19 |
| Body | 10.5px | 13 |
| Caption | 8.5–9px | 11.5 |
| Eyebrow (uppercase) | 7.5–8.5px | 10 |

The relative ordering is unchanged: hero money still dominates, section titles
still read as titles, supporting copy still recedes. Only the absolute floor
moved.

System text scaling is honoured and clamped to 0.85×–1.35× in `EcomsbdApp`;
above that the hero money figure overflows its card even with `FittedBox`.

## Consequences

- Screens are slightly taller than the prototype at the same width. Every screen
  is a scrolling surface, so this costs nothing structurally.
- Cards that were tight in the prototype needed responsive column counts rather
  than fixed grids — hence `ResponsiveGrid`, which drops from four columns to
  two at 360dp instead of squeezing.
- A pixel-for-pixel comparison against the HTML will differ in text size. That
  is this decision, not a regression.
