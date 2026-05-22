// robots.ts — Next.js robots.txt generation (C5).
//
// Allows crawlers to index public pages (/directory/*, /, /pricing).
// Disallows authenticated/workspace routes that require a session.
//
// TODO P4: add Crawl-delay when rate-limiting is in place.

import type { MetadataRoute } from "next";

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? "https://civicsignals.io";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        // General crawlers: allow public pages only.
        userAgent: "*",
        allow: ["/", "/pricing", "/directory/", "/sitemap.xml"],
        disallow: [
          "/entities/",
          "/login",
          "/signup",
          "/forgot-password",
          "/reset-password",
          "/verify-email",
          "/api/",
        ],
      },
    ],
    sitemap: `${SITE_URL}/sitemap.xml`,
  };
}
