import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";

// jsdom has no rendering/layout engine. Returning null deliberately exercises
// the decorative canvas' unsupported-WebGL fallback; graphics need a browser.
Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
  configurable: true,
  value: vi.fn(() => null),
});
Object.defineProperty(window, "scrollTo", { configurable: true, value: vi.fn() });
Object.defineProperty(Element.prototype, "scrollIntoView", {
  configurable: true,
  value: vi.fn(),
});

/* jsdom implements no IntersectionObserver, which Motion needs for
 * `whileInView` — the mechanism the entry experience's scroll
 * storytelling is built on (Sprint 9K.4).
 *
 * The stub reports every observed element as intersecting, so tests see
 * the page in its scrolled-through state. That is the right default
 * here: what these tests must protect is that the *content* is present
 * and readable, and content whose only path to being shown is an
 * animation firing would be a genuine defect. If a section ever failed
 * to render because its reveal never ran, a test asserting on its text
 * would catch it. */
class ImmediateIntersectionObserver implements IntersectionObserver {
  readonly root: Element | Document | null = null;
  readonly rootMargin: string = "";
  readonly thresholds: ReadonlyArray<number> = [0];

  constructor(private readonly callback: IntersectionObserverCallback) {}

  observe(target: Element): void {
    this.callback(
      [{ target, isIntersecting: true, intersectionRatio: 1 } as IntersectionObserverEntry],
      this,
    );
  }

  unobserve(): void {}
  disconnect(): void {}
  takeRecords(): IntersectionObserverEntry[] {
    return [];
  }
}

globalThis.IntersectionObserver =
  ImmediateIntersectionObserver as unknown as typeof IntersectionObserver;
