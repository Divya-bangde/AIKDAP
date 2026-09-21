import { Suspense, lazy } from "react";

import { EntryBackdrop } from "@/features/landing/EntryBackdrop";
import { Hero } from "@/features/landing/Hero";
import { LandingNav } from "@/features/landing/LandingNav";
import { useForcedTheme } from "@/hooks/useTheme";

/* Sections 02–09 are code-split away from the hero (Phase 21).
 *
 * The fetch begins the moment this page mounts — `lazy` starts loading
 * as soon as the element is rendered, not when it scrolls into view —
 * so in practice the chunk has arrived long before anyone reaches the
 * second section. What the split buys is the *first* paint: the entry
 * payload contains the screen a visitor is actually waiting for, and
 * not the eight below it. The hero is never lazy. */
const LandingStory = lazy(() => import("@/features/landing/LandingStory"));

/**
 * AIKDAP's public entry experience.
 *
 * **Always dark** (Sprint 9K.3, Phase 16), regardless of the user's
 * saved application theme: `useForcedTheme` pins the document while
 * this page is mounted and restores their real preference on unmount,
 * without ever writing to it. A user who prefers light sees a dark
 * landing and a light workspace — that contrast is the intent.
 *
 * The page is a scrolling document of nine sections that walks through
 * the platform's real pipeline in the order the pipeline runs, so
 * descending the page and descending the architecture are the same
 * movement (Sprint 9K.4).
 *
 * Everything on it is explanatory. The components, models and stages
 * named here exist and are named as the codebase names them, but no
 * number, status or measurement on this page is read from a running
 * system — and the two figures that could be mistaken for measurements
 * say so in their own captions.
 */
export function Landing() {
  useForcedTheme("dark");

  return (
    <div className="relative min-h-screen overflow-x-hidden bg-background">
      <EntryBackdrop animated />

      <LandingNav />

      <main className="relative z-10">
        <Hero />

        {/* The placeholder reserves height so the scrollbar does not
         * jump when the chunk resolves. It carries no text: an empty
         * region is honest about a region that is still empty, and a
         * spinner here would imply the page was broken. */}
        <Suspense fallback={<div aria-hidden="true" className="min-h-screen" />}>
          <LandingStory />
        </Suspense>
      </main>
    </div>
  );
}
