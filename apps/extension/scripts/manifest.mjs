/**
 * The shipped manifest: the template's hosts and permissions, the package
 * version, and the API origin as the only extra host permission.
 */

export const CHAT_HOSTS = ['business.facebook.com', 'www.messenger.com', 'web.whatsapp.com'];

/** An https base URL without a path, query or credentials, or an error. */
export function baseUrl(value, name) {
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new Error(`${name} is not a URL`);
  }
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash) {
    throw new Error(`${name} must be a plain https URL`);
  }
  if (url.pathname !== '/' && url.pathname !== '') throw new Error(`${name} must not have a path`);
  return url.origin;
}

export function buildManifest(template, { version, apiBaseUrl }) {
  if (!/^\d+(\.\d+){0,3}$/.test(version)) throw new Error('version must be 1-4 dot-separated numbers');
  return {
    ...template,
    version,
    host_permissions: [`${baseUrl(apiBaseUrl, 'ECOMSBD_API_BASE_URL')}/*`],
  };
}
