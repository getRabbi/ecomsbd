import { readdirSync } from 'node:fs';
import { join } from 'node:path';

import type { NextConfig } from 'next';

/**
 * Hostnames of the Cloudflare deployment (see `wrangler.jsonc`). The rules
 * below match these hosts only, so the Vercel deployment and the
 * scalemyprints.com pages behave exactly as before.
 *
 * The public site lives on the apex and the workspace on `app.`, because the
 * Supabase session is stored per origin: signing in on the apex would leave
 * the seller signed out on `app.`.
 */
const PUBLIC_HOST = 'ecomsbd.com';
const WWW_HOST = 'www.ecomsbd.com';
const APP_ORIGIN = 'https://app.ecomsbd.com';

/**
 * Every top-level workspace route, read from the `(app)` route group so a new
 * screen is covered without editing this file, plus the auth pages whose
 * callbacks have to land on the workspace origin.
 */
const WORKSPACE_ROUTES = [
  ...readdirSync(join(process.cwd(), 'app', '(app)'), { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name),
  'sign-in',
  'reset-password',
];

/**
 * The web dashboard is a browser client for the existing FastAPI. It has no
 * backend of its own, no database driver and no server-side business logic, so
 * the config here is deliberately small.
 *
 * The headers below are the ones a dashboard that displays money should not be
 * shipped without. They are set here rather than in a proxy so they travel with
 * the app to whatever host it lands on.
 */
const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,

  async redirects() {
    return [
      {
        source: '/:path*',
        has: [{ type: 'host', value: WWW_HOST }],
        destination: `https://${PUBLIC_HOST}/:path*`,
        permanent: true,
      },
      {
        source: `/:route(${WORKSPACE_ROUTES.join('|')})/:rest*`,
        has: [{ type: 'host', value: PUBLIC_HOST }],
        destination: `${APP_ORIGIN}/:route/:rest*`,
        permanent: false,
      },
    ];
  },

  async rewrites() {
    return {
      // The apex root is the public product page. Everywhere else `/` keeps
      // sending visitors to the dashboard or sign-in.
      beforeFiles: [
        {
          source: '/',
          has: [{ type: 'host', value: PUBLIC_HOST }],
          destination: '/ecomsbd',
        },
      ],
      afterFiles: [],
      fallback: [],
    };
  },

  async headers() {
    return [
      {
        source: '/:path*',
        headers: [
          // This app is never framed. Clickjacking a bulk-action table is a
          // real attack: an invisible frame over "Book selected" ships parcels.
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          // No reason for a seller dashboard to reach any of these.
          {
            key: 'Permissions-Policy',
            value: 'camera=(), microphone=(), geolocation=(), payment=()',
          },
        ],
      },
      {
        source: '/ecomsbd/whatsapp-connect',
        headers: [
          { key: 'Cache-Control', value: 'no-store' },
          { key: 'Referrer-Policy', value: 'no-referrer' },
          { key: 'Cross-Origin-Opener-Policy', value: 'same-origin-allow-popups' },
        ],
      },
    ];
  },
};

export default nextConfig;
