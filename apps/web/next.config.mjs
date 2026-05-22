/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Standalone output for the production container image (TODO O1).
  output: "standalone",
  // RSC for primary pages; client components for interactivity (doc 06 §2).
  transpilePackages: ["@civicsignals/sdk-ts"],
  experimental: {
    typedRoutes: true,
  },
};

export default nextConfig;
