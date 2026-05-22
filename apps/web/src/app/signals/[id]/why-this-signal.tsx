// WhyThisSignal — the F4 "Why this signal?" panel.
//
// Renders a human-readable explanation of a signal's per-workspace relevance score
// from the score_breakdown JSONB the scorer persists (F3, doc 14 §6.2). The
// breakdown already carries pre-formatted `bullets`; when those are absent (an older
// / partial row) we derive bullets from the structured `matched` flags so the panel
// still says something useful. The per-component point contributions are shown as a
// compact "what drove the score" list below the bullets.
//
// Server state only — this is a pure presentational component fed by the data the
// G2 detail endpoint already returns (no extra fetch; doc 06 §2).

import {
  SCORE_COMPONENT_LABELS,
  type ScoreBreakdown,
  type ScoreComponent,
} from "@/lib/signals-api";

// ---- Helpers ----------------------------------------------------------------

/** Sentence-case a scorer-seeded bullet (they arrive lowercase, doc 14 §6.2). */
function humanizeBullet(s: string): string {
  const trimmed = s.trim();
  if (!trimmed) return trimmed;
  return trimmed.charAt(0).toUpperCase() + trimmed.slice(1);
}

/**
 * Derive fallback bullets from the structured `matched` flags when the breakdown
 * carries no pre-formatted `bullets` (a partial / legacy score row).
 */
function deriveBullets(breakdown: ScoreBreakdown): string[] {
  const matched = breakdown.matched;
  if (!matched) return [];
  const bullets: string[] = [];
  if (matched.signal_type) bullets.push("Signal type is of interest");
  for (const state of matched.states ?? []) bullets.push(`Matched state ${state}`);
  if (matched.entity_kind) bullets.push("Matched entity kind");
  if (matched.size_band) bullets.push("Entity size in target band");
  for (const kw of matched.keywords ?? []) bullets.push(`Matched keyword '${kw}'`);
  return bullets;
}

/** Component rows sorted by point contribution desc, dropping zero/empty ones. */
function rankedComponents(
  components: Record<string, ScoreComponent> | undefined,
): Array<{ key: string; label: string; points: number }> {
  if (!components) return [];
  return Object.entries(components)
    .map(([key, c]) => ({
      key,
      label: SCORE_COMPONENT_LABELS[key] ?? key,
      points: typeof c.points === "number" ? c.points : 0,
    }))
    .filter((c) => c.points > 0)
    .sort((a, b) => b.points - a.points);
}

// ---- Component --------------------------------------------------------------

interface WhyThisSignalProps {
  breakdown: ScoreBreakdown | null;
  score: number | null;
}

export function WhyThisSignal({ breakdown, score }: WhyThisSignalProps) {
  // No per-workspace score row → the signal didn't match this workspace's ICP.
  if (score === null || breakdown === null) {
    return (
      <section aria-labelledby="why-heading" data-testid="why-this-signal">
        <h2 id="why-heading" className="mb-3 text-xl font-semibold">
          Why this signal?
        </h2>
        <p className="text-sm text-muted-foreground" data-testid="why-no-score">
          This signal isn&apos;t scored for your workspace — it didn&apos;t match your
          ICP, so there&apos;s no relevance breakdown to show.
        </p>
      </section>
    );
  }

  const bullets =
    breakdown.bullets && breakdown.bullets.length > 0
      ? breakdown.bullets
      : deriveBullets(breakdown);
  const components = rankedComponents(breakdown.components);
  const hasContent = bullets.length > 0 || components.length > 0;

  return (
    <section aria-labelledby="why-heading" data-testid="why-this-signal">
      <h2 id="why-heading" className="mb-3 text-xl font-semibold">
        Why this signal?
      </h2>

      {!hasContent ? (
        <p className="text-sm text-muted-foreground" data-testid="why-empty">
          Scored {score.toFixed(0)}/100 for your workspace. No detailed breakdown is
          available for this signal.
        </p>
      ) : (
        <div className="space-y-4 rounded-lg border border-gray-200 p-4">
          <p className="text-sm text-gray-700">
            Scored{" "}
            <span className="font-semibold text-gray-900">{score.toFixed(0)}/100</span>{" "}
            for your workspace because:
          </p>

          {bullets.length > 0 && (
            <ul className="list-disc space-y-1 pl-5 text-sm text-gray-700">
              {bullets.map((b, i) => (
                <li key={`${b}-${i}`} data-testid="why-bullet">
                  {humanizeBullet(b)}
                </li>
              ))}
            </ul>
          )}

          {components.length > 0 && (
            <div data-testid="why-components">
              <p className="mb-2 text-xs font-medium text-muted-foreground">
                What drove the score
              </p>
              <ul className="space-y-1.5">
                {components.map((c) => (
                  <li
                    key={c.key}
                    data-testid="why-component"
                    className="flex items-center gap-3 text-sm"
                  >
                    <span className="w-40 shrink-0 text-gray-600">{c.label}</span>
                    <span className="flex-1">
                      <span
                        className="block h-2 rounded-full bg-indigo-500"
                        style={{
                          width: `${Math.min(100, (c.points / 100) * 100).toFixed(1)}%`,
                        }}
                        aria-hidden="true"
                      />
                    </span>
                    <span className="w-12 shrink-0 text-right tabular-nums text-gray-500">
                      +{c.points.toFixed(0)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
