import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SourceMixBar } from "@/features/research/SourceMixBar";

describe("SourceMixBar", () => {
  it("summarises the non-zero shares and hides empty segments", () => {
    render(<SourceMixBar mix={{ kb: 3, web: 1, general: 0, unit: "citations" }} />);
    expect(screen.getByRole("img")).toHaveAccessibleName("Source mix: 75% your documents, 25% web");
    expect(screen.getByText("Your documents")).toBeInTheDocument();
    expect(screen.queryByText("General knowledge")).not.toBeInTheDocument();
  });

  it("renders nothing without a mix or with nothing counted", () => {
    const { container, rerender } = render(<SourceMixBar mix={null} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<SourceMixBar mix={{ kb: 0, web: 0, general: 0, unit: "citations" }} />);
    expect(container).toBeEmptyDOMElement();
  });
});
