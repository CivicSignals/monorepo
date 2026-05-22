// N6 — Plan tier cards. Prices are illustrative pending N1/N2/LC-13 Stripe setup.
import { cn } from "@/lib/utils";
import { PLANS, type PlanTier } from "./pricing-data";

function CheckIcon({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden="true"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth={2.5}
      viewBox="0 0 24 24"
      xmlns="http://www.w3.org/2000/svg"
    >
      <path
        d="M4.5 12.75l6 6 9-13.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function XIcon({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden="true"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth={2.5}
      viewBox="0 0 24 24"
      xmlns="http://www.w3.org/2000/svg"
    >
      <path
        d="M6 18L18 6M6 6l12 12"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function PlanCard({ plan }: { plan: PlanTier }) {
  return (
    <article
      aria-label={`${plan.name} plan`}
      className={cn(
        "relative flex flex-col rounded-xl border bg-background p-6 shadow-sm transition-shadow hover:shadow-md",
        plan.highlighted && "border-primary ring-2 ring-primary",
      )}
    >
      {plan.highlighted && (
        <div className="absolute -top-3.5 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-full bg-primary px-3 py-1 text-xs font-semibold text-primary-foreground">
          Most popular
        </div>
      )}

      <header>
        <h2 className="text-xl font-bold">{plan.name}</h2>
        <p className="mt-1 text-sm text-muted-foreground">{plan.tagline}</p>
      </header>

      <div className="mt-5">
        {plan.monthlyPrice !== null ? (
          <p className="text-4xl font-extrabold tracking-tight">
            {plan.monthlyPrice}
            <span className="ml-1 text-base font-normal text-muted-foreground">
              /seat/mo
            </span>
          </p>
        ) : (
          <p className="text-3xl font-extrabold tracking-tight">
            Custom quote
          </p>
        )}
        <p className="mt-1 text-xs text-muted-foreground">{plan.annualNote}</p>
      </div>

      <a
        href={plan.ctaHref}
        className={cn(
          "mt-6 block rounded-md px-4 py-2.5 text-center text-sm font-semibold transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2",
          plan.highlighted
            ? "bg-primary text-primary-foreground hover:bg-primary/90 focus-visible:outline-primary"
            : "border border-border bg-background text-foreground hover:bg-muted focus-visible:outline-ring",
        )}
      >
        {plan.ctaLabel}
      </a>

      {!plan.selfServe && (
        <p className="mt-2 text-center text-xs text-muted-foreground">
          Enterprise only — contact required
        </p>
      )}

      <ul aria-label={`${plan.name} features`} className="mt-6 space-y-3">
        {plan.features.map((feature) => (
          <li
            key={feature.text}
            className={cn(
              "flex items-start gap-2.5 text-sm",
              !feature.included && "text-muted-foreground/60",
            )}
          >
            {feature.included ? (
              <CheckIcon className="mt-0.5 h-4 w-4 shrink-0 text-green-600" />
            ) : (
              <XIcon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground/40" />
            )}
            <span>{feature.text}</span>
          </li>
        ))}
      </ul>
    </article>
  );
}

export function PricingTiers() {
  return (
    <section aria-labelledby="pricing-tiers-heading" className="py-20">
      <div className="container">
        <h2
          id="pricing-tiers-heading"
          className="sr-only"
        >
          Plan tiers
        </h2>
        <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
          {PLANS.map((plan) => (
            <PlanCard key={plan.id} plan={plan} />
          ))}
        </div>
        <p className="mt-8 text-center text-xs text-muted-foreground">
          All plans include a 14-day free trial. No credit card required for
          SMB tiers. Prices are illustrative pending launch — see{" "}
          <a
            href="https://github.com/CivicSignals/monorepo"
            className="underline underline-offset-4"
          >
            our changelog
          </a>{" "}
          for updates.
        </p>
      </div>
    </section>
  );
}
