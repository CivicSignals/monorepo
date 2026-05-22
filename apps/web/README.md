# @civicsignals/web

Next.js 15 (App Router) + React 19 frontend. TypeScript, Tailwind, shadcn/ui,
TanStack Query (server state), Zustand (client state), React Hook Form + Zod.

## Develop

```bash
pnpm install          # from repo root
pnpm --filter @civicsignals/web dev
```

The app talks to `apps/api` over REST at `NEXT_PUBLIC_API_BASE_URL`. The shared
typed client lives in `packages/sdk-ts`.

## Layout

- `src/app/` — App Router routes (RSC by default).
- `src/components/` — UI; `src/components/ui/` is shadcn/ui output.
- `src/lib/` — utilities (`cn`, API helpers).
- `src/store/` — Zustand client-only stores.
