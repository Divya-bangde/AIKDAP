import { useEffect, useState } from "react";

/** A theme CSS variable as a colour canvas can paint with. The theme
 * stores raw HSL channels ("243 75% 59%"), which canvas cannot parse;
 * anything already a full colour (hex, `oklch(...)`, `hsl(...)`) is
 * used as is. */
export function toCanvasColor(raw: string, fallback = "#888"): string {
  const value = raw.trim();
  if (!value) return fallback;
  if (value.startsWith("#") || /^[a-z]+\(/i.test(value)) return value;
  return `hsl(${value.replace(/\s*\/\s*/, " / ")})`;
}

export interface GraphColors {
  uploaded: string;
  suggested: string;
  external: string;
  link: string;
  label: string;
}

function readColors(): GraphColors {
  const style = getComputedStyle(document.documentElement);
  const read = (name: string) => toCanvasColor(style.getPropertyValue(name));
  return {
    uploaded: read("--primary"),
    suggested: read("--success"),
    external: read("--muted-foreground"),
    link: read("--border-strong"),
    label: read("--foreground"),
  };
}

/** Graph colours from the live theme, re-read whenever `<html>`'s class
 * changes (the dark-mode toggle flips `dark` there). */
export function useGraphColors(): GraphColors {
  const [colors, setColors] = useState(readColors);
  useEffect(() => {
    const observer = new MutationObserver(() => setColors(readColors()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return colors;
}
