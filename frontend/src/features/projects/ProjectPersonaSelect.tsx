import { useMutation, useQueryClient } from "@tanstack/react-query";

import { useAuth } from "@/hooks/useAuth";
import { messageFor } from "@/lib/api-error";
import { PERSONA_OPTIONS, personaLabel, type Persona } from "@/lib/persona";
import * as projectsService from "@/services/projects";
import type { components } from "@/types/api";

type ProjectRead = components["schemas"]["ProjectRead"];

/** Per-project persona. The empty value clears the override, so the
 * project follows the user's default again. */
export function ProjectPersonaSelect({ project }: { project: ProjectRead }) {
  const { user } = useAuth();
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: (override: Persona | null) =>
      projectsService.updateProject(project.id, { persona_override: override }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["projects"] }),
  });

  const defaultLabel = user ? personaLabel(user.persona) : "account setting";

  return (
    <div className="flex flex-wrap items-center gap-2">
      <select
        aria-label="Project persona"
        value={project.persona_override ?? ""}
        disabled={mutation.isPending}
        onChange={(event) => mutation.mutate((event.target.value || null) as Persona | null)}
        className="rounded-md border border-input bg-background px-2 py-1 text-xs text-foreground"
      >
        <option value="">Use my default ({defaultLabel})</option>
        {PERSONA_OPTIONS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {mutation.isError && (
        <span role="alert" className="text-xs text-destructive">
          {messageFor(mutation.error)}
        </span>
      )}
    </div>
  );
}
