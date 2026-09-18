import nextConfig from 'eslint-config-next';

/**
 * Next's recommended rules, plus one of our own.
 *
 * The `no-restricted-imports` entry is the important line in this file: it
 * stops anything under `app/` or `components/` importing the Supabase client
 * directly. Auth goes through `lib/session`, and business data goes through
 * `lib/api` to FastAPI — a `.from('orders')` in a screen would be a second,
 * unauthorised path to the same data with none of the tenant scoping,
 * permission checks or audit the API applies.
 */
const config = [
  ...nextConfig,
  {
    files: ['app/**/*.{ts,tsx}', 'components/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          paths: [
            {
              name: '@supabase/supabase-js',
              message:
                'Screens must not talk to Supabase directly. Use lib/session for auth and lib/api for data.',
            },
            {
              name: '@supabase/ssr',
              message:
                'Screens must not talk to Supabase directly. Use lib/session for auth and lib/api for data.',
            },
          ],
        },
      ],
    },
  },
];

export default config;
