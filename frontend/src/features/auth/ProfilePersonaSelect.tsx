import { useMutation, useQueryClient } from "@tanstack/react-query";

import { messageFor } from "@/lib/api-error";
import { PERSONA_OPTIONS, type Persona } from "@/lib/persona";
import * as authService from "@/services/auth";
import { useAuthStore } from "@/store/auth-store";

/** The user's default persona. Projects without an override follow it,
 * so their cached copies are refetched after a change. */
export function ProfilePersonaSelect({ persona }: { persona: Persona }) {
  const queryClient = useQueryClient();
  const setUser = useAuthStore((state) => state.setUser);

  const mutation = useMutation({
    mutationFn: (next: Persona) => authService.updateMe({ persona: next }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["auth", "me"], updated);
      setUser(updated);
      queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });

  return (
    <label className="mt-1 flex flex-col gap-1 text-xs text-muted-foreground">
      I am a
      <select
        value={persona}
        disabled={mutation.isPending}
        onChange={(event) => mutation.mutate(event.target.value as Persona)}
        className="rounded-md border border-input bg-background px-2 py-1 text-sm text-foreground"
      >
        {PERSONA_OPTIONS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {mutation.isError && (
        <span role="alert" className="text-destructive">
          {messageFor(mutation.error)}
        </span>
      )}
    </label>
  );
}
