"""Test-case visualization data prep (Sprint 16 Phase 6, Part H/R).

The backend only ever returns raw trace data -- no chart is rendered
server-side, and no causal claim is generated from a mere trend.
"""

import pytest

from app.agents.planner.experiment import (
    NO_GROUP_VALUE,
    choose_series_kind,
    describe_trend,
    group_test_cases,
    prepare_input_output_series,
)
from app.modules.research.experiment_schemas import (
    ExperimentPlanCreateFromEquation,
    ExperimentOutputValue,
    ExperimentTestCase,
    OutputKind,
)
from app.modules.research.experiment_service import ExperimentPlanService


def _case(id_, x, y):
    return ExperimentTestCase(
        id=id_,
        inputs={"X": x},
        expected_outputs=[ExperimentOutputValue(output_name="Y", value=y, kind=OutputKind.EXPECTED)],
    )


class TestPrepareInputOutputSeries:
    def test_numeric_cases_sorted_by_x(self):
        cases = [_case("c1", "2", "5.0"), _case("c2", "1", "2.5")]
        pairs = prepare_input_output_series(cases, "X", "Y")
        assert pairs == [("1", 2.5), ("2", 5.0)]

    def test_missing_input_skipped(self):
        cases = [ExperimentTestCase(id="c1", inputs={"Z": "1"}, expected_outputs=[])]
        pairs = prepare_input_output_series(cases, "X", "Y")
        assert pairs == []

    def test_missing_output_gives_none_not_fabricated_value(self):
        cases = [ExperimentTestCase(id="c1", inputs={"X": "1"}, expected_outputs=[])]
        pairs = prepare_input_output_series(cases, "X", "Y")
        assert pairs == [("1", None)]

    def test_non_numeric_x_sorted_last_not_dropped(self):
        cases = [_case("c1", "abc", "1.0"), _case("c2", "1", "2.0")]
        pairs = prepare_input_output_series(cases, "X", "Y")
        assert pairs[0] == ("1", 2.0)
        assert pairs[1] == ("abc", 1.0)

    def test_categorical_parameter_values(self):
        """Categorical (non-numeric) parameter comparison -- values are
        preserved as labels, not coerced to numbers."""
        cases = [_case("c1", "adam", "0.9"), _case("c2", "sgd", "0.85")]
        pairs = prepare_input_output_series(cases, "X", "Y")
        assert {p[0] for p in pairs} == {"adam", "sgd"}


class TestDescribeTrend:
    @pytest.mark.parametrize(
        ("ys", "expected"),
        [
            ([1.0, 2.0, 3.0], "Y increases as X increases"),
            ([1.0, 1.0, 3.0], "Y does not decrease as X increases"),
            ([3.0, 2.0, 1.0], "Y decreases as X increases"),
            ([3.0, 3.0, 1.0], "Y does not increase as X increases"),
            ([2.0, 2.0, 2.0], "Y stays the same as X increases"),
            ([1.0, 3.0, 2.0], "Y both rises and falls as X increases"),
        ],
    )
    def test_every_direction_is_described(self, ys, expected):
        pairs = [(str(x), y) for x, y in enumerate(ys, start=1)]
        note = describe_trend(pairs, "X", "Y")
        assert note is not None
        assert note.startswith(expected)
        assert note.endswith("not a causal relationship.")

    @pytest.mark.parametrize(
        "pairs",
        [
            [("1", 1.0)],  # a single point has no direction
            [("32", 0.7), ("32", 0.8)],  # the input never changes
            [("adam", 0.9), ("sgd", 0.8)],  # categorical input has no order
            [("1", 1.0), ("2", None)],  # only one point has an output
        ],
    )
    def test_no_note_without_a_real_trend(self, pairs):
        assert describe_trend(pairs, "X", "Y") is None

    def test_points_missing_an_output_are_ignored(self):
        note = describe_trend([("1", 1.0), ("2", None), ("3", 2.0)], "X", "Y")
        assert note is not None
        assert note.startswith("Y increases as X increases")


class TestChooseSeriesKind:
    @pytest.mark.parametrize(
        ("pair_lists", "expected"),
        [
            ([[("1", 1.0), ("2", 2.0)]], "line"),
            ([[("adam", 0.9), ("sgd", 0.8)]], "bar"),
            ([[("32", 0.7), ("32", 0.8)]], "scatter"),  # repeated x within a series
            ([[("1", 1.0), ("2", 2.0)], [("1", 3.0), ("2", 4.0)]], "line"),  # repeats across series are fine
            ([[]], "scatter"),  # nothing to draw
        ],
    )
    def test_kind_follows_the_data(self, pair_lists, expected):
        assert choose_series_kind(pair_lists) == expected


class TestGroupTestCases:
    def test_groups_ordered_numerically_and_missing_values_kept(self):
        cases = [
            ExperimentTestCase(id="a", inputs={"X": "1", "B": "64"}, expected_outputs=[]),
            ExperimentTestCase(id="b", inputs={"X": "1", "B": "8"}, expected_outputs=[]),
            ExperimentTestCase(id="c", inputs={"X": "1"}, expected_outputs=[]),
        ]
        groups = group_test_cases(cases, "B")
        assert [value for value, _ in groups] == ["8", "64", NO_GROUP_VALUE]
        assert [c.id for c in groups[2][1]] == ["c"]


class TestVisualizationEndpointBehavior:
    @pytest.fixture
    def service(self, session) -> ExperimentPlanService:
        return ExperimentPlanService(session)

    @pytest.mark.asyncio
    async def test_visualization_reflects_imported_test_cases(self, service, project):
        import json

        created = await service.create_from_equation(
            project.owner_id,
            ExperimentPlanCreateFromEquation(
                project_id=project.id, title="viz test", expression="Y = a*X", known_inputs=["X"]
            ),
        )
        content = json.dumps([
            {"X": "1", "expected_Y": "2.0"},
            {"X": "2", "expected_Y": "4.0"},
            {"X": "3", "expected_Y": "6.0"},
        ]).encode()
        await service.import_test_cases(project.owner_id, created.id, content, "application/json")

        viz = await service.get_visualization_data(project.owner_id, created.id, "X", "Y")
        assert viz.chart_type == "line"
        assert viz.series[0].kind == "line"
        assert viz.x_label == "X"
        assert viz.y_label == "Y"
        assert len(viz.series) == 1
        assert viz.series[0].x == ["1", "2", "3"]
        assert viz.series[0].y == [2.0, 4.0, 6.0]

    @pytest.mark.asyncio
    async def test_trend_note_never_claims_causality(self, service, project):
        """Part H: 'Y increases with X' is fine; 'X causes Y to
        increase' is not -- the note must never use causal language."""
        import json

        created = await service.create_from_equation(
            project.owner_id,
            ExperimentPlanCreateFromEquation(
                project_id=project.id, title="causality check", expression="Y = a*X", known_inputs=["X"]
            ),
        )
        content = json.dumps([
            {"X": "1", "expected_Y": "1.0"},
            {"X": "2", "expected_Y": "2.0"},
        ]).encode()
        await service.import_test_cases(project.owner_id, created.id, content, "application/json")

        viz = await service.get_visualization_data(project.owner_id, created.id, "X", "Y")
        if viz.note:
            for forbidden in ("causes", "because", "results in", "leads to"):
                assert forbidden not in viz.note.lower()

    @pytest.mark.asyncio
    async def test_empty_test_cases_produce_empty_series_not_error(self, service, project):
        created = await service.create_from_equation(
            project.owner_id,
            ExperimentPlanCreateFromEquation(project_id=project.id, title="empty viz", expression="Y = a*X"),
        )
        viz = await service.get_visualization_data(project.owner_id, created.id, "X", "Y")
        assert viz.series[0].x == []
        assert viz.series[0].y == []

    @pytest.mark.asyncio
    async def test_group_by_draws_one_series_per_value(self, service, project):
        import json

        created = await service.create_from_equation(
            project.owner_id,
            ExperimentPlanCreateFromEquation(
                project_id=project.id, title="grouped viz", expression="Y = a*X", known_inputs=["X"]
            ),
        )
        content = json.dumps([
            {"X": "1", "B": "64", "expected_Y": "3.0"},
            {"X": "2", "B": "64", "expected_Y": "5.0"},
            {"X": "1", "B": "32", "expected_Y": "2.0"},
            {"X": "2", "B": "32", "expected_Y": "4.0"},
        ]).encode()
        await service.import_test_cases(project.owner_id, created.id, content, "application/json")

        viz = await service.get_visualization_data(project.owner_id, created.id, "X", "Y", group_by="B")
        assert viz.chart_type == "line"
        assert [s.name for s in viz.series] == ["B = 32", "B = 64"]
        assert viz.series[0].y == [2.0, 4.0]
        assert viz.note is None

    @pytest.mark.asyncio
    async def test_group_by_the_x_axis_input_is_rejected(self, service, project):
        from app.modules.research.experiment_service import ExperimentPlanValidationError

        created = await service.create_from_equation(
            project.owner_id,
            ExperimentPlanCreateFromEquation(project_id=project.id, title="bad group", expression="Y = a*X"),
        )
        with pytest.raises(ExperimentPlanValidationError):
            await service.get_visualization_data(project.owner_id, created.id, "X", "Y", group_by="X")
