/**
 * Where the extension talks to. `scripts/build.mjs` replaces both values from
 * `ECOMSBD_API_BASE_URL` / `ECOMSBD_WEB_BASE_URL` at build time (https only)
 * and adds the API origin, and nothing else, to the manifest's
 * `host_permissions`.
 */

export const API_BASE_URL = 'https://api.ecomsbd.com';
export const WEB_BASE_URL = 'https://app.ecomsbd.com';
