import { motion, useScroll, useSpring, useTransform } from "motion/react";
import { useRef } from "react";

import { inViewOnce, sectionItem } from "@/lib/motion";

export interface PipelineStage {
  name: string;
  detail: string;
}

/**
 * The scroll-driven spine of the entry experience.
 *
 * A single line runs down the left of the stage list and draws itself
 * as the reader scrolls through the section — so descending the page
 * *is* descending the pipeline. This is the one place on the landing
 * page where animation is bound to scroll position rather than to a
 * viewport crossing, because it is the one place where the reader's
 * progress and the content's progress are the same thing.
 *
 * Scroll is never hijacked. The page scrolls at exactly its normal
 * rate; only the line's length is derived from how far through the
 * section the reader has travelled. Every stage's text is present and
 * readable regardless of the line's state — if the animation never ran
 * at all, nothing would be lost but the effect.
 *
 * Labels describe a simplified architecture path. Optional work and fallbacks
 * are explained by the surrounding section; this is not live progress.
 */
export function PipelineRail({ stages }: { stages: PipelineStage[] }) {
  const containerRef = useRef<HTMLOListElement>(null);

  /* Measured from the section entering the lower third of the viewport
   * to it leaving the upper third — so the line completes as the last
   * stage is read, rather than racing ahead or lagging behind. */
  const { scrollYProgress } = useScroll({
    target: containerRef,
    offset: ["start 75%", "end 45%"],
  });

  /* A spring on the raw scroll value: without it the line tracks a
   * trackpad's jitter exactly and looks nervous. Low stiffness, high
   * damping — it should trail the scroll slightly, never overshoot. */
  const drawn = useSpring(scrollYProgress, { stiffness: 90, damping: 26, mass: 0.4 });
  const opacity = useTransform(scrollYProgress, [0, 0.02], [0, 1]);

  return (
    <ol ref={containerRef} className="relative ml-1 space-y-10 md:space-y-12">
      {/* The rail. Two layers: a static track at very low contrast so
       * the full path is implied from the start, and the drawn line on
       * top of it. */}
      <span
        aria-hidden="true"
        className="absolute bottom-2 left-[7px] top-2 w-px bg-border"
      />
      <motion.span
        aria-hidden="true"
        style={{ scaleY: drawn, opacity, originY: 0 }}
        className="absolute bottom-2 left-[7px] top-2 w-px bg-gradient-to-b from-ai via-ai to-primary"
      />

      {stages.map((stage, index) => (
        <motion.li
          key={stage.name}
          initial="hidden"
          whileInView="visible"
          viewport={inViewOnce}
          variants={sectionItem}
          className="relative pl-10"
        >
          <span
            aria-hidden="true"
            className="absolute left-0 top-1.5 flex h-[15px] w-[15px] items-center justify-center rounded-full border border-border-strong bg-background"
          >
            <span className="h-1.5 w-1.5 rounded-full bg-ai" />
          </span>

          <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
            <h4 className="font-display text-headline text-foreground">{stage.name}</h4>
            {/* Solid `text-muted-foreground`, not a faded one: at 60%
             * alpha axe measured this at 3.64:1 against the entry
             * page's background, under WCAG AA's 4.5:1. It stays
             * subordinate through size and tracking instead. */}
            <span aria-hidden="true" className="font-mono text-eyebrow text-muted-foreground">
              {String(index + 1).padStart(2, "0")}
            </span>
          </div>
          <p className="mt-2 max-w-xl text-sm leading-relaxed text-muted-foreground md:text-base">
            {stage.detail}
          </p>
        </motion.li>
      ))}
    </ol>
  );
}
