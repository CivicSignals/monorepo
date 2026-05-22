# CivicSignals web image (Next.js standalone). Multi-arch + signed in CI (TODO O1).
FROM node:22-bookworm-slim AS base
ENV PNPM_HOME=/pnpm
ENV PATH=$PNPM_HOME:$PATH
RUN corepack enable

# ---- deps ----
FROM base AS deps
WORKDIR /repo
COPY package.json pnpm-lock.yaml* pnpm-workspace.yaml turbo.json ./
COPY apps/web/package.json apps/web/package.json
COPY packages/sdk-ts/package.json packages/sdk-ts/package.json
COPY packages/recipe-schema/package.json packages/recipe-schema/package.json
RUN pnpm install --frozen-lockfile || pnpm install

# ---- build ----
FROM deps AS build
WORKDIR /repo
COPY . .
RUN pnpm --filter @civicsignals/web build

# ---- runtime ----
FROM base AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /repo/apps/web/.next/standalone ./
COPY --from=build /repo/apps/web/.next/static ./apps/web/.next/static
COPY --from=build /repo/apps/web/public ./apps/web/public
EXPOSE 3000
CMD ["node", "apps/web/server.js"]
