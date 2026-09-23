import { describe, expect, it } from "vitest";

import { asSpans, highlightBoxes } from "@/features/research/source-highlights";

const spans = asSpans([
  { page: 1, page_width: 600, page_height: 800, rects: [[60, 100, 360, 112]] },
  { page: 2, page_width: 600, page_height: 800, rects: [[0, 0, 300, 10], [0, 12, 150, 22]] },
  { page: 3, page_width: 0, page_height: 800, rects: [[0, 0, 1, 1]] },
  { junk: true },
]);

describe("highlightBoxes", () => {
  it("scales PDF points by rendered width with no y-flip", () => {
    expect(highlightBoxes(spans, 1, 300)).toEqual([{ left: 30, top: 50, width: 150, height: 6 }]);
  });

  it("returns every rect on the requested page only", () => {
    expect(highlightBoxes(spans, 2, 600)).toHaveLength(2);
    expect(highlightBoxes(spans, 4, 600)).toEqual([]);
  });

  it("drops malformed spans", () => {
    expect(spans.map((span) => span.page)).toEqual([1, 2]);
  });
});
