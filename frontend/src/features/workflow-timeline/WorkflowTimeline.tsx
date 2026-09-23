import { ResearchPipeline } from "@/features/research/ResearchPipeline";
import { useRunSteps } from "@/hooks/useRunSteps";
import type { StepOwner } from "@/services/steps";
import type { components } from "@/types/api";

type ResearchStepRead = components["schemas"]["ResearchStepRead"];

/** Steps grouped by generation attempt, oldest first. A research run is
 * always attempt 1; a retried report is 1, then +1 per retry. */
export function stepsByAttempt(steps: ResearchStepRead[]): [number, ResearchStepRead[]][] {
  const groups = new Map<number, ResearchStepRead[]>();
  for (const step of steps) {
    const attempt = step.attempt ?? 1;
    groups.set(attempt, [...(groups.get(attempt) ?? []), step]);
  }
  return [...groups.entries()].sort(([a], [b]) => a - b);
}

/** The live workflow timeline for one run or report.
 *
 * Steps come from `useRunSteps` (initial list + live SSE, polling as a
 * fallback) and render through `ResearchPipeline` -- the one step view,
 * so a run and a report can never show two different timelines. Pass
 * `active` while the owner is not terminal; `seed` when the caller
 * already has the steps from its own detail request. */
export function WorkflowTimeline({
  owner,
  active,
  seed,
}: {
  owner: StepOwner;
  active: boolean;
  seed?: ResearchStepRead[];
}) {
  const { steps } = useRunSteps(owner, { active, seed });
  const groups = stepsByAttempt(steps);

  if (groups.length <= 1) return <ResearchPipeline steps={steps} />;

  return (
    <div className="flex flex-col gap-4">
      {groups.map(([attempt, attemptSteps], index) => (
        <section key={attempt} aria-label={`Attempt ${attempt}`}>
          <p className="mb-2 text-label uppercase text-muted-foreground">
            Attempt {attempt}
            {index === groups.length - 1 ? " (latest)" : ""}
          </p>
          <ResearchPipeline steps={attemptSteps} />
        </section>
      ))}
    </div>
  );
}
