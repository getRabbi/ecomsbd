# Cloudflare Workers deployment

The web dashboard runs as one Worker, `ecomsbd-web`, built with the OpenNext
adapter (`@opennextjs/cloudflare`). Vercel (`ecomsbd-web.vercel.app`, and the
scalemyprints.com pages) keeps building the same app from `main` as the
fallback.

| Host | Serves |
| --- | --- |
| `ecomsbd.com` | Public site: `/` shows the product page; support, privacy and account deletion stay here. Workspace and auth paths redirect (307) to `app.` |
| `app.ecomsbd.com` | Seller workspace, unchanged behaviour |
| `www.ecomsbd.com` | 308 to `ecomsbd.com` |

The host rules live in `next.config.ts` and match only these hostnames. The API
stays `https://api.scalemyprints.com/v1`.

## Deploy

From `apps/web`:

1. Log in once: `npx wrangler login`.
2. Provide the three public build values in `.env.production.local` (git-ignored)
   or in the shell: `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`,
   `NEXT_PUBLIC_API_BASE_URL=https://api.scalemyprints.com/v1`. They are the
   same values as the Vercel project's.
3. `npm run cf:deploy`. The first deploy creates the three custom domains, their
   DNS records and certificates.

`cf:build` refuses to run while any `.env*` file here holds a variable other than
`NEXT_PUBLIC_*`, because the adapter copies every env-file variable into the
Worker. Move such a variable out (for example the `VERCEL_OIDC_TOKEN` that
`vercel link` writes to `.env.local`), or build from a clean checkout.

`npm run cf:preview` serves the build locally. While `wrangler.jsonc` lists the
custom domains, the preview presents every request as `ecomsbd.com`, so host
rules can only be checked against a preview without `routes`.

## Outside this repository

- Supabase → Authentication → URL Configuration → Redirect URLs:
  `https://ecomsbd.com/**` and `https://app.ecomsbd.com/**`, next to the existing
  `https://ecomsbd-web.vercel.app/**`. Site URL stays `https://scalemyprints.com`.
- Northflank secret group `ecomsbd-production`: `CORS_ALLOW_ORIGINS` includes
  `https://ecomsbd.com,https://app.ecomsbd.com`; restart `ecomsbd-api` after
  editing it, since group edits do not restart running containers.

## Free plan

No R2, KV, D1, Queues or Durable Objects. `/_next/static/*`, `/brand/*` and other
files in `.open-next/assets` are served without running the Worker. Prerendered
pages come from the read-only static-assets cache with cache interception, so
they skip loading the Next.js server. The bundle is about 1.5 MiB gzipped, under
the 3 MiB free-plan limit.

Rollback: `npx wrangler rollback`, or send users back to the Vercel URL.
