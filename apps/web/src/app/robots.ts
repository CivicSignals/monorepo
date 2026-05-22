// robots.ts — Next.js robots.txt generation (C5, P2).
//
// Allows crawlers to index public pages (/directory/*, /s/* signal pages, /,
// /pricing). Disallows authenticated/workspace routes that require a session
// (the /feed signal feed is workspace-scoped, so it stays disallowed — only the
// public /s/* signal pages are crawlable).
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
        allow: ["/", "/pricing", "/directory/", "/s/", "/sitemap.xml"],
        disallow: [
          "/entities/",
          "/feed",
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
