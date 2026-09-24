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

/** Reads one theme CSS variable from `<html>` as a concrete colour. */
export function readThemeColor(name: string): string {
  return toCanvasColor(getComputedStyle(document.documentElement).getPropertyValue(name));
}

/** A value derived from the live theme, re-read whenever `<html>`'s class
 * changes (the dark-mode toggle flips `dark` there). For renderers that
 * need concrete colours rather than CSS variables (canvas, Plotly,
 * Mermaid). `read` must be a stable, module-level function. */
export function useThemeValue<T>(read: () => T): T {
  const [value, setValue] = useState(read);
  useEffect(() => {
    const observer = new MutationObserver(() => setValue(read()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, [read]);
  return value;
}
