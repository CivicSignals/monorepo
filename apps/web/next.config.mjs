/** @type {import('next').NextConfig} */

// Optional path prefix for when the app is served under a subpath behind a
// shared reverse proxy (e.g. /civic on a domain it co-hosts with another app).
// Unset/empty = served at the domain root (the default). When set, the
// browser-facing API base URL (NEXT_PUBLIC_API_BASE_URL) must carry the same
// prefix. Read at build/start time; restart the web process after changing it.
const basePath = process.env.NEXT_PUBLIC_BASE_PATH || undefined;

const nextConfig = {
  reactStrictMode: true,
  // Standalone output for the production container image (TODO O1).
  output: "standalone",
  // RSC for primary pages; client components for interactivity (doc 06 §2).
  transpilePackages: ["@civicsignals/sdk-ts"],
  // Only set basePath when a prefix is configured — passing undefined keeps the
  // root-mounted default and avoids tripping Next's "must start with /" check.
  ...(basePath ? { basePath } : {}),
  experimental: {
    typedRoutes: true,
  },
};

export default nextConfig;
