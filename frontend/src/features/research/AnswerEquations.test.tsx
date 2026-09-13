import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AnswerBody } from "@/features/research/AnswerBody";
import { AnswerEquations, type AnswerEquation, type EquationVariable } from "@/features/research/AnswerEquations";
import cardSource from "@/features/research/InteractiveEquationCard.tsx?raw";
import { compileSolution, formatValue } from "@/features/research/InteractiveEquationCard";
import { renderWithProviders } from "@/test/render";

type PlotTrace = {
  x: number[];
  y: (number | null)[];
  name?: string;
  line?: { dash?: string };
};
type PlotLayout = {
  xaxis?: { range?: [number, number] };
  yaxis?: { range?: [number, number] };
  showlegend?: boolean;
};

// Plotly needs a real canvas: stood in for by the points, the marker,
// and the axis ranges/trace names it was given, so these tests exercise
// the card's own decisions (stable axes, reference/compare traces)
// without a real chart library.
vi.mock("@/features/research/PlotlyVisualization", () => ({
  default: ({ data, layout }: { data: PlotTrace[]; layout: PlotLayout }) => (
    <div
      data-testid="plot"
      data-xrange={JSON.stringify(layout.xaxis?.range ?? null)}
      data-yrange={JSON.stringify(layout.yaxis?.range ?? null)}
      data-traces={JSON.stringify(data.map((trace) => ({ name: trace.name, dash: trace.line?.dash })))}
    >
      {data[0].y.length} points; marker {data[1].x[0]},{data[1].y[0]}
    </div>
  ),
}));

function variable(latex: string, fields: Partial<EquationVariable>): EquationVariable {
  return { name: "", unit: "", role: "input", value: 1, min: 0, max: 2, illustrative: true, latex, ...fields };
}

const distance: AnswerEquation = {
  label: "Distance",
  latex: "d = v t",
  expression: "d = v*t",
  output: "d",
  variables: {
    d: variable("d", { name: "distance", unit: "m", value: 20, min: 0, max: 100 }),
    t: variable("t", { name: "time", unit: "s", value: 10, min: 0, max: 60 }),
    v: variable("v", { name: "speed", unit: "m/s", value: 2, min: 0, max: 10, illustrative: false }),
  },
  solutions: {
    d: { expression: "t*v", latex: "t v" },
    t: { expression: "d/v", latex: "\\frac{d}{v}" },
    v: { expression: "d/t", latex: "\\frac{d}{t}" },
  },
};

const schwarzschild: AnswerEquation = {
  label: "Schwarzschild radius",
  latex: "r_s = \\frac{2GM}{c^2}",
  expression: "r_s = 2*G*M/c**2",
  output: "r_s",
  variables: {
    G: variable("G", { name: "gravitational constant", role: "constant", value: 6.674e-11, illustrative: false }),
    M: variable("M", { name: "mass", unit: "kg", value: 2e30, min: 1e29, max: 1e32 }),
    c: variable("c", { name: "speed of light", unit: "m/s", role: "constant", value: 2.998e8, illustrative: false }),
    r_s: variable("r_{s}", { name: "Schwarzschild radius", unit: "m", value: 2954, min: 0, max: 10000 }),
  },
  solutions: {
    M: { expression: "c^2*r_s/(2*G)", latex: "\\frac{c^{2} r_{s}}{2 G}" },
    r_s: { expression: "2*G*M/c^2", latex: "\\frac{2 G M}{c^{2}}" },
  },
};

const potential: AnswerEquation = {
  label: "Effective potential",
  latex: "V(r) = (1 - \\frac{2M}{r})(1 + \\frac{L^2}{r^2})",
  expression: "V = (1 - 2*M/r)*(1 + L**2/r**2)",
  output: "V",
  variables: {
    L: variable("L", { name: "angular momentum", value: 4, min: 0, max: 8 }),
    M: variable("M", { name: "mass", value: 1, min: 0.5, max: 2 }),
    V: variable("V", { name: "effective potential", value: 0.9, min: 0, max: 1.2 }),
    r: variable("r", { name: "radius", value: 16, min: 2, max: 30 }),
  },
  // The backend skips r: it is a root of a cubic.
  solutions: {
    L: { expression: "r*sqrt((-2*M - V*r + r)/(2*M - r))", latex: "r \\sqrt{\\frac{- 2 M - V r + r}{2 M - r}}" },
    M: { expression: "r*(L^2 - V*r^2 + r^2)/(2*(L^2 + r^2))", latex: "\\frac{r \\left(L^{2} - V r^{2} + r^{2}\\right)}{2 \\left(L^{2} + r^{2}\\right)}" },
    V: { expression: "(1 - 2*M/r)*(1 + L^2/r^2)", latex: "\\left(1 - \\frac{2 M}{r}\\right) \\left(1 + \\frac{L^{2}}{r^{2}}\\right)" },
  },
};

const integral: AnswerEquation = {
  label: "Integral mass formula",
  latex: "M = \\int_S (2{T_a}^b - T{\\delta_a}^b) K^a d\\Sigma_b + 2\\Omega_H J_H + \\frac{1}{4\\pi} \\int_{\\partial B} \\kappa \\, dA",
  expression: null,
  output: null,
  variables: { "\\Omega_H": variable("\\Omega_H", { name: "horizon angular velocity" }) },
  solutions: {},
};

const rearrangement = () => screen.getByTestId("rearrangement").querySelector("annotation")?.textContent;

afterEach(() => vi.restoreAllMocks());

describe("AnswerEquations", () => {
  it("shows a LaTeX-only equation with its variables but no sliders or chart", () => {
    const { container } = renderWithProviders(<AnswerEquations equations={[integral]} />);

    expect(screen.getByRole("heading", { name: "Experiment Playground" })).toBeInTheDocument();
    expect(screen.getByText("Integral mass formula")).toBeInTheDocument();
    expect(screen.getByText("horizon angular velocity")).toBeInTheDocument();
    expect(container.querySelector(".katex-display")).not.toBeNull();
    expect(container.querySelector(".katex-error")).toBeNull();
    expect(screen.queryByRole("slider")).not.toBeInTheDocument();
    expect(screen.queryByTestId("plot")).not.toBeInTheDocument();
  });

  it("shows a run stored before variables carried values as a display card", () => {
    renderWithProviders(
      <AnswerEquations equations={[{ latex: "r_s = \\frac{2GM}{c^2}", variables: { r_s: "Schwarzschild radius" } }]} />,
    );

    expect(screen.getByText("Schwarzschild radius")).toBeInTheDocument();
    expect(screen.queryByRole("slider")).not.toBeInTheDocument();
  });

  it("d = v t: solves for d and updates it from the slider and the number input", async () => {
    renderWithProviders(<AnswerEquations equations={[distance]} />);

    const result = await screen.findByLabelText("Result d");
    expect(result).toHaveTextContent("20");
    expect(rearrangement()).toBe("d = t v");

    fireEvent.change(screen.getByLabelText("v value"), { target: { value: "3" } });
    expect(result).toHaveTextContent("30");

    fireEvent.change(screen.getByLabelText("t slider"), { target: { value: "5" } });
    expect(result).toHaveTextContent("15");
    expect(await screen.findByTestId("plot")).toHaveTextContent("120 points; marker 5,15");
  });

  it("d = v t: switching solve-for changes the rearrangement and keeps values consistent", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AnswerEquations equations={[distance]} />);

    const group = await screen.findByRole("group", { name: "Solve for" });
    expect(within(group).getAllByRole("button")).toHaveLength(3);

    await user.click(screen.getByRole("button", { name: "Solve for v" }));
    expect(rearrangement()).toBe("v = \\frac{d}{t}");
    expect(screen.getByRole("button", { name: "Solve for v" })).toHaveAttribute("aria-pressed", "true");
    // d is now an input at the 20 it had: v = 20 / 10.
    expect(screen.getByLabelText("Result v")).toHaveTextContent("2");
    expect(screen.getByLabelText("d slider")).toBeInTheDocument();
    expect(screen.queryByLabelText("v slider")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Solve for t" }));
    expect(rearrangement()).toBe("t = \\frac{d}{v}");
    expect(screen.getByLabelText("Result t")).toHaveTextContent("10");
  });

  it("stretches the slider and curve to a value outside the backend range", async () => {
    renderWithProviders(<AnswerEquations equations={[distance]} />);

    fireEvent.change(await screen.findByLabelText("t value"), { target: { value: "90" } });
    expect(screen.getByLabelText("t slider")).toHaveAttribute("max", "90");
    // t is plotted: the marker sits on the curve's last point, not past it.
    expect(await screen.findByTestId("plot")).toHaveTextContent("marker 90,180");
  });

  it("Schwarzschild: G and c are fixed constants, M is a slider", async () => {
    renderWithProviders(<AnswerEquations equations={[schwarzschild]} />);

    expect(await screen.findByLabelText("Result r_s")).toHaveTextContent(formatValue((2 * 6.674e-11 * 2e30) / 2.998e8 ** 2));
    expect(screen.getByLabelText("M slider")).toBeInTheDocument();
    expect(screen.queryByLabelText("G slider")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("c slider")).not.toBeInTheDocument();
    expect(screen.getAllByText("constant")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "Solve for G" })).not.toBeInTheDocument();
  });

  it("effective potential: evaluates V(r) and plots against the chosen input", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AnswerEquations equations={[potential]} />);

    expect(await screen.findByLabelText("Result V")).toHaveTextContent("0.9297");
    expect(screen.queryByRole("button", { name: "Solve for r" })).not.toBeInTheDocument();
    expect(await screen.findByTestId("plot")).toHaveTextContent("marker 4,0.9296875");

    // Disambiguated by name: "Compare" adds a second combobox once there
    // is another input left to compare against.
    await user.selectOptions(screen.getByRole("combobox", { name: "Plot against" }), "r");
    expect(screen.getByTestId("plot")).toHaveTextContent("marker 16,0.9296875");
  });
});

function plotAttrs(plot: HTMLElement) {
  return {
    xrange: JSON.parse(plot.getAttribute("data-xrange") ?? "null") as [number, number] | null,
    yrange: JSON.parse(plot.getAttribute("data-yrange") ?? "null") as [number, number] | null,
    traces: JSON.parse(plot.getAttribute("data-traces") ?? "[]") as { name?: string; dash?: string }[],
  };
}

describe("stable axes and the Fit button (Problem 1)", () => {
  it("widens the y-axis to keep including the curve, but never shrinks it back down", async () => {
    renderWithProviders(<AnswerEquations equations={[distance]} />);

    // v = 2, t in [0, 60]: the curve's own extent is [0, 120].
    let plot = await screen.findByTestId("plot");
    expect(plotAttrs(plot).yrange).toEqual([0, 120]);

    // Halving v shrinks the live curve to [0, 60], but the reference
    // curve (frozen at v = 2) still reaches 120, so the range is
    // unchanged rather than shrinking to fit the smaller curve.
    fireEvent.change(screen.getByLabelText("v value"), { target: { value: "1" } });
    plot = screen.getByTestId("plot");
    expect(plotAttrs(plot).yrange).toEqual([0, 120]);

    // Now v = 6 pushes the live curve to [0, 360]: the range must grow.
    fireEvent.change(screen.getByLabelText("v value"), { target: { value: "6" } });
    plot = screen.getByTestId("plot");
    expect(plotAttrs(plot).yrange).toEqual([0, 360]);

    // Dropping back to v = 1 must not shrink the range back down.
    fireEvent.change(screen.getByLabelText("v value"), { target: { value: "1" } });
    plot = screen.getByTestId("plot");
    expect(plotAttrs(plot).yrange).toEqual([0, 360]);
  });

  it('"Fit" rescales the y-axis to the current curve', async () => {
    renderWithProviders(<AnswerEquations equations={[distance]} />);

    fireEvent.change(await screen.findByLabelText("v value"), { target: { value: "6" } });
    fireEvent.change(screen.getByLabelText("v value"), { target: { value: "1" } });
    // Still widened from the v = 6 excursion, not yet fit to v = 1.
    expect(plotAttrs(screen.getByTestId("plot")).yrange).toEqual([0, 360]);

    await userEvent.setup().click(screen.getByRole("button", { name: "Fit" }));
    // Tight fit to what is on screen now: the live curve (max 60) and
    // the reference curve frozen at the original v = 2 (max 120).
    expect(plotAttrs(screen.getByTestId("plot")).yrange).toEqual([0, 120]);
  });

  it("draws a faint reference curve at the starting values, unaffected by later changes", async () => {
    renderWithProviders(<AnswerEquations equations={[distance]} />);

    fireEvent.change(await screen.findByLabelText("v value"), { target: { value: "4" } });

    const { traces } = plotAttrs(screen.getByTestId("plot"));
    expect(traces).toHaveLength(3);
    expect(traces[2]).toEqual({ name: "starting values", dash: "dot" });
  });

  it("sets a log-scale axis range in log10 units, not raw values", async () => {
    renderWithProviders(<AnswerEquations equations={[schwarzschild]} />);

    const { xrange } = plotAttrs(await screen.findByTestId("plot"));
    expect(xrange).toEqual([Math.log10(1e29), Math.log10(1e32)]);
  });
});

describe("Compare (Problem 2a)", () => {
  it("overlays 3-5 labelled curves across another input's range, off by default", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AnswerEquations equations={[potential]} />);

    // Off by default: only the live curve, the marker and the reference.
    expect(await screen.findByTestId("plot")).toHaveTextContent("120 points");
    expect(plotAttrs(screen.getByTestId("plot"))).toHaveProperty("traces.length", 3);

    await user.selectOptions(screen.getByRole("combobox", { name: "Plot against" }), "r");
    await user.selectOptions(screen.getByRole("combobox", { name: "Compare against" }), "L");

    const { traces } = plotAttrs(screen.getByTestId("plot"));
    const family = traces.filter((trace) => trace.name?.startsWith("angular momentum ="));
    expect(family.length).toBeGreaterThanOrEqual(3);
    expect(family.length).toBeLessThanOrEqual(5);
    // Each curve in the family is labelled with a distinct value.
    expect(new Set(family.map((trace) => trace.name)).size).toBe(family.length);
  });
});

describe("Systems of equations (Problem 2b)", () => {
  const linearSystem: AnswerEquation = {
    label: "Linear system",
    latex: "x + y = 5 \\\\ x - y = a",
    variables: {
      a: variable("a", { name: "difference", value: 1, min: 0, max: 5 }),
      x: variable("x", { name: "x" }),
      y: variable("y", { name: "y" }),
    },
    systems: [
      { unknowns: ["x", "y"], solutions: { x: { expression: "4 - a", latex: "4 - a" }, y: { expression: "a + 1", latex: "a + 1" } } },
    ],
  };

  it("x + y = 5, x - y = 1: solving for {x, y} gives 3 and 2, and changing an input updates both", async () => {
    renderWithProviders(<AnswerEquations equations={[linearSystem]} />);

    expect(await screen.findByLabelText("Result x")).toHaveTextContent("3");
    expect(screen.getByLabelText("Result y")).toHaveTextContent("2");

    fireEvent.change(screen.getByLabelText("a value"), { target: { value: "3" } });
    expect(screen.getByLabelText("Result x")).toHaveTextContent("1");
    expect(screen.getByLabelText("Result y")).toHaveTextContent("4");
  });

  it("falls back to a display-only card when no unknown set was solvable", () => {
    renderWithProviders(<AnswerEquations equations={[{ ...linearSystem, systems: [] }]} />);

    expect(screen.getByText("Linear system")).toBeInTheDocument();
    expect(screen.queryByLabelText("Result x")).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Solve for" })).not.toBeInTheDocument();
  });
});

describe("Multi-line LaTeX (Problem 2c)", () => {
  it("renders an aligned system with no katex-error", () => {
    const { container } = renderWithProviders(
      <AnswerEquations
        equations={[
          {
            label: "Aligned system",
            latex: "\\begin{aligned} x + y &= 5 \\\\ x - y &= 1 \\end{aligned}",
          },
        ]}
      />,
    );

    expect(container.querySelector(".katex-display")).not.toBeNull();
    expect(container.querySelector(".katex-error")).toBeNull();
  });
});

describe("Independent cards (Problem 2d, known limit)", () => {
  it("two cards sharing a symbol never link -- dragging one never moves the other", async () => {
    const other: AnswerEquation = { ...distance, label: "Other distance" };
    renderWithProviders(<AnswerEquations equations={[distance, other]} />);

    const vInputs = await screen.findAllByLabelText("v value");
    expect(vInputs).toHaveLength(2);

    fireEvent.change(vInputs[0], { target: { value: "9" } });
    expect(vInputs[0]).toHaveValue(9);
    expect(vInputs[1]).toHaveValue(2);
  });
});

describe("Old stored runs (backward compatibility)", () => {
  it("a run stored before systems/expressions existed renders exactly as before", async () => {
    // `distance` has neither `expressions` nor `systems` at all -- not
    // even `null` -- matching a row persisted before this feature.
    renderWithProviders(<AnswerEquations equations={[distance]} />);

    expect(await screen.findByLabelText("Result d")).toHaveTextContent("20");
    expect(screen.getByRole("group", { name: "Solve for" })).toBeInTheDocument();
  });
});

describe("InteractiveEquationCard evaluation", () => {
  it("never uses eval or the Function constructor", async () => {
    const evalSpy = vi.spyOn(globalThis, "eval");
    renderWithProviders(<AnswerEquations equations={[distance]} />);
    fireEvent.change(await screen.findByLabelText("v value"), { target: { value: "4" } });

    expect(screen.getByLabelText("Result d")).toHaveTextContent("40");
    expect(evalSpy).not.toHaveBeenCalled();
    expect(cardSource).not.toMatch(/\beval\s*\(|new\s+Function|Function\s*\(/);
  });

  it.each([
    ["a string", 'import("fs")'],
    ["property access", "t.constructor"],
    ["assignment", "t = 2"],
    ["an unknown function", "evaluate(t)"],
    ["an unknown symbol", "q + t"],
  ])("rejects a solution containing %s", (_, expression) => {
    expect(() => compileSolution(expression, ["t", "v"])).toThrow();
  });

  it("evaluates an allowlisted solution", () => {
    expect(compileSolution("sqrt(t) * pi + v^2", ["t", "v"]).evaluate({ t: 4, v: 3 })).toBeCloseTo(2 * Math.PI + 9);
  });
});

describe("AnswerBody math", () => {
  it("typesets $...$ LaTeX but leaves currency as text", () => {
    const { container } = renderWithProviders(
      <AnswerBody answer={"The radius is $r_s = 2GM/c^2$ and it cost $5 and $10."} citations={[]} onSelect={() => {}} />,
    );

    expect(container.querySelectorAll(".katex")).toHaveLength(1);
    expect(screen.getByText(/cost \$5 and \$10\./)).toBeInTheDocument();
  });

  it("typesets a $$...$$ line as display math", () => {
    const { container } = renderWithProviders(
      <AnswerBody answer={"$$E = mc^2$$"} citations={[]} onSelect={() => {}} />,
    );

    expect(container.querySelector(".katex-display")).not.toBeNull();
  });
});
