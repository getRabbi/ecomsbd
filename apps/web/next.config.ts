import type { NextConfig } from 'next';

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
