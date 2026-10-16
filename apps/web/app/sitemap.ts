import type { MetadataRoute } from 'next';

import { homeUrl, SITE_URL } from './ecomsbd/content';

/** The public homepage in both languages; the workspace is not for search. */
export default function sitemap(): MetadataRoute.Sitemap {
  const languages = { en: SITE_URL, bn: homeUrl.bn };
  return [
    { url: SITE_URL, alternates: { languages } },
    { url: homeUrl.bn, alternates: { languages } },
  ];
}
