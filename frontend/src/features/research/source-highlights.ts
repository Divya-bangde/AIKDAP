/** One page's worth of a chunk's position, as `chunk_positions.spans`
 * stores it: PDF points, top-left origin. */
export interface PositionSpan {
  page: number;
  page_width: number;
  page_height: number;
  rects: [number, number, number, number][];
}

export interface HighlightBox {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** The spans stored as JSONB arrive untyped; keep only well-formed ones
 * so a malformed row draws nothing rather than a box in the wrong place. */
export function asSpans(raw: Record<string, unknown>[]): PositionSpan[] {
  return raw.filter(
    (span): span is PositionSpan & Record<string, unknown> =>
      typeof span.page === "number" &&
      typeof span.page_width === "number" &&
      span.page_width > 0 &&
      Array.isArray(span.rects),
  );
}

/** Pixel boxes for `page` at `renderedWidth`. Both sides use a top-left
 * origin, so this is a uniform scale with no y-flip. */
export function highlightBoxes(
  spans: PositionSpan[],
  page: number,
  renderedWidth: number,
): HighlightBox[] {
  return spans
    .filter((span) => span.page === page)
    .flatMap((span) => {
      const scale = renderedWidth / span.page_width;
      return span.rects.map(([x0, y0, x1, y1]) => ({
        left: x0 * scale,
        top: y0 * scale,
        width: (x1 - x0) * scale,
        height: (y1 - y0) * scale,
      }));
    });
}
