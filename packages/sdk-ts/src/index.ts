// CivicSignals TypeScript SDK.
//
// The REST conventions are fixed by doc 06 §5: versioned `/api/v1`, bearer auth,
// cursor pagination (`?cursor=...&limit=25`), RFC 7807 problem+json errors.
// Endpoint types are generated from the live OpenAPI doc — run `pnpm generate`
// to populate `src/generated/schema.ts` (TODO Q3 wires this into CI).

export interface ClientOptions {
  /** e.g. http://localhost:8000/api/v1 */
  baseUrl: string;
  /** Bearer token (JWT or cs_live_… API key). */
  token?: string;
  /** X-Workspace-Id header value (doc 06 §6, TODO B5). */
  workspaceId?: string;
  fetch?: typeof fetch;
}

export interface Page<T> {
  items: T[];
  nextCursor: string | null;
}

export class CivicSignalsClient {
  private readonly opts: ClientOptions;

  constructor(opts: ClientOptions) {
    this.opts = opts;
  }

  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const doFetch = this.opts.fetch ?? fetch;
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    if (this.opts.token) headers.set("Authorization", `Bearer ${this.opts.token}`);
    if (this.opts.workspaceId) headers.set("X-Workspace-Id", this.opts.workspaceId);

    const res = await doFetch(`${this.opts.baseUrl}${path}`, { ...init, headers });
    if (!res.ok) {
      // RFC 7807 problem+json (doc 06 §5).
      const problem = await res.json().catch(() => ({ title: res.statusText }));
      throw new CivicSignalsError(res.status, problem);
    }
    return (await res.json()) as T;
  }
}

export class CivicSignalsError extends Error {
  constructor(
    public readonly status: number,
    public readonly problem: unknown,
  ) {
    super(`CivicSignals API error ${status}`);
    this.name = "CivicSignalsError";
  }
}
