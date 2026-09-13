"""Equations a synthesis answer carries: validated, then rearranged.

The model proposes each equation -- LaTeX for display, what its symbols
mean with a value and range, and, when it can be written as plain math,
an expression. None of that is trusted as-is:

- The expression goes through `experiment.parse_equation`, the same
  hardened parser the Experiment Plan Engine uses, so an unsafe or
  malformed expression is never evaluated. The equation is still shown
  from its LaTeX; only computing with it is dropped.
- Every "solve for" rearrangement is computed here with `sympy.solve`,
  never by the model, and only kept when it is unique, real and uses
  functions the client evaluator accepts. The client evaluates these
  strings for the interactive card; it never sees model-written math.

An equation may instead carry `expressions` -- two or three equations
sharing symbols, e.g. `["x + y = 5", "x - y = 1"]`. That system path
solves every combination of input symbols sized to the equation count
the same way: real symbols first, then positive, keeping only a unique
closed form. The model is never asked to solve either form itself.

Lenient like the rest of synthesis parsing: a bad entry is dropped and
logged, never allowed to fail an answer that otherwise parsed.
"""

import itertools
import re

import sympy
from pydantic import ValidationError

from app.agents.planner.experiment import (
    EquationParseError,
    EquationParseResult,
    _bounded_parse_time,
    parse_equation,
)
from app.core.logging.logger import get_logger
from app.modules.research.schemas import Equation, EquationVariable

logger = get_logger(__name__)

#: Upper bound on equations kept from one answer.
_MAX_EQUATIONS = 6
#: A system needs at least two equations to be worth solving, and at
#: most three -- more than that is rarely something a 4B model states
#: coherently, and the combination search below grows with it.
_MIN_SYSTEM_EQUATIONS = 2
_MAX_SYSTEM_EQUATIONS = 3
#: Every combination of input symbols sized to the equation count is
#: tried; capped so a model that lists many input variables cannot turn
#: one answer into an unbounded search, even inside one time budget.
_MAX_SYSTEM_COMBINATIONS = 20
#: Functions a solution may call -- the same set the frontend's
#: `InteractiveEquationCard` evaluator allows. Anything else (Abs,
#: Piecewise, LambertW) drops that one rearrangement.
_ALLOWED_FUNCTIONS = frozenset(
    {"exp", "log", "sin", "cos", "tan", "asin", "acos", "atan", "sinh", "cosh", "tanh"}
)
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def build_equations(raw: object) -> list[dict]:
    """Validate the model's `equations` entries and rearrange each one.

    Each kept entry is the dumped `Equation` (every variable also carrying
    its symbol's `latex`, e.g. `\\Omega_{H}`) plus:

    - `output`: the symbol on the left of the expression, or `None`.
    - `solutions`: `{symbol: {"expression": ..., "latex": ...}}`, one per
      input symbol with a unique closed form. `expression` is plain math
      with `^` for powers. Empty for an equation that only displays.

    An entry whose expression is missing, does not parse, or has no
    output name keeps its LaTeX but carries `expression` as `None`, so
    nothing downstream computes with it.
    """
    if not isinstance(raw, list):
        return []

    built: list[dict] = []
    for item in raw[:_MAX_EQUATIONS]:
        try:
            equation = Equation.model_validate(item)
        except ValidationError as exc:
            logger.warning("synthesis_equation_dropped", reason=str(exc)[:200])
            continue

        if equation.expressions:
            built.append(_build_system_equation(equation))
            continue

        parsed = None
        if equation.expression is not None:
            try:
                parsed = parse_equation(equation.expression)
            except EquationParseError as exc:
                logger.warning("synthesis_equation_expression_dropped", reason=str(exc)[:200])
        if parsed is None or parsed.output_name is None:
            built.append(_dump(equation.model_copy(update={"expression": None}), equation.variables, None, {}))
            continue

        variables = _variables_for(equation, parsed)
        built.append(_dump(equation, variables, parsed.output_name, _solutions(parsed, variables)))
    return built


def _variables_for(equation: Equation, parsed: EquationParseResult) -> dict[str, EquationVariable]:
    """Exactly the expression's symbols: a variable the expression does not
    use is dropped, and a symbol the model did not describe gets an
    illustrative default so every slider has a value and range."""
    symbols = {str(symbol) for symbol in parsed.rhs_expr.free_symbols} | {parsed.output_name}
    stray = sorted(set(equation.variables) - symbols)
    if stray:
        logger.warning("synthesis_equation_variables_dropped", symbols=stray)
    return {symbol: equation.variables.get(symbol) or EquationVariable() for symbol in sorted(symbols)}


def _solutions(parsed: EquationParseResult, variables: dict[str, EquationVariable]) -> dict[str, dict]:
    """Rearrange for every input symbol, all within one parse-time budget:
    a pathological equation keeps whatever rearrangements finished."""
    solutions: dict[str, dict] = {}
    try:
        with _bounded_parse_time():
            for target, variable in variables.items():
                if variable.role != "input":
                    continue
                solution = _solve_uniquely(parsed.output_name, parsed.rhs_expr, target)
                if solution is not None:
                    solutions[target] = {
                        "expression": sympy.sstr(solution).replace("**", "^"),
                        "latex": sympy.latex(solution),
                    }
    except EquationParseError as exc:
        logger.warning("synthesis_equation_solve_timeout", reason=str(exc)[:200])
    return solutions


def _solve_uniquely(output: str, rhs: sympy.Expr, target: str) -> sympy.Expr | None:
    """The single closed form of `output = rhs` for `target`, or `None`.

    Tried over real symbols first, then positive ones, where a root
    with a leading minus sign (the `-` of a `±` pair sympy cannot rule
    out) is dropped: `r_s = 2GM/c^2` has two real roots for `c` but one
    positive root, and physical magnitudes are positive.

    Cubic/quartic formulas are not attempted: they are rarely usable
    closed forms, and sympy spends tens of seconds deriving them in
    ways the `signal.alarm` budget cannot reliably interrupt."""
    names = {str(symbol) for symbol in rhs.free_symbols} | {output}
    for assumption in ({"real": True}, {"positive": True}):
        symbols = {name: sympy.Symbol(name, **assumption) for name in names}
        bound = rhs.xreplace({symbol: symbols[str(symbol)] for symbol in rhs.free_symbols})
        try:
            roots = sympy.solve(sympy.Eq(symbols[output], bound), symbols[target], cubics=False, quartics=False)
        except (NotImplementedError, ValueError, TypeError):
            continue
        if "positive" in assumption and len(roots) == 2 and sympy.expand(roots[0] + roots[1]) == 0:
            roots = [root for root in roots if not root.could_extract_minus_sign()]
        if len(roots) == 1 and _computable(roots[0]):
            return roots[0]
    return None


def _build_system_equation(equation: Equation) -> dict:
    """The system-of-equations counterpart of the single-`expression`
    path above: parse every member equation, solve for every candidate
    unknown set, and dump the result with `systems` filled instead of
    `output`/`solutions`.

    Too few equations survive parsing (fewer than
    `_MIN_SYSTEM_EQUATIONS`) keeps the LaTeX display-only, exactly like
    a single equation whose `expression` failed to parse.
    """
    texts = equation.expressions[:_MAX_SYSTEM_EQUATIONS] if equation.expressions else []
    parsed_list: list[EquationParseResult] = []
    for text in texts:
        try:
            parsed_list.append(_parse_system_equation(text))
        except EquationParseError as exc:
            logger.warning("synthesis_system_expression_dropped", reason=str(exc)[:200])

    if len(parsed_list) < _MIN_SYSTEM_EQUATIONS:
        return _dump(equation.model_copy(update={"expressions": None}), equation.variables, None, {})

    variables = _variables_for_system(equation, parsed_list)
    systems = _system_solutions(parsed_list, variables)
    return _dump(equation, variables, None, {}, systems)


def _parse_system_equation(expression: str) -> EquationParseResult:
    """Parse one member of a system, keeping the whole equation rather
    than only its right-hand side.

    A single equation's `expression` is naturally `name = rhs`, so
    `parse_equation` only needs the right side. A system member rarely
    is (`x + y = 5` has no lone symbol on the left), so this rewrites
    it as the difference `(lhs) - (rhs)` under a placeholder output
    name before handing it to `parse_equation` -- the same hardened,
    time-bounded parser, just fed the full equation."""
    raw = expression.strip()
    if "=" in raw:
        lhs, _, rhs = raw.partition("=")
        combined = f"__sys__ = ({lhs.strip()}) - ({rhs.strip()})"
    else:
        combined = f"__sys__ = {raw}"
    return parse_equation(combined)


def _variables_for_system(equation: Equation, parsed_list: list[EquationParseResult]) -> dict[str, EquationVariable]:
    """Every symbol used by at least one member equation; a variable the
    model described but no equation uses is dropped, matching
    `_variables_for`'s single-equation rule."""
    symbols: set[str] = set()
    for parsed in parsed_list:
        symbols |= {str(symbol) for symbol in parsed.rhs_expr.free_symbols}
    stray = sorted(set(equation.variables) - symbols)
    if stray:
        logger.warning("synthesis_equation_variables_dropped", symbols=stray)
    return {symbol: equation.variables.get(symbol) or EquationVariable() for symbol in sorted(symbols)}


def _system_solutions(parsed_list: list[EquationParseResult], variables: dict[str, EquationVariable]) -> list[dict]:
    """Solve every combination of input symbols sized to the equation
    count, all within one parse-time budget: a pathological system
    keeps whatever combinations finished, and a budget that runs out
    entirely yields no systems (the card falls back to display-only)."""
    exprs = [parsed.rhs_expr for parsed in parsed_list]
    k = len(exprs)
    input_symbols = sorted(symbol for symbol, variable in variables.items() if variable.role == "input")
    combinations = list(itertools.islice(itertools.combinations(input_symbols, k), _MAX_SYSTEM_COMBINATIONS))

    systems: list[dict] = []
    try:
        with _bounded_parse_time():
            for combo in combinations:
                solution = _solve_system_uniquely(exprs, combo)
                if solution is not None:
                    systems.append({"unknowns": list(combo), "solutions": solution})
    except EquationParseError as exc:
        logger.warning("synthesis_system_solve_timeout", reason=str(exc)[:200])
        return []
    return systems


def _solve_system_uniquely(exprs: list[sympy.Expr], unknowns: tuple[str, ...]) -> dict[str, dict] | None:
    """The single, real, closed-form solution of `exprs = 0` for
    `unknowns`, or `None` -- tried over real symbols first, then
    positive ones, mirroring `_solve_uniquely`'s strategy for one
    equation."""
    names: set[str] = set()
    for expr in exprs:
        names |= {str(symbol) for symbol in expr.free_symbols}

    for assumption in ({"real": True}, {"positive": True}):
        symbols = {name: sympy.Symbol(name, **assumption) for name in names}
        bound = [expr.xreplace({symbol: symbols[str(symbol)] for symbol in expr.free_symbols}) for expr in exprs]
        targets = [symbols[name] for name in unknowns]
        try:
            candidates = sympy.solve(bound, targets, dict=True)
        except (NotImplementedError, ValueError, TypeError):
            continue
        real_candidates = [
            candidate
            for candidate in candidates
            if len(candidate) == len(targets) and all(_computable(candidate[target]) for target in targets)
        ]
        if len(real_candidates) == 1:
            solution = real_candidates[0]
            return {
                str(target): {
                    "expression": sympy.sstr(solution[target]).replace("**", "^"),
                    "latex": sympy.latex(solution[target]),
                }
                for target in targets
            }
    return None


def _computable(expr: sympy.Expr) -> bool:
    return not expr.has(sympy.I) and all(
        type(function).__name__ in _ALLOWED_FUNCTIONS for function in expr.atoms(sympy.Function)
    )


def _symbol_latex(symbol: str) -> str:
    """`Omega_H` -> `\\Omega_{H}`. A key that is already LaTeX (a LaTeX-only
    equation's `\\Omega_H`) is kept as written."""
    return sympy.latex(sympy.Symbol(symbol)) if _IDENTIFIER.fullmatch(symbol) else symbol


def _dump(
    equation: Equation,
    variables: dict[str, EquationVariable],
    output: str | None,
    solutions: dict[str, dict],
    systems: list[dict] | None = None,
) -> dict:
    return {
        **equation.model_dump(mode="json"),
        "variables": {
            symbol: {**variable.model_dump(mode="json"), "latex": _symbol_latex(symbol)}
            for symbol, variable in variables.items()
        },
        "output": output,
        "solutions": solutions,
        "systems": systems or [],
    }
