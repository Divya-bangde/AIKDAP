import { AnimatePresence, motion } from "motion/react";
import { Check, CircleDashed, Loader2, SkipForward, X } from "lucide-react";

import { TechnicalDetails } from "@/components/common/TechnicalDetails";
import { statusChange } from "@/lib/motion";
import { cn } from "@/lib/utils";
import { presentationFor } from "@/features/research/research-presentation";
import { decisionLine, modelLabel, stepMetrics } from "@/features/research/step-metrics";
import { Badge } from "@/components/ui/badge";
import type { components } from "@/types/api";

type ResearchStepRead = components["schemas"]["ResearchStepRead"];

const STEP_ICON: Record<string, typeof Check> = {
  completed: Check,
  running: Loader2,
  failed: X,
  skipped: SkipForward,
  pending: CircleDashed,
};

const NODE_STYLE: Record<string, string> = {
  completed: "border-success/40 bg-success/10 text-success",
  running: "border-ai/50 bg-ai-soft text-ai",
  failed: "border-destructive/40 bg-destructive/10 text-destructive",
  skipped: "border-border bg-muted text-muted-foreground",
  pending: "border-border bg-muted text-muted-foreground",
};

const LABEL_STYLE: Record<string, string> = {
  completed: "text-success",
  running: "text-ai",
  failed: "text-destructive",
  skipped: "text-muted-foreground",
  pending: "text-muted-foreground",
};

/** The live research pipeline.
 *
 * Renders exactly the `steps[]` the backend returned, in the backend's
 * own order, with the backend's own status for each. Nothing here
 * infers a stage from elapsed time, assumes a stage exists before the
 * backend reports it, or draws a stage as complete because a later one
 * started. A skipped node renders as "Skipped", never as "Completed".
 *
 * Two information layers per step (Sprint 9K.8), not two different
 * pipelines: the default view is `presentationFor(step.node_name)` —
 * static, plain-language copy about what a stage of this kind does —
 * plus the real status and timing. The backend's own title, its real
 * `summary` for *this* run, and every metric `stepMetrics` extracts
 * live inside a collapsed `TechnicalDetails` disclosure, unchanged
 * from what they showed before this sprint — moved, not removed. An
 * error is the one exception: it stays outside the disclosure,
 * unconditionally visible, because a failure isn't something either
 * audience should have to click to find.
 *
 * Motion is used to (a) reveal each stage as it arrives, (b) pulse the
 * node that is genuinely `running`, and (c) run a travelling highlight
 * down the connector *above a running stage only*. Every one of those
 * is a function of real backend state — if the run is finished, none
 * of them animate. */
export function ResearchPipeline({ steps }: { steps: ResearchStepRead[] }) {
  if (steps.length === 0) {
    return (
      <div className="flex items-center gap-3 rounded-lg bg-sunken p-4">
        <Loader2 className="h-4 w-4 animate-spin text-ai" />
        <p className="text-sm text-muted-foreground">Waiting for the pipeline to start…</p>
      </div>
    );
  }

  const ordered = steps.slice().sort((a, b) => a.step_index - b.step_index);

  return (
    <ol className="flex flex-col">
      <AnimatePresence initial={false}>
        {ordered.map((step, index) => {
          const Icon = STEP_ICON[step.status] ?? CircleDashed;
          const metrics = stepMetrics(step);
          const presentation = presentationFor(step.node_name);
          const model = modelLabel(step);
          const decision = decisionLine(step);
          const nextIsRunning = ordered[index + 1]?.status === "running";
          const isLast = index === ordered.length - 1;

          return (
            <motion.li
              key={step.id}
              layout
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
              className="flex gap-4"
            >
              <div className="flex flex-col items-center">
                <div
                  className={cn(
                    "flex h-8 w-8 shrink-0 items-center justify-center rounded-full border",
                    NODE_STYLE[step.status] ?? NODE_STYLE.pending,
                    step.status === "running" && "ai-pulse-ring",
                  )}
                >
                  <Icon
                    className={cn("h-4 w-4", step.status === "running" && "animate-spin")}
                  />
                </div>
                {!isLast && (
                  <div className="relative my-1 w-px flex-1 overflow-hidden bg-border">
                    {nextIsRunning && <span className="ai-flow-line absolute inset-0" />}
                  </div>
                )}
              </div>

              <div className={cn("min-w-0 flex-1", !isLast && "pb-5")}>
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  {/* The plain-language title is primary; the backend's
                   * own step title moves into the technical disclosure
                   * below rather than disappearing. */}
                  <p className="text-sm font-medium leading-tight">{presentation.title}</p>
                  <div className="flex flex-wrap items-center gap-3">
                    {model && (
                      <Badge variant="muted" className="max-w-[12rem] truncate font-mono" title={step.model_name ?? undefined}>
                        {model}
                      </Badge>
                    )}
                    {step.duration_ms !== null && step.duration_ms !== undefined && (
                      <span className="tabular font-mono text-xs text-muted-foreground">
                        {step.duration_ms} ms
                      </span>
                    )}
                    <AnimatePresence mode="wait" initial={false}>
                      <motion.span
                        key={step.status}
                        variants={statusChange}
                        initial="hidden"
                        animate="visible"
                        exit="exit"
                        className={cn(
                          "text-label uppercase",
                          LABEL_STYLE[step.status] ?? LABEL_STYLE.pending,
                        )}
                      >
                        {step.status}
                      </motion.span>
                    </AnimatePresence>
                  </div>
                </div>

                {presentation.description && (
                  <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                    {presentation.description}
                  </p>
                )}

                {decision && <p className="mt-1 text-xs text-foreground">{decision}</p>}

                {step.status === "failed" && step.error_message && (
                  <p role="alert" className="mt-2 text-xs text-destructive">
                    {step.error_message}
                  </p>
                )}

                <TechnicalDetails id={step.id} className="mt-2.5">
                  <div className="flex flex-col gap-2.5 rounded-lg bg-sunken p-3">
                    <p className="font-mono text-xs text-muted-foreground">
                      {step.node_name} · {step.title}
                    </p>

                    {step.summary && (
                      <p className="text-xs leading-relaxed text-foreground">{step.summary}</p>
                    )}

                    {metrics.length > 0 ? (
                      <div className="flex flex-wrap gap-1.5">
                        {metrics.map((metric) => (
                          <span
                            key={metric.label}
                            className="inline-flex items-center gap-1.5 rounded-md border border-border bg-card px-2 py-1"
                          >
                            <span className="text-xs uppercase tracking-wide text-muted-foreground">
                              {metric.label}
                            </span>
                            <span
                              className={cn(
                                "text-xs font-medium text-foreground",
                                metric.mono ? "font-mono" : "tabular",
                              )}
                            >
                              {metric.value}
                            </span>
                          </span>
                        ))}
                      </div>
                    ) : (
                      <p className="text-xs text-muted-foreground">
                        No additional technical detail was recorded for this step.
                      </p>
                    )}
                  </div>
                </TechnicalDetails>
              </div>
            </motion.li>
          );
        })}
      </AnimatePresence>
    </ol>
  );
}
