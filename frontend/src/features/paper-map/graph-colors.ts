import { readThemeColor, useThemeValue } from "@/lib/theme-colors";

export interface GraphColors {
  uploaded: string;
  suggested: string;
  external: string;
  link: string;
  label: string;
}

function readColors(): GraphColors {
  return {
    uploaded: readThemeColor("--primary"),
    suggested: readThemeColor("--success"),
    external: readThemeColor("--muted-foreground"),
    link: readThemeColor("--border-strong"),
    label: readThemeColor("--foreground"),
  };
}

/** Graph colours from the live theme, re-read on every theme change. */
export function useGraphColors(): GraphColors {
  return useThemeValue(readColors);
}
