# ADR 0002 — Charts are CustomPainters, not a charting package

Status: Accepted · 2026-09-09 · Phase A

## Context

The locked UI prototype contains six chart types: a smooth profit trend with a
gradient stroke and fading area, a COD composition donut, a delivery funnel, a
settled-vs-due grouped bar chart, and two horizontal ranked bar lists. The build
instruction was to evaluate a chart package before adding one.

## Decision

No charting dependency. Each chart is a `CustomPainter` or a composition of
primitives in `apps/mobile_flutter/lib/design/charts/`.

## Alternatives considered

`fl_chart` and `syncfusion_flutter_charts` were the realistic candidates. Both
are maintained and capable. Both were rejected for the same reason: four of the
six charts here are not really charts. The funnel and the two horizontal bar
lists are label–bar–value rows, roughly forty lines each, and expressing them
through a chart library's axis/series model costs more code than drawing them.

The two that *are* charts — the trend and the donut — need the prototype's exact
gradient stroke, area fade, cap style and inter-slice gap, which means
overriding the library's rendering at every level.

Weighed against that: a chart package adds meaningfully to an APK that targets
low-end Android on metered connections, brings its own transitive dependencies,
and becomes a Flutter-upgrade blocker.

## Consequences

- Zero added dependencies; charts read the design tokens directly, so a palette
  change reaches them like every other component.
- Interactive features a library gives free — tooltips, pan/zoom, series
  toggling — must be written by hand if they are ever wanted. Phase A needs
  none: these charts are read, not explored.
- Each painter carries its own tests.

## Revisit if

A screen needs genuine chart interaction (crosshair inspection, zoomable time
ranges) or more than about ten distinct chart types. At that point the
trade-off inverts and this ADR should be superseded.
