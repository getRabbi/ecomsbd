import { defineCloudflareConfig } from '@opennextjs/cloudflare';
import staticAssetsIncrementalCache from '@opennextjs/cloudflare/overrides/incremental-cache/static-assets-incremental-cache';

/**
 * Every page in this app is either prerendered at build time or rendered per
 * request; nothing revalidates. The read-only static-assets cache therefore
 * covers it without R2 or KV, and cache interception answers prerendered pages
 * before the Next.js server is loaded, which keeps the Worker well inside the
 * free plan's CPU budget.
 */
export default defineCloudflareConfig({
  incrementalCache: staticAssetsIncrementalCache,
  enableCacheInterception: true,
});
