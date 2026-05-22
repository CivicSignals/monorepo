// Step indicator for the ICP onboarding wizard (F2).
// Shows the numbered steps with current/completed/upcoming state.

import type { WizardStep } from "@/store/icp-wizard";
import { WIZARD_STEPS, WIZARD_STEP_LABELS } from "@/store/icp-wizard";

interface WizardStepIndicatorProps {
  currentStep: WizardStep;
}

export function WizardStepIndicator({ currentStep }: WizardStepIndicatorProps) {
  const currentIdx = WIZARD_STEPS.indexOf(currentStep);

  return (
    <nav aria-label="Wizard progress" className="mb-8">
      <ol className="flex flex-wrap items-center gap-1 text-sm">
        {WIZARD_STEPS.map((step, idx) => {
          const isActive = step === currentStep;
          const isComplete = idx < currentIdx;
          const isLast = idx === WIZARD_STEPS.length - 1;

          return (
            <li key={step} className="flex items-center gap-1">
              <span
                aria-current={isActive ? "step" : undefined}
                className={[
                  "flex h-6 w-6 items-center justify-center rounded-full text-xs font-semibold shrink-0",
                  isActive
                    ? "bg-primary text-primary-foreground"
                    : isComplete
                      ? "bg-primary/20 text-primary"
                      : "bg-muted text-muted-foreground",
                ].join(" ")}
              >
                {isComplete ? (
                  <span aria-label="complete">✓</span>
                ) : (
                  idx + 1
                )}
              </span>
              <span
                className={[
                  "hidden sm:inline",
                  isActive ? "font-semibold text-foreground" : "text-muted-foreground",
                ].join(" ")}
              >
                {WIZARD_STEP_LABELS[step]}
              </span>
              {!isLast && (
                <span aria-hidden className="text-muted-foreground mx-1">
                  /
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
