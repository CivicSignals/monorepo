// JsonLdScript — renders a schema.org JSON-LD block (P3 SEO).
//
// The standard Next.js pattern for structured data is a server-rendered
// <script type="application/ld+json"> whose body is JSON.stringify(obj) injected
// via dangerouslySetInnerHTML. The serialised body is NOT inherently safe: the
// JSON-LD fields (titles, summaries, entity names) come from crawled third-party
// content, and JSON.stringify does NOT escape `<` or `/`. A value containing
// `</script>` would close the script element and let the following bytes parse as
// HTML — XSS. We therefore HTML-escape the JSON before injection (see
// serializeJsonLd in @/lib/jsonld). Centralising it here keeps every public page
// consistent and lets tests target a single component.

import { serializeJsonLd, type JsonLd } from "@/lib/jsonld";

export function JsonLdScript({ data }: { data: JsonLd }) {
  return (
    <script
      type="application/ld+json"
      // `serializeJsonLd` escapes `<`, `>`, and `&` in the serialised JSON so a
      // field containing `</script>` (from crawled third-party content) cannot
      // break out of the script tag.
      dangerouslySetInnerHTML={{ __html: serializeJsonLd(data) }}
    />
  );
}
