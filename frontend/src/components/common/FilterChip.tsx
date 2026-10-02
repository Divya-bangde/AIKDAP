import { cn } from "@/lib/utils";

/** One toggle in a row of list filters ("All", a status, a type). */
export function FilterChip({
  label,
  active,
  onClick,
  className,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
  className?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "whitespace-nowrap rounded-full border px-2.5 py-1 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        active
          ? "border-primary bg-primary text-primary-foreground"
          : "border-border bg-background text-muted-foreground hover:border-border-strong hover:text-foreground",
        className,
      )}
    >
      {label}
    </button>
  );
}
