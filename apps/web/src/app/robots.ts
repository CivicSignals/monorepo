// robots.ts — Next.js robots.txt generation (C5, P2, P4).
//
// Allows crawlers to index public pages (/directory/*, /s/* signal pages, /,
// /pricing). Disallows authenticated/workspace routes that require a session
// (the /feed signal feed is workspace-scoped, so it stays disallowed — only the
// public /s/* signal pages are crawlable).
//
// P4 — "polite to crawlers, hostile to scrapers": we emit a `Crawl-delay`
// directive so well-behaved crawlers pace themselves (one request per N seconds),
// which dovetails with the backend per-IP rate limiter on the public read
// endpoints (civicsignals_api.ratelimit). The delay is generous enough that an
// honest crawler honouring it never trips the backend limiter.
//
// Method note: `Crawl-delay` is a non-standard directive (Google ignores it but
// honours Search Console crawl settings; Bing/Yandex/most polite bots honour it).
// Next's typed robots metadata DOES support it via the per-rule `crawlDelay`
// field (Next >= 14.1; this repo is on 15.5) — the metadata route resolver emits
// `Crawl-delay: <n>` under each rule. We set it on the general `*` rule so it
// applies to every non-Googlebot crawler.

import type { MetadataRoute } from "next";

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? "https://civicsignals.io";

// Seconds a crawler should wait between successive requests. 10s ≈ 6 req/min,
// comfortably under the backend public limiter's default (120 req / 60s per IP),
// so a compliant crawler is never throttled while a scrape loop ignoring this is.
const CRAWL_DELAY_SECONDS = 10;

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        // General crawlers: allow public pages only, throttled by Crawl-delay.
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
        crawlDelay: CRAWL_DELAY_SECONDS,
      },
    ],
    sitemap: `${SITE_URL}/sitemap.xml`,
  };
}
