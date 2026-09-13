import {
  isConstantNode,
  isFunctionNode,
  isOperatorNode,
  isParenthesisNode,
  isSymbolNode,
  parse,
  type EvalFunction,
} from "mathjs";
import { lazy, Suspense, useMemo, useRef, useState } from "react";

import type { InteractiveEquation, SystemInteractiveEquation } from "@/features/research/AnswerEquations";

type Range = { min: number; max: number };
import { MathTex } from "@/features/research/MathTex";

const PlotlyVisualization = lazy(() => import("@/features/research/PlotlyVisualization"));

/** Same set the backend lets a solution call (`planner.equations._ALLOWED_FUNCTIONS`). */
const FUNCTIONS = new Set(["sqrt", "exp", "log", "sin", "cos", "tan", "asin", "acos", "atan", "sinh", "cosh", "tanh"]);
const OPERATORS = new Set(["add", "subtract", "multiply", "divide", "pow", "unaryMinus", "unaryPlus"]);
const CONSTANTS = new Set(["pi", "E"]);
const CURVE_SAMPLES = 120;
const SLIDER_STEPS = 1000;
/** A positive range spanning this many times its minimum is shown on a
 * log scale: a linear 1e29..1e32 slider pins every real value at one end. */
const LOG_SPAN = 100;

/** Parses a backend-computed solution and compiles it only if every node
 * is plain arithmetic over the equation's own symbols: numbers, + - * / ^,
 * parentheses and the allowlisted functions. Anything else (strings,
 * property access, assignment, `import`, unknown functions) throws before
 * compiling. Never uses eval or the Function constructor. */
export function compileSolution(expression: string, symbols: string[]): EvalFunction {
  const allowed = new Set(symbols);
  const root = parse(expression);
  root.traverse((node, path, parent) => {
    const ok =
      (isConstantNode(node) && typeof node.value === "number") ||
      isParenthesisNode(node) ||
      (isOperatorNode(node) && OPERATORS.has(node.fn)) ||
      (isFunctionNode(node) && isSymbolNode(node.fn) && FUNCTIONS.has(node.fn.name)) ||
      (isSymbolNode(node) &&
        (allowed.has(node.name) ||
          CONSTANTS.has(node.name) ||
          (path === "fn" && parent !== null && isFunctionNode(parent) && FUNCTIONS.has(node.name))));
    if (!ok) throw new Error(`Unsupported term "${node.toString()}" in ${expression}`);
  });
  return root.compile();
}

/** A real, finite result, or NaN (a pole, a complex root, a domain error). */
function evaluateAt(fn: EvalFunction, scope: Record<string, number>): number {
  try {
    const result = fn.evaluate({ ...scope });
    return typeof result === "number" && Number.isFinite(result) ? result : NaN;
  } catch {
    return NaN;
  }
}

export function formatValue(x: number): string {
  if (!Number.isFinite(x)) return "—";
  const magnitude = Math.abs(x);
  return magnitude !== 0 && (magnitude >= 1e5 || magnitude < 1e-3) ? x.toExponential(3) : String(Number(x.toPrecision(4)));
}

/** `formatValue` as TeX: `2.000e+30` -> `2.000 \times 10^{30}`. */
export function valueTex(x: number): string {
  return formatValue(x).replace(/e([+-])(\d+)/, (_, sign: string, exponent: string) =>
    ` \\times 10^{${sign === "-" ? "-" : ""}${exponent}}`,
  );
}

/** `m^3 kg^-1 s^-2` -> upright TeX with braced exponents and thin spaces. */
export function unitTex(unit: string): string {
  const tex = unit
    .trim()
    .replace(/([%#&_$])/g, "\\$1")
    .replace(/\^\(?(-?[\d.]+)\)?/g, "^{$1}")
    .replace(/\s+/g, "\\,");
  return `\\mathrm{${tex}}`;
}

const isLog = ({ min, max }: Range) => min > 0 && max / min >= LOG_SPAN;

/** How many curves a "Compare" family overlays. */
const COMPARE_CURVES = 5;

/** A Plotly axis `range`, in log10 units when `log` (Plotly's own
 * convention for a log-type axis), or plain values otherwise. */
function axisRange({ min, max }: Range, log: boolean): [number, number] {
  return log ? [Math.log10(min), Math.log10(max)] : [min, max];
}

/** The y-axis range for a card's plot: fixed once a (target, plot-against)
 * pair is chosen, then only ever widened to keep including the curve --
 * never auto-shrunk as sliders move. Without this, Plotly's own
 * autoscale refits both axes on every change, so a line that only got
 * steeper (or shallower) lands in the same spot on screen and only its
 * tick labels move. `key` changing (a different pair, or a "Fit" click
 * bumping a version counter into it) starts a fresh, tight baseline. */
function useWidenOnlyRange(key: string | null, candidate: Range | null): Range | null {
  const baseline = useRef<{ key: string; range: Range } | null>(null);
  if (!key || !candidate) {
    if (!key) baseline.current = null;
    return null;
  }
  if (!baseline.current || baseline.current.key !== key) {
    baseline.current = { key, range: candidate };
  } else {
    const current = baseline.current.range;
    baseline.current = {
      key,
      range: { min: Math.min(current.min, candidate.min), max: Math.max(current.max, candidate.max) },
    };
  }
  return baseline.current.range;
}

/** The theme's primary color, concrete, for Plotly (which cannot read CSS variables). */
function accentColor(): string {
  const probe = document.createElement("span");
  probe.className = "text-primary";
  document.body.append(probe);
  const color = getComputedStyle(probe).color;
  probe.remove();
  return color;
}

function Unit({ unit }: { unit: string }) {
  return unit ? (
    <span className="text-xs text-muted-foreground">
      <MathTex tex={unitTex(unit)} />
    </span>
  ) : null;
}

/** Slider for one input; log-scaled when its range spans orders of magnitude. */
function ValueSlider({
  symbol,
  range,
  value,
  onChange,
}: {
  symbol: string;
  range: Range;
  value: number;
  onChange: (value: number) => void;
}) {
  const log = isLog(range);
  const [lo, hi] = log ? [Math.log10(range.min), Math.log10(range.max)] : [range.min, range.max];
  const position = log ? Math.log10(Math.max(value, range.min)) : value;
  return (
    <input
      type="range"
      aria-label={`${symbol} slider`}
      min={lo}
      max={hi}
      step={(hi - lo) / SLIDER_STEPS}
      value={position}
      onChange={(event) => {
        const raw = log ? 10 ** event.target.valueAsNumber : event.target.valueAsNumber;
        // Slider steps are binary fractions: round away the float noise.
        onChange(Number(raw.toPrecision(4)));
      }}
      className="min-w-0 flex-1 accent-primary"
    />
  );
}

/** A physics-calculator card for one equation: pick what to solve for,
 * drive the other inputs by slider or number, and watch the result and
 * its curve against one chosen input. Every rearrangement, unit and
 * range comes from the backend; this only evaluates them. Default export
 * so `AnswerEquations` loads it (and mathjs) lazily.
 *
 * Known limit: two cards that happen to share a symbol (e.g. the same
 * `M` in two different equations from the same answer) never link --
 * each card keeps its own independent `values` state, so dragging one
 * card's slider never moves another card's. */
export default function InteractiveEquationCard({ equation }: { equation: InteractiveEquation }) {
  const { variables, solutions } = equation;
  const symbols = Object.keys(variables);

  const compiled = useMemo(() => {
    const out: Record<string, EvalFunction> = {};
    for (const [symbol, solution] of Object.entries(solutions)) {
      try {
        out[symbol] = compileSolution(solution.expression, symbols);
      } catch {
        // Not offered as a "solve for" choice: it is never evaluated.
      }
    }
    return out;
    // `symbols` is derived from `variables`, which is part of `equation`.
  }, [equation]);
  const targets = Object.keys(compiled);
  const accent = useMemo(accentColor, []);

  const [target, setTarget] = useState(() =>
    equation.output && compiled[equation.output] ? equation.output : targets[0],
  );
  const [values, setValues] = useState<Record<string, number>>(() =>
    Object.fromEntries(symbols.map((symbol) => [symbol, variables[symbol].value])),
  );
  const [xChoice, setXChoice] = useState<string | null>(null);
  const [compareWith, setCompareWith] = useState<string | null>(null);
  const [fitVersion, setFitVersion] = useState(0);

  const fn = target ? compiled[target] : undefined;
  const result = fn ? evaluateAt(fn, values) : NaN;
  const inputs = symbols.filter((symbol) => symbol !== target && variables[symbol].role === "input");
  const x = xChoice && inputs.includes(xChoice) ? xChoice : inputs[0];
  // The backend range, stretched to the current value: a value carried
  // over from a previous result (or typed in) can lie outside it, and the
  // slider and curve must still reach it.
  const rangeOf = (symbol: string): Range => ({
    min: Math.min(variables[symbol].min, values[symbol]),
    max: Math.max(variables[symbol].max, values[symbol]),
  });
  const xLog = x ? isLog(rangeOf(x)) : false;

  const curve = useMemo(() => {
    if (!fn || !x) return null;
    const { min, max } = rangeOf(x);
    const at = (i: number) => i / (CURVE_SAMPLES - 1);
    const xs = Array.from({ length: CURVE_SAMPLES }, (_, i) =>
      xLog ? 10 ** (Math.log10(min) + (Math.log10(max) - Math.log10(min)) * at(i)) : min + (max - min) * at(i),
    );
    const ys = xs.map((xv) => {
      const y = evaluateAt(fn, { ...values, [x]: xv });
      return Number.isNaN(y) ? null : y;
    });
    return { xs, ys };
  }, [fn, x, xLog, values, variables]);

  // A faint dashed trace frozen at the values current when this
  // (target, plot-against) pair was first shown, drawn behind the live
  // curve: a later slider change is then visible as the live curve
  // pulling away from its own starting shape, not just moving.
  const referenceKey = target && x ? `${target}|${x}` : null;
  const referenceRef = useRef<{ key: string; values: Record<string, number> } | null>(null);
  if (referenceKey && (!referenceRef.current || referenceRef.current.key !== referenceKey)) {
    referenceRef.current = { key: referenceKey, values: { ...values } };
  }
  const referenceValues = referenceKey ? (referenceRef.current?.values ?? null) : null;
  const referenceCurve = useMemo(() => {
    if (!fn || !x || !curve || !referenceValues) return null;
    const ys = curve.xs.map((xv) => {
      const y = evaluateAt(fn, { ...referenceValues, [x]: xv });
      return Number.isNaN(y) ? null : y;
    });
    return { xs: curve.xs, ys };
  }, [fn, x, curve, referenceValues]);

  // "Compare": 3-5 curves at values spread across one other input's own
  // range (log-spaced when it is), each a snapshot of the same
  // compiled solution -- off by default.
  const compareCandidates = inputs.filter((symbol) => symbol !== x);
  const activeCompare = compareWith && compareCandidates.includes(compareWith) ? compareWith : null;
  const compareCurves = useMemo(() => {
    if (!activeCompare || !fn || !x || !curve) return null;
    const range = rangeOf(activeCompare);
    const log = isLog(range);
    const at = (i: number) => i / (COMPARE_CURVES - 1);
    return Array.from({ length: COMPARE_CURVES }, (_, i) => {
      const value = log
        ? 10 ** (Math.log10(range.min) + (Math.log10(range.max) - Math.log10(range.min)) * at(i))
        : range.min + (range.max - range.min) * at(i);
      const ys = curve.xs.map((xv) => {
        const y = evaluateAt(fn, { ...values, [activeCompare]: value, [x]: xv });
        return Number.isNaN(y) ? null : y;
      });
      return { value, xs: curve.xs, ys };
    });
    // `values` supplies every symbol compare does not itself vary.
  }, [activeCompare, fn, x, curve, values]);

  // The y-axis range: fixed once (target, x) is chosen, only ever
  // widened to keep including the live curve, the reference curve and
  // the compare family, and reset to a tight fit by "Fit".
  const yKey = target && x ? `${target}|${x}|${fitVersion}` : null;
  const yCandidate = useMemo(() => {
    if (!curve) return null;
    const pool = curve.ys.filter((y): y is number => y !== null);
    if (Number.isFinite(result)) pool.push(result);
    if (referenceCurve) for (const y of referenceCurve.ys) if (y !== null) pool.push(y);
    if (compareCurves) for (const family of compareCurves) for (const y of family.ys) if (y !== null) pool.push(y);
    return pool.length > 0 ? { min: Math.min(...pool), max: Math.max(...pool) } : null;
  }, [curve, result, referenceCurve, compareCurves]);
  const yRange = useWidenOnlyRange(yKey, yCandidate);
  const yLog = yRange !== null && yRange.min > 0 && yRange.max / yRange.min >= LOG_SPAN;

  if (!fn || !target) {
    return <p className="text-sm text-muted-foreground">This equation cannot be evaluated here.</p>;
  }

  function solveFor(next: string) {
    // The old output becomes an input at the value it had, so the other
    // numbers on the card stay consistent with each other.
    // Rounded like a slider value, so its input box stays readable.
    if (Number.isFinite(result)) setValues((current) => ({ ...current, [target]: Number(result.toPrecision(4)) }));
    setTarget(next);
  }

  function setValue(symbol: string, value: number) {
    if (Number.isFinite(value)) setValues((current) => ({ ...current, [symbol]: value }));
  }

  const axisTitle = (symbol: string) => {
    const { name, unit } = variables[symbol];
    return `${name || symbol}${unit ? ` (${unit})` : ""}`;
  };
  const output = variables[target];
  const anyIllustrative = symbols.some((symbol) => variables[symbol].illustrative && variables[symbol].role === "input");

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)]">
      <div className="flex min-w-0 flex-col gap-4">
        {targets.length > 1 && (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted-foreground">Solve for</span>
            <div role="group" aria-label="Solve for" className="inline-flex gap-1 rounded-md bg-sunken p-1">
              {targets.map((symbol) => (
                <button
                  key={symbol}
                  type="button"
                  aria-pressed={symbol === target}
                  aria-label={`Solve for ${symbol}`}
                  title={variables[symbol].name}
                  onClick={() => solveFor(symbol)}
                  className={
                    "min-w-9 rounded px-3 py-1 text-sm transition-colors " +
                    (symbol === target
                      ? "bg-primary text-primary-foreground shadow-sm"
                      : "text-muted-foreground hover:bg-background hover:text-foreground")
                  }
                >
                  <MathTex tex={variables[symbol].latex} />
                </button>
              ))}
            </div>
          </div>
        )}

        <div data-testid="rearrangement" className="overflow-x-auto py-1 text-lg">
          <MathTex tex={`${output.latex} = ${solutions[target].latex}`} display />
        </div>

        <div className="flex flex-col gap-1 rounded-lg border border-primary/40 bg-primary/10 px-4 py-3">
          <span className="text-xs text-muted-foreground">{output.name || "Result"}</span>
          <div className="flex flex-wrap items-baseline gap-2">
            <span className="text-lg">
              <MathTex tex={output.latex} />
            </span>
            <span className="text-muted-foreground">=</span>
            <output aria-label={`Result ${target}`} className="font-mono text-3xl font-semibold tabular-nums text-foreground">
              <MathTex tex={valueTex(result)} />
            </output>
            <Unit unit={output.unit} />
          </div>
        </div>

        <div className="flex flex-col gap-2">
          {symbols
            .filter((symbol) => symbol !== target)
            .map((symbol) => {
              const variable = variables[symbol];
              const constant = variable.role === "constant";
              return (
                <div key={symbol} className="flex flex-col gap-1.5 rounded-md border border-border px-3 py-2">
                  <div className="flex items-baseline gap-2">
                    <MathTex tex={variable.latex} />
                    <span className="truncate text-xs text-muted-foreground">
                      {variable.name}
                      {!constant && variable.illustrative && (
                        <span title="Value and range are illustrative, not stated by the evidence"> ≈</span>
                      )}
                    </span>
                    {constant && <span className="ml-auto text-xs text-muted-foreground">constant</span>}
                  </div>
                  {constant ? (
                    <p className="flex items-baseline gap-2 font-mono text-sm text-foreground">
                      <MathTex tex={valueTex(values[symbol])} /> <Unit unit={variable.unit} />
                    </p>
                  ) : (
                    <div className="flex items-center gap-2">
                      <ValueSlider
                        symbol={symbol}
                        range={rangeOf(symbol)}
                        value={values[symbol]}
                        onChange={(value) => setValue(symbol, value)}
                      />
                      <input
                        type="number"
                        aria-label={`${symbol} value`}
                        step="any"
                        value={values[symbol]}
                        onChange={(event) => setValue(symbol, event.target.valueAsNumber)}
                        className="w-24 rounded border border-border bg-background px-2 py-1 font-mono text-sm tabular-nums"
                      />
                      <span className="w-16 shrink-0">
                        <Unit unit={variable.unit} />
                      </span>
                    </div>
                  )}
                </div>
              );
            })}
        </div>
        {anyIllustrative && (
          <p className="text-xs text-muted-foreground">≈ illustrative value and range, not stated by the evidence.</p>
        )}
      </div>

      {curve && x && (
        <figure className="flex min-w-0 flex-col gap-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <figcaption className="text-xs text-muted-foreground">
              {output.name || target} vs {variables[x].name || x}
            </figcaption>
            <div className="flex flex-wrap items-center gap-2">
              {inputs.length > 1 && (
                <label className="flex shrink-0 items-center gap-2 whitespace-nowrap text-xs text-muted-foreground">
                  Plot against
                  <select
                    value={x}
                    onChange={(event) => setXChoice(event.target.value)}
                    className="rounded border border-border bg-background px-2 py-1 text-foreground"
                  >
                    {inputs.map((symbol) => (
                      <option key={symbol} value={symbol}>
                        {variables[symbol].name || symbol}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {compareCandidates.length > 0 && (
                <label className="flex shrink-0 items-center gap-2 whitespace-nowrap text-xs text-muted-foreground">
                  Compare
                  <select
                    aria-label="Compare against"
                    value={activeCompare ?? ""}
                    onChange={(event) => setCompareWith(event.target.value || null)}
                    className="rounded border border-border bg-background px-2 py-1 text-foreground"
                  >
                    <option value="">Off</option>
                    {compareCandidates.map((symbol) => (
                      <option key={symbol} value={symbol}>
                        {variables[symbol].name || symbol}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <button
                type="button"
                onClick={() => setFitVersion((version) => version + 1)}
                className="shrink-0 rounded border border-border px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-sunken hover:text-foreground"
              >
                Fit
              </button>
            </div>
          </div>
          <Suspense fallback={<p className="text-sm text-muted-foreground">Loading plot…</p>}>
            <PlotlyVisualization
              data={[
                {
                  type: "scatter",
                  mode: "lines",
                  x: curve.xs,
                  y: curve.ys,
                  name: target,
                  connectgaps: false,
                  line: { color: accent, width: 2.5 },
                  hovertemplate: "%{x:.4g}, %{y:.4g}<extra></extra>",
                },
                {
                  type: "scatter",
                  mode: "markers",
                  x: [values[x]],
                  y: [Number.isFinite(result) ? result : null],
                  name: "current",
                  marker: { size: 12, color: accent, line: { color: "white", width: 2 } },
                  hovertemplate: "current: %{x:.4g}, %{y:.4g}<extra></extra>",
                },
                ...(referenceCurve
                  ? [
                      {
                        type: "scatter",
                        mode: "lines",
                        x: referenceCurve.xs,
                        y: referenceCurve.ys,
                        name: "starting values",
                        showlegend: false,
                        opacity: 0.35,
                        line: { color: accent, width: 1.5, dash: "dot" },
                        hoverinfo: "skip",
                      },
                    ]
                  : []),
                ...(compareCurves && activeCompare
                  ? compareCurves.map((family, index) => ({
                      type: "scatter",
                      mode: "lines",
                      x: family.xs,
                      y: family.ys,
                      name: `${variables[activeCompare].name || activeCompare} = ${formatValue(family.value)}${variables[activeCompare].unit ? ` ${variables[activeCompare].unit}` : ""}`,
                      opacity: 0.25 + (0.55 * index) / Math.max(1, COMPARE_CURVES - 1),
                      line: { color: accent, width: 1.5 },
                      hovertemplate: "%{x:.4g}, %{y:.4g}<extra></extra>",
                    }))
                  : []),
              ]}
              layout={{
                showlegend: !!(compareCurves && activeCompare),
                // dtick 1 on a log axis: label powers of ten only, not every 2 and 5.
                // automargin: wide tick labels (7×10³⁰) push the title out instead of under them.
                xaxis: {
                  title: { text: axisTitle(x) },
                  automargin: true,
                  exponentformat: "power",
                  range: axisRange(rangeOf(x), xLog),
                  ...(xLog && { type: "log", dtick: 1 }),
                },
                yaxis: {
                  title: { text: axisTitle(target) },
                  automargin: true,
                  exponentformat: "power",
                  range: yRange ? axisRange(yRange, yLog) : undefined,
                  ...(yLog && { type: "log", dtick: 1 }),
                },
              }}
            />
          </Suspense>
        </figure>
      )}
    </div>
  );
}

/** The system-of-equations counterpart of the card above: pick which
 * unknown set to solve for (each equation's own `sympy.solve`
 * combination), show one KaTeX line per solved symbol, and -- when
 * that set still leaves a free symbol -- plot one solved symbol
 * against it. Every rearrangement comes from the backend; this only
 * evaluates it. Named export so `AnswerEquations` loads it lazily
 * alongside the single-equation card. */
export function SystemEquationCard({ equation }: { equation: SystemInteractiveEquation }) {
  const { variables, systems } = equation;
  const symbols = Object.keys(variables);
  const accent = useMemo(accentColor, []);

  const compiledSystems = useMemo(() => {
    const built: { unknowns: string[]; remaining: string[]; compiled: Record<string, EvalFunction> }[] = [];
    for (const set of systems) {
      const remaining = symbols.filter((symbol) => !set.unknowns.includes(symbol));
      const compiled: Record<string, EvalFunction> = {};
      let ok = true;
      for (const unknown of set.unknowns) {
        const solution = set.solutions[unknown];
        try {
          compiled[unknown] = compileSolution(solution.expression, remaining);
        } catch {
          ok = false;
          break;
        }
      }
      if (ok) built.push({ unknowns: set.unknowns, remaining, compiled });
    }
    return built;
    // `symbols` and `systems` both come from `equation`.
  }, [equation]);

  const [setIndex, setSetIndex] = useState(0);
  const active = compiledSystems[Math.min(setIndex, compiledSystems.length - 1)] as
    | (typeof compiledSystems)[number]
    | undefined;

  const [values, setValues] = useState<Record<string, number>>(() =>
    Object.fromEntries(symbols.map((symbol) => [symbol, variables[symbol].value])),
  );
  const [xChoice, setXChoice] = useState<string | null>(null);
  const [yChoice, setYChoice] = useState<string | null>(null);
  const [fitVersion, setFitVersion] = useState(0);

  const rangeOf = (symbol: string): Range => ({
    min: Math.min(variables[symbol].min, values[symbol]),
    max: Math.max(variables[symbol].max, values[symbol]),
  });

  const inputCandidates = active ? active.remaining.filter((symbol) => variables[symbol].role === "input") : [];
  const x = xChoice && inputCandidates.includes(xChoice) ? xChoice : inputCandidates[0];
  const y = active ? (yChoice && active.unknowns.includes(yChoice) ? yChoice : active.unknowns[0]) : undefined;
  const xLog = x ? isLog(rangeOf(x)) : false;
  const fn = active && y ? active.compiled[y] : undefined;
  const result = fn ? evaluateAt(fn, values) : NaN;

  const curve = useMemo(() => {
    if (!fn || !x) return null;
    const { min, max } = rangeOf(x);
    const at = (i: number) => i / (CURVE_SAMPLES - 1);
    const xs = Array.from({ length: CURVE_SAMPLES }, (_, i) =>
      xLog ? 10 ** (Math.log10(min) + (Math.log10(max) - Math.log10(min)) * at(i)) : min + (max - min) * at(i),
    );
    const ys = xs.map((xv) => {
      const value = evaluateAt(fn, { ...values, [x]: xv });
      return Number.isNaN(value) ? null : value;
    });
    return { xs, ys };
  }, [fn, x, xLog, values]);

  const yKey = active && x && y ? `${active.unknowns.join(",")}|${x}|${y}|${fitVersion}` : null;
  const yCandidate = useMemo(() => {
    if (!curve) return null;
    const pool = curve.ys.filter((value): value is number => value !== null);
    if (Number.isFinite(result)) pool.push(result);
    return pool.length > 0 ? { min: Math.min(...pool), max: Math.max(...pool) } : null;
  }, [curve, result]);
  const yRange = useWidenOnlyRange(yKey, yCandidate);
  const yLog = yRange !== null && yRange.min > 0 && yRange.max / yRange.min >= LOG_SPAN;

  if (!active) {
    return <p className="text-sm text-muted-foreground">This system cannot be evaluated here.</p>;
  }

  function setValue(symbol: string, value: number) {
    if (Number.isFinite(value)) setValues((current) => ({ ...current, [symbol]: value }));
  }

  const axisTitle = (symbol: string) => {
    const { name, unit } = variables[symbol];
    return `${name || symbol}${unit ? ` (${unit})` : ""}`;
  };
  const results: Record<string, number> = Object.fromEntries(
    active.unknowns.map((unknown) => [unknown, evaluateAt(active.compiled[unknown], values)]),
  );
  const freeInputs = active.remaining.filter((symbol) => variables[symbol].role === "input");

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)]">
      <div className="flex min-w-0 flex-col gap-4">
        {compiledSystems.length > 1 && (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted-foreground">Solve for</span>
            <div role="group" aria-label="Solve for" className="inline-flex flex-wrap gap-1 rounded-md bg-sunken p-1">
              {compiledSystems.map((set, index) => (
                <button
                  key={set.unknowns.join(",")}
                  type="button"
                  aria-pressed={index === setIndex}
                  aria-label={`Solve for ${set.unknowns.join(", ")}`}
                  onClick={() => setSetIndex(index)}
                  className={
                    "rounded px-3 py-1 text-sm transition-colors " +
                    (index === setIndex
                      ? "bg-primary text-primary-foreground shadow-sm"
                      : "text-muted-foreground hover:bg-background hover:text-foreground")
                  }
                >
                  {set.unknowns.map((symbol) => variables[symbol].name || symbol).join(", ")}
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="flex flex-col gap-2 rounded-lg border border-primary/40 bg-primary/10 px-4 py-3">
          {active.unknowns.map((unknown) => (
            <div key={unknown} className="flex flex-wrap items-baseline gap-2">
              <span className="text-lg">
                <MathTex tex={variables[unknown].latex} />
              </span>
              <span className="text-muted-foreground">=</span>
              <output aria-label={`Result ${unknown}`} className="font-mono text-2xl font-semibold tabular-nums text-foreground">
                <MathTex tex={valueTex(results[unknown])} />
              </output>
              <Unit unit={variables[unknown].unit} />
            </div>
          ))}
        </div>

        {active.remaining.length > 0 && (
          <div className="flex flex-col gap-2">
            {active.remaining.map((symbol) => {
              const variable = variables[symbol];
              const constant = variable.role === "constant";
              return (
                <div key={symbol} className="flex flex-col gap-1.5 rounded-md border border-border px-3 py-2">
                  <div className="flex items-baseline gap-2">
                    <MathTex tex={variable.latex} />
                    <span className="truncate text-xs text-muted-foreground">{variable.name}</span>
                    {constant && <span className="ml-auto text-xs text-muted-foreground">constant</span>}
                  </div>
                  {constant ? (
                    <p className="flex items-baseline gap-2 font-mono text-sm text-foreground">
                      <MathTex tex={valueTex(values[symbol])} /> <Unit unit={variable.unit} />
                    </p>
                  ) : (
                    <div className="flex items-center gap-2">
                      <ValueSlider
                        symbol={symbol}
                        range={rangeOf(symbol)}
                        value={values[symbol]}
                        onChange={(value) => setValue(symbol, value)}
                      />
                      <input
                        type="number"
                        aria-label={`${symbol} value`}
                        step="any"
                        value={values[symbol]}
                        onChange={(event) => setValue(symbol, event.target.valueAsNumber)}
                        className="w-24 rounded border border-border bg-background px-2 py-1 font-mono text-sm tabular-nums"
                      />
                      <span className="w-16 shrink-0">
                        <Unit unit={variable.unit} />
                      </span>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {freeInputs.length === 0 && (
          <p className="text-xs text-muted-foreground">
            This set has no free input left to plot -- every symbol is solved or fixed.
          </p>
        )}
      </div>

      {curve && x && y && (
        <figure className="flex min-w-0 flex-col gap-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <figcaption className="text-xs text-muted-foreground">
              {variables[y].name || y} vs {variables[x].name || x}
            </figcaption>
            <div className="flex flex-wrap items-center gap-2">
              {active.unknowns.length > 1 && (
                <label className="flex shrink-0 items-center gap-2 whitespace-nowrap text-xs text-muted-foreground">
                  Plot
                  <select
                    value={y}
                    onChange={(event) => setYChoice(event.target.value)}
                    className="rounded border border-border bg-background px-2 py-1 text-foreground"
                  >
                    {active.unknowns.map((symbol) => (
                      <option key={symbol} value={symbol}>
                        {variables[symbol].name || symbol}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {inputCandidates.length > 1 && (
                <label className="flex shrink-0 items-center gap-2 whitespace-nowrap text-xs text-muted-foreground">
                  vs
                  <select
                    value={x}
                    onChange={(event) => setXChoice(event.target.value)}
                    className="rounded border border-border bg-background px-2 py-1 text-foreground"
                  >
                    {inputCandidates.map((symbol) => (
                      <option key={symbol} value={symbol}>
                        {variables[symbol].name || symbol}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <button
                type="button"
                onClick={() => setFitVersion((version) => version + 1)}
                className="shrink-0 rounded border border-border px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-sunken hover:text-foreground"
              >
                Fit
              </button>
            </div>
          </div>
          <Suspense fallback={<p className="text-sm text-muted-foreground">Loading plot…</p>}>
            <PlotlyVisualization
              data={[
                {
                  type: "scatter",
                  mode: "lines",
                  x: curve.xs,
                  y: curve.ys,
                  name: y,
                  connectgaps: false,
                  line: { color: accent, width: 2.5 },
                  hovertemplate: "%{x:.4g}, %{y:.4g}<extra></extra>",
                },
                {
                  type: "scatter",
                  mode: "markers",
                  x: [values[x]],
                  y: [Number.isFinite(result) ? result : null],
                  name: "current",
                  marker: { size: 12, color: accent, line: { color: "white", width: 2 } },
                  hovertemplate: "current: %{x:.4g}, %{y:.4g}<extra></extra>",
                },
              ]}
              layout={{
                showlegend: false,
                xaxis: {
                  title: { text: axisTitle(x) },
                  automargin: true,
                  exponentformat: "power",
                  range: axisRange(rangeOf(x), xLog),
                  ...(xLog && { type: "log", dtick: 1 }),
                },
                yaxis: {
                  title: { text: axisTitle(y) },
                  automargin: true,
                  exponentformat: "power",
                  range: yRange ? axisRange(yRange, yLog) : undefined,
                  ...(yLog && { type: "log", dtick: 1 }),
                },
              }}
            />
          </Suspense>
        </figure>
      )}
    </div>
  );
}
