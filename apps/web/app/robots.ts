import type { MetadataRoute } from 'next';

/**
 * Crawling is allowed everywhere: workspace pages carry `noindex` (root
 * layout), and a crawler has to fetch a page to see that.
 */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: { userAgent: '*', allow: '/' },
    sitemap: 'https://ecomsbd.com/sitemap.xml',
  };
}
