import type { MetadataRoute } from 'next';

import { homeUrl, SITE_URL } from './ecomsbd/content';

/** The public pages in both languages; the workspace is not for search. */
export default function sitemap(): MetadataRoute.Sitemap {
  const home = { en: SITE_URL, bn: homeUrl.bn };
  const privacy = { en: `${SITE_URL}/privacy-policy`, bn: `${SITE_URL}/privacy-policy/bn` };
  const deletion = { en: `${SITE_URL}/account-deletion`, bn: `${SITE_URL}/account-deletion/bn` };
  return [
    { url: home.en, alternates: { languages: home } },
    { url: home.bn, alternates: { languages: home } },
    { url: `${SITE_URL}/ecomsbd-support` },
    { url: privacy.en, alternates: { languages: privacy } },
    { url: privacy.bn, alternates: { languages: privacy } },
    { url: deletion.en, alternates: { languages: deletion } },
    { url: deletion.bn, alternates: { languages: deletion } },
  ];
}
