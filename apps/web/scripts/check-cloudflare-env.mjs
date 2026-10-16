/**
 * Refuses a Cloudflare build that would ship a secret.
 *
 * The OpenNext adapter copies every variable from this directory's `.env`,
 * `.env.local` and `.env.<mode>[.local]` files into the Worker bundle
 * (`.open-next/cloudflare/next-env.mjs`), whatever its name. Next.js itself
 * only exposes `NEXT_PUBLIC_*`, so a token that is harmless in a local
 * `.env.local` for `next dev` would be uploaded to Cloudflare. Only browser-safe
 * `NEXT_PUBLIC_*` values (and the adapter's own `NEXTJS_ENV`) may be there.
 */
import { readdirSync, readFileSync } from 'node:fs';

const ENV_FILE = /^\.env(\.(local|(production|development|test)(\.local)?))?$/;
const ALLOWED = /^(NEXT_PUBLIC_[A-Z0-9_]+|NEXTJS_ENV)$/;

const offenders = [];
for (const file of readdirSync('.').filter((name) => ENV_FILE.test(name))) {
  for (const line of readFileSync(file, 'utf8').split(/\r?\n/)) {
    const match = /^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=/.exec(line);
    if (match && !ALLOWED.test(match[1])) {
      offenders.push(`${file}: ${match[1]}`);
    }
  }
}

if (offenders.length > 0) {
  console.error(
    'Refusing to build for Cloudflare: these variables would be bundled into the Worker.\n' +
      offenders.map((entry) => `  ${entry}`).join('\n') +
      '\nMove them out of apps/web/.env* (only NEXT_PUBLIC_* belongs there) and build again.',
  );
  process.exit(1);
}
