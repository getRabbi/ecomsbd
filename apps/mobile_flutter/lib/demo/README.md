# `lib/demo/` — UI development fixtures

Everything in this directory is **fake data**. It exists so the Phase A screens
can be built and reviewed before the domain endpoints they will read from
(`/v1/analytics/home`, `/v1/orders`, `/v1/money/summary`) exist.

## Rules

1. **Nothing outside `lib/demo/` may import from it except a screen's
   `demoData:` parameter.** Repositories, controllers and the API client never
   reference this directory.
2. **No production repository ever falls back to these fixtures.** A screen with
   no data shows an `EmptyState` or a `SkeletonLoader`, never invented numbers.
   A dashboard that quietly renders demo figures when the API fails would show a
   seller money they do not have.
3. **Every screen using fixtures renders a visible `Demo data` marker**, so a
   screenshot can never be mistaken for real seller data.
4. **These files are deleted as each screen is wired to its real endpoint.** The
   corresponding row in `docs/IMPLEMENTATION_STATUS.md` moves out of
   "Partially Implemented" at the same time.

The numbers are copied from the UI prototype (Noor Fashion, ৳87,450 COD
outstanding) so the Flutter screens can be compared against the reference
side by side.
