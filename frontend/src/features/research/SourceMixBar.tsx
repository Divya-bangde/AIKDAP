import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import type { components } from "@/types/api";

type SourceMix = components["schemas"]["SourceMix"];

const SEGMENTS = [
  { key: "kb", label: "Your documents", origin: "from your documents", color: "bg-primary" },
  { key: "web", label: "Web", origin: "from the web", color: "bg-muted-foreground" },
  { key: "general", label: "General knowledge", origin: "from general knowledge", color: "bg-warning" },
] as const;

/** Share of an answer's support from project documents, the web and
 * general knowledge. Renders nothing when `mix` is missing (a record
 * saved before the mix existed) or counts nothing. Zero segments are
 * hidden; the legend text carries the meaning, not the colours. */
export function SourceMixBar({ mix, className }: { mix: SourceMix | null | undefined; className?: string }) {
  if (!mix) return null;
  const total = mix.kb + mix.web + mix.general;
  if (total === 0) return null;

  const unit = mix.unit.replace(/s$/, "");
  const segments = SEGMENTS.filter((segment) => mix[segment.key] > 0).map((segment) => {
    const count = mix[segment.key];
    return {
      ...segment,
      count,
      share: (count / total) * 100,
      detail: `${count} of ${total} ${total === 1 ? unit : mix.unit} ${segment.origin}`,
    };
  });
  const summary = `Source mix: ${segments.map((s) => `${Math.round(s.share)}% ${s.label.toLowerCase()}`).join(", ")}`;

  return (
    <TooltipProvider delayDuration={150}>
      <div className={cn("flex flex-col gap-1.5", className)}>
        <div role="img" aria-label={summary} className="flex h-2 w-full overflow-hidden rounded-full bg-secondary">
          {segments.map((s) => (
            <Tooltip key={s.key}>
              <TooltipTrigger asChild>
                {/* Width is data-driven, so it cannot be a static Tailwind class. */}
                <div className={cn("h-full", s.color)} style={{ width: `${s.share}%` }} />
              </TooltipTrigger>
              <TooltipContent>{s.detail}</TooltipContent>
            </Tooltip>
          ))}
        </div>
        <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
          {segments.map((s) => (
            <li key={s.key}>
              <Tooltip>
                <TooltipTrigger className="flex items-center gap-1.5 rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                  <span aria-hidden="true" className={cn("h-2 w-2 rounded-full", s.color)} />
                  {s.label} <span className="tabular font-medium text-foreground">{Math.round(s.share)}%</span>
                </TooltipTrigger>
                <TooltipContent>{s.detail}</TooltipContent>
              </Tooltip>
            </li>
          ))}
        </ul>
      </div>
    </TooltipProvider>
  );
}
