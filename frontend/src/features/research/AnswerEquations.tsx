import { FlaskConical } from "lucide-react";
import { lazy, Suspense } from "react";

import { MathTex } from "@/features/research/MathTex";

/** One symbol of an equation (`schemas.EquationVariable`), with the
 * `latex` the backend typeset from its name (`Omega_H` -> `\Omega_{H}`). */
export interface EquationVariable {
  name: string;
  unit: string;
  role: "input" | "constant";
  value: number;
  min: number;
  max: number;
  illustrative: boolean;
  latex: string;
}

/** One unknown set a system equation's members were jointly solved
 * for (`planner.equations._system_solutions`), e.g. `{x, y}` for
 * `x + y = 5, x - y = 1`. `solutions` holds one closed form per
 * unknown, computed the same way a single equation's is: by the
 * backend with sympy, never by the model. */
export interface EquationSystemSet {
  unknowns: string[];
  solutions: Record<string, { expression: string; latex: string }>;
}

/** One equation the backend validated for an answer
 * (`planner.equations.build_equations`). `solutions` holds every "solve
 * for" rearrangement, computed by the backend with sympy -- never by the
 * model. `expression` is null and `solutions` empty for equations that
 * display but cannot be computed (integrals, tensors). `expressions` and
 * `systems` are the system-of-equations form, mutually exclusive with
 * `expression`/`solutions`. Runs stored before variables carried values
 * have plain-string variables; runs stored before either of these
 * existed have neither field at all. */
export interface AnswerEquation {
  label?: string;
  latex: string;
  expression?: string | null;
  expressions?: string[] | null;
  variables?: Record<string, EquationVariable | string>;
  output?: string | null;
  solutions?: Record<string, { expression: string; latex: string }>;
  systems?: EquationSystemSet[];
}

export type InteractiveEquation = AnswerEquation & {
  variables: Record<string, EquationVariable>;
  solutions: Record<string, { expression: string; latex: string }>;
};

export type SystemInteractiveEquation = AnswerEquation & {
  variables: Record<string, EquationVariable>;
  systems: EquationSystemSet[];
};

// mathjs and Plotly are large, and most answers carry no computable
// equation: loaded only when an interactive card renders.
const InteractiveEquationCard = lazy(() => import("@/features/research/InteractiveEquationCard"));
const SystemEquationCard = lazy(() =>
  import("@/features/research/InteractiveEquationCard").then((module) => ({ default: module.SystemEquationCard })),
);

function isInteractive(equation: AnswerEquation): equation is InteractiveEquation {
  return (
    !!equation.solutions &&
    Object.keys(equation.solutions).length > 0 &&
    Object.values(equation.variables ?? {}).every((variable) => typeof variable !== "string")
  );
}

function isSystemInteractive(equation: AnswerEquation): equation is SystemInteractiveEquation {
  return (
    !!equation.systems &&
    equation.systems.length > 0 &&
    Object.values(equation.variables ?? {}).every((variable) => typeof variable !== "string")
  );
}

/** The Research page's Experiment Playground: the equations an answer
 * relies on, typeset. A computable one becomes an interactive card;
 * the rest are shown with their variables. Rendered only when the run
 * carries equations. */
export function AnswerEquations({ equations }: { equations: AnswerEquation[] }) {
  return (
    <section aria-labelledby="experiment-playground" className="flex flex-col gap-4 rounded-lg bg-sunken/40 p-4">
      <div className="flex items-center gap-2">
        <FlaskConical aria-hidden="true" className="h-4 w-4 text-primary" />
        <h3 id="experiment-playground" className="text-sm font-medium text-foreground">
          Experiment Playground
        </h3>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        {equations.map((equation, index) => (
          <article
            key={index}
            className={
              "flex flex-col gap-3 rounded-md border border-border bg-background p-3 " +
              (isInteractive(equation) || isSystemInteractive(equation) ? "sm:col-span-2" : "")
            }
          >
            {equation.label && <p className="text-xs font-medium text-muted-foreground">{equation.label}</p>}
            {isInteractive(equation) ? (
              // The card typesets the equation itself, as the current rearrangement.
              <Suspense fallback={<p className="text-sm text-muted-foreground">Loading calculator…</p>}>
                <InteractiveEquationCard equation={equation} />
              </Suspense>
            ) : isSystemInteractive(equation) ? (
              <>
                <div className="overflow-x-auto">
                  <MathTex tex={equation.latex} display />
                </div>
                <Suspense fallback={<p className="text-sm text-muted-foreground">Loading calculator…</p>}>
                  <SystemEquationCard equation={equation} />
                </Suspense>
              </>
            ) : (
              <>
                <div className="overflow-x-auto">
                  <MathTex tex={equation.latex} display />
                </div>
                {equation.variables && Object.keys(equation.variables).length > 0 && (
                  <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
                    {Object.entries(equation.variables).map(([symbol, variable]) => (
                      <div key={symbol} className="contents">
                        <dt className="text-foreground">
                          <MathTex tex={typeof variable === "string" ? symbol : variable.latex} />
                        </dt>
                        <dd className="text-muted-foreground">
                          {typeof variable === "string" ? variable : variable.name}
                        </dd>
                      </div>
                    ))}
                  </dl>
                )}
              </>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}
