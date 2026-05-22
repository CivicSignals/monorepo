// JsonLdScript — renders a schema.org JSON-LD block (P3 SEO).
//
// The standard Next.js pattern for structured data is a server-rendered
// <script type="application/ld+json"> whose body is JSON.stringify(obj) injected
// via dangerouslySetInnerHTML (the content is our own serialised object, never
// user-controlled HTML, so this is safe). Centralising it here keeps every public
// page consistent and lets tests target a single component.

import type { JsonLd } from "@/lib/jsonld";

export function JsonLdScript({ data }: { data: JsonLd }) {
  return (
    <script
      type="application/ld+json"
      // Safe: `data` is a plain object we build server-side from public-safe
      // fields and serialise ourselves — no untrusted HTML is interpolated.
      dangerouslySetInnerHTML={{ __html: JSON.stringify(data) }}
    />
  );
}
