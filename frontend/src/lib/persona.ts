import type { components } from "@/types/api";

export type Persona = components["schemas"]["Persona"];

/** The one list of personas, used by signup, the profile control, and
 * the project control so labels never drift between them. */
export const PERSONA_OPTIONS: { value: Persona; label: string; description: string }[] = [
  {
    value: "student",
    label: "Student",
    description: "Study summaries and project synopses from your documents.",
  },
  {
    value: "researcher",
    label: "Researcher",
    description: "Evidence-backed research across your documents and the web.",
  },
  {
    value: "builder",
    label: "Project builder",
    description: "Tool and process recommendations for building from papers.",
  },
];

export function personaLabel(persona: Persona): string {
  return PERSONA_OPTIONS.find((option) => option.value === persona)?.label ?? persona;
}
