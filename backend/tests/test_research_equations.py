"""Synthesis equations: validation, safe parsing, and backend rearrangement."""

import json

import pytest
import sympy

from app.agents.planner.equations import build_equations
from app.agents.planner.experiment import EquationParseError
from app.agents.planner.synthesis import _parse_response
from app.modules.research.schemas import EquationVariable

DISTANCE = {
    "label": "Distance",
    "latex": "d = v t",
    "expression": "d = v*t",
    "variables": {
        "d": {"name": "distance", "unit": "m", "value": 20, "min": 0, "max": 100},
        "v": {"name": "speed", "unit": "m/s", "value": 2, "min": 0, "max": 10, "illustrative": False},
        "t": {"name": "time", "unit": "s", "value": 10, "min": 0, "max": 60},
    },
}

SCHWARZSCHILD = {
    "label": "Schwarzschild radius",
    "latex": r"r_s = \frac{2GM}{c^2}",
    "expression": "r_s = 2*G*M/c**2",
    "variables": {
        "r_s": {"name": "Schwarzschild radius", "unit": "m", "value": 2954, "min": 0, "max": 10000},
        "M": {"name": "mass", "unit": "kg", "value": 2e30, "min": 1e29, "max": 1e32},
        "G": {"name": "gravitational constant", "role": "constant", "value": 6.674e-11, "illustrative": False},
        "c": {"name": "speed of light", "unit": "m/s", "role": "constant", "value": 2.998e8, "illustrative": False},
    },
}

EFFECTIVE_POTENTIAL = {
    "label": "Effective potential",
    "latex": r"V(r) = \left(1 - \frac{2M}{r}\right)\left(1 + \frac{L^2}{r^2}\right)",
    "expression": "V = (1 - 2*M/r)*(1 + L**2/r**2)",
    "variables": {
        "V": {"name": "effective potential", "value": 0.9, "min": 0, "max": 1.2},
        "r": {"name": "radius", "value": 16, "min": 2, "max": 30},
        "M": {"name": "mass", "value": 1, "min": 0.5, "max": 2},
        "L": {"name": "angular momentum", "value": 4, "min": 0, "max": 8},
    },
}


def _as_sympy(solution: dict) -> sympy.Expr:
    return sympy.sympify(solution["expression"].replace("^", "**"))


def test_distance_solves_for_all_three():
    [equation] = build_equations([DISTANCE])
    solutions = equation["solutions"]
    d, v, t = sympy.symbols("d v t")
    assert equation["output"] == "d"
    assert set(solutions) == {"d", "v", "t"}
    assert sympy.simplify(_as_sympy(solutions["d"]) - v * t) == 0
    assert sympy.simplify(_as_sympy(solutions["v"]) - d / t) == 0
    assert sympy.simplify(_as_sympy(solutions["t"]) - d / v) == 0
    assert solutions["v"]["latex"] == r"\frac{d}{t}"
    assert equation["variables"]["v"]["unit"] == "m/s"
    assert equation["variables"]["v"]["illustrative"] is False


def test_schwarzschild_never_solves_for_constants():
    [equation] = build_equations([SCHWARZSCHILD])
    assert set(equation["solutions"]) == {"r_s", "M"}
    r_s, G, c = sympy.symbols("r_s G c")
    assert sympy.simplify(_as_sympy(equation["solutions"]["M"]) - c**2 * r_s / (2 * G)) == 0
    assert equation["variables"]["r_s"]["latex"] == "r_{s}"
    assert equation["variables"]["G"]["role"] == "constant"


def test_effective_potential_skips_the_non_unique_radius():
    [equation] = build_equations([EFFECTIVE_POTENTIAL])
    solutions = equation["solutions"]
    # r is a root of a cubic, so no single closed form; L is the positive root.
    assert set(solutions) == {"V", "M", "L"}
    V = _as_sympy(solutions["V"]).subs({"r": 16, "M": 1, "L": 4})
    assert float(V) == pytest.approx((1 - 2 / 16) * (1 + 16 / 16**2))
    L = _as_sympy(solutions["L"]).subs({"V": float(V), "r": 16, "M": 1})
    assert float(L) == pytest.approx(4)


def test_multi_root_solution_is_skipped():
    [equation] = build_equations([{"latex": r"y = \sin x", "expression": "y = sin(x)"}])
    assert set(equation["solutions"]) == {"y"}


def test_variables_match_the_expression_symbols():
    [equation] = build_equations(
        [{**DISTANCE, "variables": {"v": DISTANCE["variables"]["v"], "q": {"name": "not in the equation"}}}]
    )
    assert set(equation["variables"]) == {"d", "v", "t"}
    # The model never described t: it gets an illustrative default.
    assert equation["variables"]["t"] == {
        "name": "", "unit": "", "role": "input", "value": 1.0, "min": 0.0, "max": 2.0,
        "illustrative": True, "latex": "t",
    }


def test_greek_symbol_latex():
    [equation] = build_equations([{"latex": r"\Omega_H", "expression": "w = Omega_H*r"}])
    assert equation["variables"]["Omega_H"]["latex"] == r"\Omega_{H}"


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        pytest.param("radius", {"name": "radius", "value": 1.0, "min": 0.0, "max": 2.0, "illustrative": True}, id="bare-string"),
        pytest.param({"value": 5, "min": 10, "max": 20, "illustrative": False}, {"value": 5, "min": 0, "max": 10, "illustrative": True}, id="value-outside-range"),
        pytest.param({"value": 5, "min": 0, "max": 20, "illustrative": False}, {"value": 5, "min": 0, "max": 20, "illustrative": False}, id="usable-range-kept"),
        pytest.param({"value": 0}, {"value": 0, "min": -1, "max": 1, "illustrative": True}, id="zero-value"),
    ],
)
def test_variable_range_is_always_usable(given, expected):
    variable = EquationVariable.model_validate(given).model_dump()
    assert {key: variable[key] for key in expected} == expected


@pytest.mark.parametrize(
    "expression",
    [
        pytest.param("y = __import__('os').system('echo hi')", id="unsafe"),
        pytest.param("y = ().__class__", id="attribute-access"),
        pytest.param("r_s = 2*G*", id="malformed"),
        pytest.param("y = y + 1", id="output-on-both-sides"),
        pytest.param("2*x + 1", id="no-output-name"),
    ],
)
def test_bad_expression_keeps_the_card_but_never_computes(expression):
    [equation] = build_equations([{"latex": "y", "expression": expression}])
    assert equation["latex"] == "y"
    assert equation["expression"] is None
    assert equation["output"] is None
    assert equation["solutions"] == {}


def test_latex_only_equation_is_shown_without_computing():
    smarr = {
        "label": "Integral mass formula",
        "latex": r"M = \int_S (2{T_a}^b - T{\delta_a}^b) K^a d\Sigma_b + 2\Omega_H J_H + \frac{1}{4\pi} \int_{\partial B} \kappa \, dA",
        "expression": None,
        "variables": {"M": "total mass", "\\Omega_H": "horizon angular velocity"},
    }
    [equation] = build_equations([smarr])
    assert equation["latex"].startswith(r"M = \int_S")
    assert equation["expression"] is None
    assert equation["solutions"] == {}
    assert equation["variables"]["\\Omega_H"]["latex"] == "\\Omega_H"
    assert equation["variables"]["\\Omega_H"]["name"] == "horizon angular velocity"


@pytest.mark.parametrize(
    "item",
    [
        pytest.param({"expression": "y = x"}, id="no-latex"),
        pytest.param("not an object", id="not-an-object"),
        pytest.param({**DISTANCE, "variables": {"v": {"role": "slider"}}}, id="unknown-role"),
    ],
)
def test_invalid_entry_is_dropped(item):
    assert build_equations([item]) == []


@pytest.mark.parametrize("raw", [None, {}, "equations", 3])
def test_non_list_yields_no_equations(raw):
    assert build_equations(raw) == []


LINEAR_SYSTEM = {
    "label": "Linear system",
    "latex": r"x + y = 5 \\ x - y = 1",
    "expressions": ["x + y = 5", "x - y = 1"],
    "variables": {
        "x": {"name": "first quantity"},
        "y": {"name": "second quantity"},
    },
}


def test_system_solves_shared_symbols():
    [equation] = build_equations([LINEAR_SYSTEM])
    assert equation["expression"] is None
    assert equation["output"] is None
    assert equation["solutions"] == {}
    [system] = equation["systems"]
    assert system["unknowns"] == ["x", "y"]
    assert sympy.sympify(system["solutions"]["x"]["expression"]) == 3
    assert sympy.sympify(system["solutions"]["y"]["expression"]) == 2


def test_system_updates_when_an_input_changes():
    """x + y = 5, x - y = a: solving for {x, y} keeps a free parameter,
    so changing that parameter (done client-side, not here) updates
    both -- checked at the sympy level, where the frontend's evaluator
    would compute from the same closed form."""
    [equation] = build_equations(
        [{**LINEAR_SYSTEM, "expressions": ["x + y = 5", "x - y = a"], "variables": {**LINEAR_SYSTEM["variables"], "a": {"name": "difference"}}}]
    )
    system = next(s for s in equation["systems"] if s["unknowns"] == ["x", "y"])
    a = sympy.Symbol("a")
    x = sympy.sympify(system["solutions"]["x"]["expression"].replace("^", "**"))
    y = sympy.sympify(system["solutions"]["y"]["expression"].replace("^", "**"))
    assert x.subs(a, 1) == 3 and y.subs(a, 1) == 2
    assert x.subs(a, 3) == 4 and y.subs(a, 3) == 1


def test_system_omits_the_non_unique_unknown_set():
    # Sum/product form: x + z = 5 and x*z = 6 has two ordered real roots,
    # {x: 2, z: 3} and {x: 3, z: 2} -- genuinely ambiguous (both orders
    # are positive, so the positive-symbol pass does not resolve it
    # either), so the only candidate set is dropped rather than guessed.
    [equation] = build_equations(
        [{"label": "Sum and product", "latex": r"x + z = 5 \\ x z = 6", "expressions": ["x + z = 5", "x*z = 6"]}]
    )
    assert equation["systems"] == []


def test_pathological_system_falls_back_to_display_only(monkeypatch):
    def _never_finishes(*args, **kwargs):
        raise EquationParseError("Expression took longer than 5s to parse/evaluate")

    monkeypatch.setattr("app.agents.planner.equations.sympy.solve", _never_finishes)

    [equation] = build_equations([LINEAR_SYSTEM])

    assert equation["systems"] == []
    assert equation["expressions"] == ["x + y = 5", "x - y = 1"]
    assert equation["latex"] == LINEAR_SYSTEM["latex"]


def test_system_needs_at_least_two_equations():
    [equation] = build_equations([{**LINEAR_SYSTEM, "expressions": ["x + y = 5"]}])
    assert equation["expressions"] is None
    assert equation["systems"] == []


def test_single_equation_still_carries_an_empty_systems_list():
    [equation] = build_equations([DISTANCE])
    assert equation["systems"] == []


def _response(**extra) -> str:
    return json.dumps(
        {"answer": "Orbits are bound inside the well [c1].", "citation_ids": ["c1"], "grounding_status": "grounded", **extra}
    )


def test_parse_response_carries_equations():
    _, _, _, _, equations, _, _ = _parse_response(_response(equations=[EFFECTIVE_POTENTIAL]))
    assert [e["label"] for e in equations] == ["Effective potential"]


def test_parse_response_without_equations_yields_empty_list():
    _, _, _, _, equations, _, _ = _parse_response(_response())
    assert equations == []
