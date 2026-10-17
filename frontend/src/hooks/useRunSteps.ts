import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createStepStreamToken,
  listSteps,
  openStepStream,
  type StepOwner,
} from "@/services/steps";
import type { components } from "@/types/api";
import { sessionEpoch } from "@/lib/session-lifecycle";
import { useAuthStore } from "@/store/auth-store";

type ResearchStepRead = components["schemas"]["ResearchStepRead"];

const FALLBACK_POLL_MS = 2000;

/** Upsert one step by `id`, keeping execution order (attempt, index).
 * A finished copy is never replaced by an older unfinished one — the
 * parent's poll can lag behind the stream. */
export function mergeStep(steps: ResearchStepRead[], incoming: ResearchStepRead): ResearchStepRead[] {
  const next = steps.some((step) => step.id === incoming.id)
    ? steps.map((step) =>
        step.id !== incoming.id || (step.completed_at && !incoming.completed_at)
          ? step
          : { ...step, ...incoming },
      )
    : [...steps, incoming];
  return next.sort(
    (a, b) => (a.attempt ?? 1) - (b.attempt ?? 1) || a.step_index - b.step_index,
  );
}

/** A run's (or report's) workflow steps, live.
 *
 * - The list is loaded with TanStack Query. A caller that already has
 *   the steps (the run detail it polls) passes them as `seed`; they
 *   seed the cache and every newer copy is merged in, so no second
 *   list request is made.
 * - While `active`, an SSE stream (authorized by a ~60s stream token)
 *   merges each event into the cache with `setQueryData`; the server's
 *   `end` event triggers one final refetch (unseeded only).
 * - If the stream cannot open or errors, the hook polls every 2s
 *   instead — unless a `seed` is supplied, whose owner is already
 *   polling.
 * - Nothing streams or polls once `active` is false (terminal run). */
export function useRunSteps(
  owner: StepOwner,
  { active, seed }: { active: boolean; seed?: ResearchStepRead[] },
) {
  const queryClient = useQueryClient();
  const sessionId = useAuthStore((state) => state.sessionId);
  const queryKey = ["steps", owner.kind, owner.id, sessionId];
  const [streamFailed, setStreamFailed] = useState(false);
  const seeded = seed !== undefined;

  const query = useQuery({
    queryKey,
    queryFn: () => listSteps(owner),
    initialData: seed,
    staleTime: seed ? Infinity : 0,
    refetchInterval: active && streamFailed && !seed ? FALLBACK_POLL_MS : false,
  });

  useEffect(() => {
    if (!seed) return;
    queryClient.setQueryData<ResearchStepRead[]>(queryKey, (old) =>
      seed.reduce(mergeStep, old ?? []),
    );
    // `queryKey` is derived from `owner`; listing it would loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seed, owner.kind, owner.id, queryClient, sessionId]);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    const epoch = sessionEpoch();
    const current = () => !cancelled && epoch === sessionEpoch();
    let source: EventSource | null = null;
    setStreamFailed(false);

    createStepStreamToken(owner)
      .then(({ token }) => {
        if (!current()) return;
        source = openStepStream(owner, token);
        if (!source) {
          setStreamFailed(true);
          return;
        }
        source.addEventListener("step", (event) => {
          if (!current()) return;
          const step = JSON.parse((event as MessageEvent<string>).data) as ResearchStepRead;
          queryClient.setQueryData<ResearchStepRead[]>(queryKey, (old) => mergeStep(old ?? [], step));
        });
        source.addEventListener("end", () => {
          if (!current()) return;
          source?.close();
          // A seeded owner refreshes its own detail on completion.
          if (!seeded) void queryClient.invalidateQueries({ queryKey });
        });
        source.onerror = () => {
          if (!current()) return;
          source?.close();
          setStreamFailed(true);
        };
      })
      .catch(() => {
        if (current()) setStreamFailed(true);
      });

    return () => {
      cancelled = true;
      source?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, owner.kind, owner.id, queryClient, sessionId]);

  return { steps: query.data ?? [], isLoading: query.isLoading, streaming: active && !streamFailed };
}
