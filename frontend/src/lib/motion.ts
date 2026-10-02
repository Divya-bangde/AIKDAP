import type { Transition, Variants } from "motion/react";

/** AIKDAP's motion vocabulary (Sprint 9K.2).
 *
 * Every animation in the product is composed from the variants below
 * rather than defined inline, so timing and easing stay consistent and
 * a change to the product's "feel" happens in one file.
 *
 * The rules this encodes:
 *
 * 1. **Motion communicates state, not decoration.** Entrances
 *    establish hierarchy (what arrived, in what order); transitions
 *    show continuity (this card became this page); status animation
 *    reflects what the backend actually reports.
 * 2. **Transform and opacity only.** Both are compositor-friendly, so
 *    animation never triggers layout and never costs a reflow.
 * 3. **Fast enough to feel responsive.** Nothing decorative runs
 *    longer than `duration.slow`; navigation is never gated on an
 *    animation finishing.
 * 4. **Distance is small.** 8–16px of travel reads as intentional;
 *    large movement reads as a template.
 *
 * Reduced motion is handled globally by `<MotionConfig reducedMotion="user">`
 * in `App.tsx`, which strips transform/layout animation for users who
 * ask for it while leaving opacity — so content still appears, it just
 * doesn't travel. CSS-driven decoration is disabled separately in
 * `index.css`. */

export const duration = {
  fast: 0.15,
  base: 0.25,
  slow: 0.4,
} as const;

/** Decelerating curve for entrances — quick to start, gentle to settle. */
export const ease = [0.22, 1, 0.36, 1] as const;
/** Symmetric curve for state changes and exits. */
export const easeInOut = [0.4, 0, 0.2, 1] as const;

export const transition: Transition = { duration: duration.base, ease };
export const fastTransition: Transition = { duration: duration.fast, ease };

/* ---- Springs (Sprint 16, Phase 8.14) -------------------------------
 *
 * Anything the user can interrupt is a spring, not a fixed duration.
 * A duration-based tween always plays to its end: grab a closing
 * drawer and it finishes closing, then reopens — a visible jump,
 * because the second animation starts from the *target* value rather
 * than from where the element actually is. A spring re-targets from
 * the current presentation value and carries its velocity through, so
 * a reversal is continuous.
 *
 * Expressed as `bounce` + `duration` rather than stiffness/damping/mass:
 * `bounce` is the damping ratio inverted into designer terms (0 =
 * critically damped, no overshoot) and `duration` is the settle time.
 * Three numbers describing a physics model are not readable; two
 * describing the felt result are.
 *
 * Surfaces that move by themselves — drawers, dialogs, layout
 * transitions — stay critically damped (`bounce: 0`): nothing pushed
 * them, so overshoot would read as a glitch. Direct touch is the
 * exception: a card under the pointer or a press being released gets a
 * small bounce, so the response feels physical rather than timed. */

/** The default: critically damped, no overshoot. */
export const spring: Transition = { type: "spring", bounce: 0, duration: 0.35 };

/** Same shape, for small or frequently repeated state changes. */
export const springFast: Transition = { type: "spring", bounce: 0, duration: 0.25 };

/** Direct-touch feedback: hover and press on cards and tiles. */
export const springTouch: Transition = { type: "spring", bounce: 0.35, duration: 0.4 };

/** Shared-layout spring, used for the project card → workspace
 * transition and the active-nav indicator. Spring rather than duration
 * because these follow a real element between two positions and should
 * feel physical, not timed.
 *
 * Previously `stiffness: 400, damping: 38, mass: 0.8`. That computes to
 * a damping ratio of 38 / (2 * sqrt(400 * 0.8)) ≈ 1.06 — correctly
 * critically damped, but nobody can read that off the numbers. Restated
 * as the equivalent `bounce: 0` with the settle time it already had. */
export const layoutSpring: Transition = {
  type: "spring",
  bounce: 0,
  duration: 0.3,
};

export const pageEnter: Variants = {
  hidden: { opacity: 0, y: 8 },
  visible: { opacity: 1, y: 0, transition },
  exit: { opacity: 0, y: -4, transition: fastTransition },
};

/** The route-level transition applied by the application shell to
 * whichever page is currently routed (Sprint 9K.4).
 *
 * Two deliberate choices:
 *
 * 1. **Enter is opacity-only.** The pages themselves already rise 8px
 *    via `pageEnter`. Adding travel here too would compound into ~18px
 *    of movement, past the 16px this system allows, and read as a
 *    slide. The shell supplies the departure the pages never had; the
 *    pages supply the arrival.
 * 2. **Short, and shorter on the way out.** Leaving is not information,
 *    so it gets 160ms. Navigation is never gated on either.
 *
 * Applied inside a `sync`-mode `AnimatePresence`, so the outgoing and
 * incoming pages are briefly mounted together — which is what allows a
 * `layoutId` element (a project card becoming a workspace header) to
 * animate between them. `mode="wait"` would unmount the source before
 * the target existed and silently break every shared-element
 * transition in the product. */
/** Direction-aware: `custom` is a signed depth delta computed in
 * `RoutedPage` from the two routes' path-segment counts (positive =
 * moving to a deeper route, negative = shallower, 0 = a lateral move
 * between siblings at the same depth).
 *
 * The asymmetry is the point (Sprint 9K.4 revisit): Dashboard →
 * Project should read as *entering* something, Project → Research as
 * moving further in, and back navigation should read as retreating —
 * not as the same fade playing in reverse. Kept to 8-10px of travel,
 * within the vocabulary's existing bound, so it registers as directed
 * rather than as a slide. */
export const routeTransition: Variants = {
  hidden: (direction: number) => ({
    opacity: 0,
    x: direction > 0 ? 10 : direction < 0 ? -10 : 0,
    scale: direction === 0 ? 1 : 0.99,
  }),
  visible: { opacity: 1, x: 0, scale: 1, transition: { duration: 0.24, ease } },
  exit: (direction: number) => ({
    opacity: 0,
    x: direction >= 0 ? -8 : 8,
    transition: { duration: 0.16, ease: easeInOut },
  }),
};

export const fadeUp: Variants = {
  hidden: { opacity: 0, y: 12 },
  visible: { opacity: 1, y: 0, transition },
};

export const fadeIn: Variants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition },
};

/** Parent of a list whose children should arrive in sequence. Pair
 * with `staggerItem` on each child. The 40ms step is deliberately
 * small: enough to read as ordered, short enough that a ten-item list
 * still finishes in under half a second. */
export const staggerContainer: Variants = {
  hidden: { opacity: 0 },
  visible: {
    opacity: 1,
    transition: { staggerChildren: 0.04, delayChildren: 0.04 },
  },
};

export const staggerItem: Variants = {
  hidden: { opacity: 0, y: 10 },
  visible: { opacity: 1, y: 0, transition },
  // Sprint 9K.9: only takes effect where a caller wraps the list in its
  // own `AnimatePresence` (e.g. `ResearchHistoryList` filtering a run
  // out of view) — everywhere else `StaggerItem` is used, the list
  // never shrinks, so this clause is simply never reached.
  exit: { opacity: 0, y: -6, transition: fastTransition },
};

/** Side panel (evidence drawer). Enters from the right on desktop.
 *
 * Spring on both directions, not just enter: the drawer is the one
 * surface in the product a user is likely to dismiss while it is still
 * arriving. With a tween, closing mid-open played the open to
 * completion and then reversed; the spring re-targets from wherever
 * the panel currently is. Enter and exit share the same offset, so the
 * path in and the path out are the same line travelled twice. */
export const drawerVariants: Variants = {
  hidden: { opacity: 0, x: 24 },
  visible: { opacity: 1, x: 0, transition: spring },
  exit: { opacity: 0, x: 24, transition: springFast },
};

/** Modal dialog surface.
 *
 * Previously the dialog had no animation of any kind — Radix mounted
 * it and it was simply there, which is the one case worse than a
 * fixed-duration tween: nothing establishes where the surface came
 * from. It now arrives on a spring from 96%, so dismissing it while it
 * is still opening reverses from the current scale instead of
 * finishing the entrance first.
 *
 * `bounce: 0` for the same reason as everywhere else here: a dialog
 * opened by a click carried no momentum into the animation, and
 * overshoot on a surface that just appeared reads as a bug. */
export const dialogVariants: Variants = {
  hidden: { opacity: 0, scale: 0.96 },
  visible: { opacity: 1, scale: 1, transition: spring },
  exit: { opacity: 0, scale: 0.96, transition: springFast },
};

export const overlayVariants: Variants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: fastTransition },
  exit: { opacity: 0, transition: fastTransition },
};

/** Expanding a collapsed region (a pipeline stage's detail). Animates
 * height because that is the property that actually communicates
 * "this opened"; kept to small regions so the layout cost is bounded. */
export const expandVariants: Variants = {
  hidden: { opacity: 0, height: 0 },
  visible: { opacity: 1, height: "auto", transition },
  exit: { opacity: 0, height: 0, transition: fastTransition },
};

/** A status badge changing value: the new value scales in rather than
 * swapping instantly, so a state change is noticed without a flash.
 *
 * A spring because status is the one thing here that changes without
 * the user asking — a backend transition can land while the previous
 * one is still animating (queued → running → complete in quick
 * succession). Each new value re-targets from the current scale
 * instead of restarting from 0.85, so a fast sequence reads as one
 * continuous settle rather than a stutter. */
export const statusChange: Variants = {
  hidden: { opacity: 0, scale: 0.85 },
  visible: { opacity: 1, scale: 1, transition: springFast },
  exit: { opacity: 0, scale: 0.85, transition: { duration: 0.1 } },
};

/** Standard interactive feedback for cards and tiles. Applied via
 * `whileHover`/`whileTap` so it never affects layout. */
export const hoverLift = {
  whileHover: { y: -3, transition: springTouch },
  /* Press reads at 0.97, not 0.995. Half a percent is arithmetic, not
   * feedback — it was below the threshold where anyone could see it,
   * which made every card in the product feel dead on touch. Motion
   * applies `whileTap` on pointer-DOWN, so the response lands with the
   * press rather than waiting for the release to be classified as a
   * click. 100ms out, matching the buttons; the release springs back
   * on `whileHover`'s `springTouch`. */
  whileTap: { y: 0, scale: 0.97, transition: { duration: 0.1, ease: "easeOut" } },
  /* Motion gives any element carrying `whileTap` a `tabIndex` of 0, on
   * the reasonable assumption that a thing with press feedback is a
   * custom button. Everywhere `hoverLift` is used that assumption is
   * wrong: it decorates a *wrapper* whose real control is the link
   * inside it (project cards, recent-project rows), or a metric tile
   * with no control at all (the dashboard's four KPI cards).
   *
   * Left alone that put five focusable-but-inert stops on the Command
   * Center — tabbing landed on each KPI tile, where Enter did nothing,
   * and on each project row twice: once on the wrapper, once on the
   * link it contains (counted in a real browser, Sprint 9K.6).
   *
   * Opting the wrapper out of the tab order removes the dead stops and
   * costs nothing: hover and press feedback are pointer affordances,
   * and the genuinely interactive element inside is still reached. */
  tabIndex: -1,
} as const;

/* ---- Cinematic entry (Sprint 9K.3) ---------------------------------
 *
 * The landing page is the one place allowed a longer, orchestrated
 * sequence — it is a brand moment rather than a working surface. Even
 * so it stays bounded: the whole reveal completes in roughly 1.2s, and
 * the CTA is interactive from the first frame, so nobody ever waits on
 * an animation to use the page (Phase 5). */

/** Parent of the landing composition. Children arrive in a deliberate
 * order: structure, then identity, then meaning, then action. */
export const landingReveal: Variants = {
  hidden: { opacity: 0 },
  visible: {
    opacity: 1,
    transition: { staggerChildren: 0.12, delayChildren: 0.08 },
  },
};

/** One element of the landing sequence. Slightly more travel and a
 * slower settle than the in-app `fadeUp`, which is what makes it read
 * as cinematic rather than as a normal UI transition. */
export const landingItem: Variants = {
  hidden: { opacity: 0, y: 20 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.55, ease },
  },
};

/** The wordmark itself: a fractionally larger scale change so the
 * identity lands with a little more weight than the copy around it. */
export const heroReveal: Variants = {
  hidden: { opacity: 0, y: 16, scale: 0.985 },
  visible: {
    opacity: 1,
    y: 0,
    scale: 1,
    transition: { duration: 0.7, ease },
  },
};

/* ---- Landing → sign-in handoff (Sprint 9K.4, Phase 17) -------------
 *
 * The two pages share a backdrop that does not move, so the handoff is
 * carried entirely by the content: the brand side resolves first, then
 * the sign-in surface arrives. ~420ms end to end, inside the brief's
 * 350–500ms window. */

export const loginBrandReveal: Variants = {
  hidden: { opacity: 0 },
  visible: {
    opacity: 1,
    transition: { duration: 0.35, ease, staggerChildren: 0.07, delayChildren: 0.05 },
  },
};

/** The wordmark settles *inward* — it arrives slightly oversized and
 * contracts to the application's scale, which is the shape of the
 * landing page's hero-sized AIKDAP becoming a workspace header. */
export const loginWordmark: Variants = {
  hidden: { opacity: 0, scale: 1.09, y: -5 },
  visible: { opacity: 1, scale: 1, y: 0, transition: { duration: 0.5, ease } },
};

/** SVG link in the knowledge-network motif, drawn on with `pathLength`.
 * Indexed so the network assembles outward rather than all at once. */
export const knowledgeLink = (index: number): Variants => ({
  hidden: { pathLength: 0, opacity: 0 },
  visible: {
    pathLength: 1,
    opacity: 1,
    transition: { duration: 0.8, ease, delay: 0.35 + index * 0.07 },
  },
});

/** Node in the knowledge-network motif. */
export const knowledgeNode = (index: number): Variants => ({
  hidden: { opacity: 0, scale: 0 },
  visible: {
    opacity: 1,
    scale: 1,
    transition: { duration: 0.4, ease, delay: 0.5 + index * 0.06 },
  },
});

/* ---- Scroll storytelling (Sprint 9K.4) -----------------------------
 *
 * The entry experience became a scrolling document, which needs a
 * different kind of motion from either the hero reveal or the working
 * UI: content that responds to *arriving* rather than to loading.
 *
 * Every one of these is used with `whileInView` and
 * `viewport={{ once: true }}`. Once means once — a section that
 * re-animates every time it scrolls back into view turns a page into a
 * fairground, and makes it impossible to re-read a sentence.
 *
 * The reader is never waiting on any of this: all content is in the
 * DOM and readable from the moment it is scrolled to; these variants
 * only affect how it arrives. Under reduced motion, `MotionConfig`
 * drops the travel and keeps the opacity. */

/** Where a section starts animating: slightly *after* its top edge
 * enters, so the reveal happens in the reader's field of view rather
 * than off the bottom of the screen where it is wasted. */
export const inViewOnce = { once: true, margin: "0px 0px -15% 0px" } as const;

/** A section's contents, arriving in reading order. */
export const sectionReveal: Variants = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.08, delayChildren: 0.05 } },
};

export const sectionItem: Variants = {
  hidden: { opacity: 0, y: 16 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.5, ease } },
};

/** A stage in an explanatory diagram. Slightly slower and further than
 * `staggerItem`, because these are read one at a time rather than
 * scanned as a group. */
export const diagramStage = (index: number): Variants => ({
  hidden: { opacity: 0, y: 14 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.45, ease, delay: index * 0.09 },
  },
});

/** The theme toggle's icon swap. Small and quick — a control, not a
 * flourish; deliberately no full rotation. */
export const themeIcon: Variants = {
  hidden: { opacity: 0, scale: 0.6, rotate: -35 },
  visible: { opacity: 1, scale: 1, rotate: 0, transition: fastTransition },
  exit: { opacity: 0, scale: 0.6, rotate: 35, transition: { duration: 0.12 } },
};
