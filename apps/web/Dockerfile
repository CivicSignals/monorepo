# CivicSignals web image — Next.js 15 standalone output.
# Multi-stage build: deps → build → lean runtime.
# Multi-arch (linux/amd64 + linux/arm64) and cosign-signing are handled in CI
# (.github/workflows/release-images.yml — task O1).
# SPDX-License-Identifier: AGPL-3.0-only

# ---- base: Node 22 LTS + pnpm via corepack ----
FROM node:22-bookworm-slim AS base
ENV PNPM_HOME=/pnpm
ENV PATH=$PNPM_HOME:$PATH
RUN corepack enable

# ---- deps: install workspace dependencies ----
FROM base AS deps
WORKDIR /repo
# Copy only manifests first for better layer caching.
COPY package.json pnpm-lock.yaml* pnpm-workspace.yaml turbo.json ./
COPY apps/web/package.json apps/web/package.json
COPY packages/sdk-ts/package.json packages/sdk-ts/package.json
COPY packages/recipe-schema/package.json packages/recipe-schema/package.json
RUN --mount=type=cache,target=/pnpm/store \
    pnpm install --frozen-lockfile || pnpm install

# ---- build: compile Next.js in standalone output mode ----
FROM deps AS build
WORKDIR /repo
COPY . .
# next.config must have output: "standalone" for the runtime stage below.
RUN pnpm --filter @civicsignals/web build

# ---- runtime: minimal image with only the standalone bundle ----
FROM node:22-bookworm-slim AS runner

# Non-root user matching the Next.js docker convention (uid 1001)
RUN groupadd --gid 1001 nodejs && \
    useradd --uid 1001 --gid nodejs --no-create-home --shell /sbin/nologin nextjs

WORKDIR /app
ENV NODE_ENV=production
# Disable Next.js telemetry in the container image
ENV NEXT_TELEMETRY_DISABLED=1

# Copy standalone bundle, static assets, and public dir from the build stage.
COPY --from=build --chown=nextjs:nodejs /repo/apps/web/.next/standalone ./
COPY --from=build --chown=nextjs:nodejs /repo/apps/web/.next/static ./apps/web/.next/static
COPY --from=build --chown=nextjs:nodejs /repo/apps/web/public ./apps/web/public

USER nextjs

EXPOSE 3000

# Healthcheck: poll Next.js directly before nginx sits in front.
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD node -e "require('http').get('http://localhost:3000/', r => process.exit(r.statusCode < 500 ? 0 : 1)).on('error', () => process.exit(1))"

# The standalone server.js is the entrypoint for Next.js standalone output.
CMD ["node", "apps/web/server.js"]
