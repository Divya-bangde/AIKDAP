"""Business logic for the Experiment Plan Engine (Sprint 16 Phase 6).

Persistence decision (Part T/Y.22): an `ExperimentPlan` is stored as an
`Asset` (`asset_type=DATASET`, `source=GENERATED`, tagged
`"experiment-plan"`) with the full structured plan serialized into
`asset_metadata["experiment_plan"]`. No new table, no migration.

Why this is enough, not a workaround:
- Every field an experiment plan needs to preserve (title, variables,
  constraints, test cases, variants, version history) is a document,
  not a row a query needs to filter/join on individually -- nothing
  here needs a SQL `WHERE variable.name = ...`, so JSONB loses nothing
  a dedicated table would have bought.
- Asset already carries exactly the ownership/project-scoping model an
  experiment plan needs (`project_id`, `owner_id`), already enforced by
  the same `_ensure_project_owned` pattern every other research
  endpoint uses -- reusing it means one authorization path, not two.
- Asset.version (an existing `int` column) already gives a plan a
  monotonic version counter for free; Part O explicitly asks for a
  "simple version model", not a second versioning system.
- Listing ("get every experiment plan in this project") is exactly
  `AssetRepository.search(project_id=..., asset_type=DATASET,
  tags=["experiment-plan"])`, which already exists.

If a future phase needs relational queries INSIDE a plan (e.g. "find
every experiment plan across all my projects using dataset X"), that is
a real argument for a dedicated table -- but nothing in this phase's
acceptance criteria requires it, so building one now would be exactly
the "unnecessary migration" Part T forbids.

A physical JSON export is written once, at creation, via the same
`StorageProvider` real uploads use, so a plan is downloadable/portable;
every subsequent read/update goes through `asset_metadata` (fast,
transactional, no re-upload on every edit).
"""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.planner.experiment import (
    EquationParseError,
    SweepTooLargeError,
    analyze_mutability,
    build_variables_from_equation,
    check_all_constraints,
    choose_series_kind,
    classify_variable_role,
    describe_trend,
    group_test_cases,
    now_utc,
    parse_equation,
    parse_test_cases,
    plan_parameter_sweep,
    prepare_input_output_series,
)
from app.modules.assets.enums import AssetProcessingStatus, AssetSource, AssetStatus, AssetType
from app.modules.assets.models import Asset
from app.modules.assets.repository import AssetRepository
from app.modules.assets.storage import get_storage_provider
from app.modules.execution.models import ExecutionJob
from app.modules.execution.repository import ExecutionJobRepository
from app.modules.execution.schemas import ExecutionJobCreate
from app.modules.projects.repository import ProjectRepository
from app.modules.research.experiment_schemas import (
    ExperimentGoal,
    ExperimentOutput,
    ExperimentPlan,
    ExperimentPlanChange,
    ExperimentPlanCreateFromEquation,
    ExperimentPlanCreateFromUnderstanding,
    ExperimentPlanUpdateRequest,
    ExperimentSweepRequest,
    ExperimentVariable,
    ExperimentVariant,
    ExperimentVariantCreateRequest,
    MutabilityStatus,
    RunPlanStatus,
    TestCaseImportResponse,
    VariableRole,
    VisualizationData,
    VisualizationSeries,
)
from app.modules.research.schemas import ResearchVariable, SourceReference
from execution_launcher.models import ExecutionCapability, ExecutionOperation, ResourceClass

_EXPERIMENT_PLAN_TAG = "experiment-plan"
_METADATA_KEY = "experiment_plan"


class ExperimentPlanNotFoundError(Exception):
    """Raised when a plan does not exist or is not owned by the caller."""


class ExperimentPlanAccessDeniedError(Exception):
    """Raised when the caller does not own the project a plan belongs to."""


class ExperimentPlanValidationError(ValueError):
    """Raised for a deterministic validation failure (constraint
    violation, fixed-variable mutation without override, malformed
    equation, oversized sweep) -- always surfaced to the caller as a
    400, never silently corrected (Part Y)."""


class ExperimentPlanService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._assets = AssetRepository(session)
        self._projects = ProjectRepository(session)
        self._storage = get_storage_provider()

    async def _ensure_project_owned(self, owner_id: uuid.UUID, project_id: uuid.UUID) -> None:
        project = await self._projects.get_by_id(project_id)
        if project is None or project.owner_id != owner_id:
            raise ExperimentPlanAccessDeniedError(project_id)

    async def _get_owned_asset(self, owner_id: uuid.UUID, plan_id: uuid.UUID) -> Asset:
        asset = await self._assets.get_by_id(plan_id)
        if (
            asset is None
            or asset.owner_id != owner_id
            or _EXPERIMENT_PLAN_TAG not in asset.tags
        ):
            raise ExperimentPlanNotFoundError(plan_id)
        return asset

    def _plan_from_asset(self, asset: Asset) -> ExperimentPlan:
        return ExperimentPlan.model_validate(asset.asset_metadata[_METADATA_KEY])

    async def _persist_new(self, owner_id: uuid.UUID, plan: ExperimentPlan) -> Asset:
        content = plan.model_dump_json(indent=2).encode("utf-8")
        storage_path = await self._storage.save(
            project_id=plan.project_id, filename=f"{plan.title}.experiment.json", content=content
        )
        asset = Asset(
            # Explicit, matching `plan.id` -- the ExperimentPlan and the
            # Asset row it lives in must share one id, or a later
            # get_plan(plan.id) would look up the wrong primary key.
            id=plan.id,
            project_id=plan.project_id,
            owner_id=owner_id,
            title=plan.title,
            description=plan.objective,
            asset_type=AssetType.DATASET,
            status=AssetStatus.ACTIVE,
            source=AssetSource.GENERATED,
            mime_type="application/json",
            file_name=f"{plan.title}.experiment.json",
            file_extension="json",
            file_size=len(content),
            storage_path=storage_path,
            checksum=hashlib.sha256(content).hexdigest(),
            version=plan.version,
            tags=[_EXPERIMENT_PLAN_TAG],
            processing_status=AssetProcessingStatus.COMPLETED,
            processing_completed_at=now_utc(),
            asset_metadata={_METADATA_KEY: plan.model_dump(mode="json")},
        )
        await self._assets.create(asset)
        await self._session.commit()
        return asset

    async def _persist_update(self, asset: Asset, plan: ExperimentPlan) -> None:
        asset.asset_metadata = {**asset.asset_metadata, _METADATA_KEY: plan.model_dump(mode="json")}
        asset.version = plan.version
        asset.title = plan.title
        asset.description = plan.objective
        await self._session.commit()

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------

    async def create_from_equation(
        self, owner_id: uuid.UUID, data: ExperimentPlanCreateFromEquation
    ) -> ExperimentPlan:
        await self._ensure_project_owned(owner_id, data.project_id)

        source_reference = None
        research_variables: dict[str, ResearchVariable] = {}
        source_asset_ids: list[uuid.UUID] = []
        if data.source_asset_id is not None:
            source_asset = await self._assets.get_by_id(data.source_asset_id)
            if source_asset is None or source_asset.owner_id != owner_id:
                raise ExperimentPlanValidationError("source_asset_id is not a valid owned asset.")
            source_asset_ids = [data.source_asset_id]
            understanding = (source_asset.asset_metadata or {}).get("research_understanding")
            if understanding:
                for v in understanding.get("variables", []):
                    research_variables[v["name"]] = ResearchVariable.model_validate(v)
            source_reference = SourceReference(
                paper_id=data.source_asset_id, paper_title=source_asset.title
            )

        try:
            parsed = parse_equation(data.expression, source_reference=source_reference)
        except EquationParseError as exc:
            raise ExperimentPlanValidationError(str(exc)) from exc

        variables, output, inputs = build_variables_from_equation(
            parsed,
            research_variables=research_variables,
            known_inputs=data.known_inputs,
            source_reference=source_reference,
        )

        # Sprint 16 Phase 7B.24: execution inputs are a SEPARATE, explicitly
        # declared list -- never derived from source_asset_id/source_asset_ids.
        # Each must be an asset the caller owns, in this same project; a plan
        # is not created at all if any declared execution input fails this
        # check (fail closed, not "create without this one").
        execution_input_asset_ids: list[uuid.UUID] = []
        for asset_id in data.execution_input_asset_ids:
            input_asset = await self._assets.get_by_id(asset_id)
            if (
                input_asset is None
                or input_asset.owner_id != owner_id
                or input_asset.project_id != data.project_id
            ):
                raise ExperimentPlanValidationError(
                    f"execution_input_asset_ids: '{asset_id}' is not a valid asset "
                    "owned by the caller in this plan's project."
                )
            execution_input_asset_ids.append(asset_id)

        timestamp = now_utc()
        plan = ExperimentPlan(
            id=uuid.uuid4(),
            project_id=data.project_id,
            title=data.title,
            objective=f"Understand and plan experiments around: {parsed.raw_expression}",
            goal=data.goal,
            hypothesis=None,
            source_expression=parsed.raw_expression,
            source_asset_ids=source_asset_ids,
            execution_input_asset_ids=execution_input_asset_ids,
            variables=variables,
            inputs=inputs,
            outputs=[output] if output else [],
            constraints=parsed.constraints,
            test_cases=[],
            variants=[
                _baseline_variant(),
            ],
            version=1,
            history=[],
            created_at=timestamp,
            updated_at=timestamp,
        )
        await self._persist_new(owner_id, plan)
        return plan

    async def create_from_understanding(
        self, owner_id: uuid.UUID, data: ExperimentPlanCreateFromUnderstanding
    ) -> ExperimentPlan:
        await self._ensure_project_owned(owner_id, data.project_id)

        asset = await self._assets.get_by_id(data.source_asset_id)
        if asset is None or asset.owner_id != owner_id:
            raise ExperimentPlanValidationError("source_asset_id is not a valid owned asset.")
        understanding = (asset.asset_metadata or {}).get("research_understanding")
        if not understanding:
            raise ExperimentPlanValidationError(
                "This asset has no stored research understanding yet -- "
                "run /research/documents/{asset_id}/analyze first."
            )

        source_reference = SourceReference(paper_id=data.source_asset_id, paper_title=asset.title)
        variables = [
            _variable_from_research_variable(ResearchVariable.model_validate(raw_var), source_reference)
            for raw_var in understanding.get("variables", [])
        ]

        goal = data.goal

        missing_for_reproduction: list[str] = []
        if goal == ExperimentGoal.REPRODUCTION:
            for gap in understanding.get("missing_information", []) or []:
                if gap.get("classification") == "required":
                    missing_for_reproduction.append(gap.get("description", "unspecified gap"))

        objective = understanding.get("problem_statement") or understanding.get("title", data.title)
        if missing_for_reproduction:
            objective += (
                "\n\nNOTE: the following details required for reproduction are missing from the "
                "source paper and have NOT been guessed: " + "; ".join(missing_for_reproduction)
            )

        timestamp = now_utc()
        plan = ExperimentPlan(
            id=uuid.uuid4(),
            project_id=data.project_id,
            title=data.title,
            objective=objective,
            goal=goal,
            hypothesis=None,
            source_asset_ids=[data.source_asset_id],
            variables=variables,
            inputs=[],
            outputs=[
                _output_from_metric(m, source_reference)
                for m in understanding.get("evaluation_metrics", []) or []
            ],
            constraints=[],
            test_cases=[],
            variants=[_baseline_variant()],
            version=1,
            history=[],
            created_at=timestamp,
            updated_at=timestamp,
        )
        await self._persist_new(owner_id, plan)
        return plan

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_plan(self, owner_id: uuid.UUID, plan_id: uuid.UUID) -> ExperimentPlan:
        asset = await self._get_owned_asset(owner_id, plan_id)
        return self._plan_from_asset(asset)

    async def list_plans(
        self, owner_id: uuid.UUID, project_id: uuid.UUID
    ) -> list[ExperimentPlan]:
        await self._ensure_project_owned(owner_id, project_id)
        assets = await self._assets.search(
            owner_id, project_id=project_id, asset_type=AssetType.DATASET, tags=[_EXPERIMENT_PLAN_TAG]
        )
        return [self._plan_from_asset(a) for a in assets]

    # ------------------------------------------------------------------
    # Update / versioning (Part O, Part Y.4)
    # ------------------------------------------------------------------

    async def update_plan(
        self, owner_id: uuid.UUID, plan_id: uuid.UUID, data: ExperimentPlanUpdateRequest
    ) -> ExperimentPlan:
        asset = await self._get_owned_asset(owner_id, plan_id)
        plan = self._plan_from_asset(asset)

        changes: list[ExperimentPlanChange] = []
        next_version = plan.version + 1
        timestamp = now_utc()

        if data.title is not None and data.title != plan.title:
            changes.append(_change(next_version, timestamp, "title", plan.title, data.title, data.reason))
            plan.title = data.title
        if data.objective is not None and data.objective != plan.objective:
            changes.append(
                _change(next_version, timestamp, "objective", plan.objective, data.objective, data.reason)
            )
            plan.objective = data.objective

        if data.variables is not None:
            old_by_name = {v.name: v for v in plan.variables}
            for new_var in data.variables:
                old_var = old_by_name.get(new_var.name)
                if (
                    old_var is not None
                    and old_var.mutable == MutabilityStatus.FIXED
                    and old_var.current_value != new_var.current_value
                    and not data.allow_fixed_variable_change
                ):
                    raise ExperimentPlanValidationError(
                        f"'{new_var.name}' is marked FIXED ({old_var.mutability_reason}). "
                        "Set allow_fixed_variable_change=true to change it explicitly."
                    )
                if old_var is None or old_var.current_value != new_var.current_value:
                    changes.append(
                        _change(
                            next_version,
                            timestamp,
                            f"variable:{new_var.name}",
                            old_var.current_value if old_var else None,
                            new_var.current_value,
                            data.reason,
                        )
                    )
            plan.variables = data.variables

        if data.inputs is not None:
            plan.inputs = data.inputs
        if data.outputs is not None:
            plan.outputs = data.outputs
        if data.constraints is not None:
            plan.constraints = data.constraints

        violations = check_all_constraints(plan.variables, plan.constraints)
        if violations:
            raise ExperimentPlanValidationError("; ".join(violations))

        if changes:
            plan.version = next_version
            plan.history = [*plan.history, *changes]
        plan.updated_at = timestamp

        await self._persist_update(asset, plan)
        return plan

    # ------------------------------------------------------------------
    # Variants / sweeps (Part I/J/K)
    # ------------------------------------------------------------------

    async def create_variant(
        self, owner_id: uuid.UUID, plan_id: uuid.UUID, data: ExperimentVariantCreateRequest
    ) -> ExperimentPlan:
        asset = await self._get_owned_asset(owner_id, plan_id)
        plan = self._plan_from_asset(asset)

        known_vars = {v.name for v in plan.variables}
        for name in data.overrides:
            if name not in known_vars:
                raise ExperimentPlanValidationError(
                    f"'{name}' is not a variable on this plan; add it before overriding it."
                )
            variable = next(v for v in plan.variables if v.name == name)
            if variable.mutable == MutabilityStatus.FIXED:
                raise ExperimentPlanValidationError(
                    f"'{name}' is marked FIXED and cannot be overridden by a variant "
                    f"({variable.mutability_reason})."
                )

        variant_id = f"variant-{uuid.uuid4().hex[:8]}"
        plan.variants = [
            *plan.variants,
            _variant_from_request(variant_id, data),
        ]
        plan.updated_at = now_utc()
        await self._persist_update(asset, plan)
        return plan

    async def plan_sweep(
        self, owner_id: uuid.UUID, plan_id: uuid.UUID, data: ExperimentSweepRequest
    ) -> ExperimentPlan:
        asset = await self._get_owned_asset(owner_id, plan_id)
        plan = self._plan_from_asset(asset)

        known_vars = {v.name for v in plan.variables}
        for name in data.parameter_values:
            if name not in known_vars:
                raise ExperimentPlanValidationError(
                    f"'{name}' is not a variable on this plan; add it before sweeping it."
                )
            variable = next(v for v in plan.variables if v.name == name)
            if variable.mutable == MutabilityStatus.FIXED:
                raise ExperimentPlanValidationError(
                    f"'{name}' is marked FIXED and cannot be swept "
                    f"({variable.mutability_reason})."
                )

        try:
            new_variants = plan_parameter_sweep(
                data.parameter_values, existing_variant_ids={v.id for v in plan.variants}
            )
        except SweepTooLargeError as exc:
            raise ExperimentPlanValidationError(str(exc)) from exc

        plan.variants = [*plan.variants, *new_variants]
        plan.updated_at = now_utc()
        await self._persist_update(asset, plan)
        return plan

    # ------------------------------------------------------------------
    # Test cases (Part G)
    # ------------------------------------------------------------------

    async def import_test_cases(
        self, owner_id: uuid.UUID, plan_id: uuid.UUID, content: bytes, mime_type: str
    ) -> TestCaseImportResponse:
        asset = await self._get_owned_asset(owner_id, plan_id)
        plan = self._plan_from_asset(asset)

        try:
            imported, rejected = parse_test_cases(content, mime_type)
        except ValueError as exc:
            raise ExperimentPlanValidationError(str(exc)) from exc

        # Append, never overwrite (Part Y.5) -- existing case ids are
        # kept unique by prefixing the import batch.
        existing_ids = {c.id for c in plan.test_cases}
        batch_prefix = f"import{len(plan.test_cases)}-"
        for case in imported:
            if case.id in existing_ids:
                case.id = batch_prefix + case.id
        plan.test_cases = [*plan.test_cases, *imported]
        plan.updated_at = now_utc()
        await self._persist_update(asset, plan)
        return TestCaseImportResponse(imported=imported, rejected=rejected)

    # ------------------------------------------------------------------
    # Visualization (Part H/R)
    # ------------------------------------------------------------------

    async def get_visualization_data(
        self,
        owner_id: uuid.UUID,
        plan_id: uuid.UUID,
        input_name: str,
        output_name: str,
        group_by: str | None = None,
    ) -> VisualizationData:
        """One series of `output_name` against `input_name`, or one per
        value of the `group_by` input. The chart type follows the data
        (see `choose_series_kind`)."""
        if group_by == input_name:
            raise ExperimentPlanValidationError("Compare by must be a different input than the x-axis.")
        asset = await self._get_owned_asset(owner_id, plan_id)
        plan = self._plan_from_asset(asset)

        if group_by:
            named_pairs = [
                (f"{group_by} = {value}", prepare_input_output_series(cases, input_name, output_name))
                for value, cases in group_test_cases(plan.test_cases, group_by)
            ]
            # A group whose cases lack the x-axis input has nothing to plot.
            named_pairs = [(name, pairs) for name, pairs in named_pairs if pairs]
        else:
            named_pairs = [
                (
                    f"{output_name} vs {input_name}",
                    prepare_input_output_series(plan.test_cases, input_name, output_name),
                )
            ]

        kind = choose_series_kind([pairs for _, pairs in named_pairs])
        series = [
            VisualizationSeries(name=name, x=[p[0] for p in pairs], y=[p[1] for p in pairs], kind=kind)
            for name, pairs in named_pairs
        ]
        # One trend note per chart: with several series it would have to
        # describe each group separately, which the legend already shows.
        note = describe_trend(named_pairs[0][1], input_name, output_name) if len(named_pairs) == 1 else None
        return VisualizationData(
            chart_type=kind,
            x_label=input_name,
            y_label=output_name,
            series=series,
            note=note,
        )

    # ------------------------------------------------------------------
    # Execution front door (Sprint 16 Phase 7B.23)
    # ------------------------------------------------------------------

    async def request_execution(
        self, owner_id: uuid.UUID, plan_id: uuid.UUID, variant_id: str
    ) -> ExecutionJob:
        """Maps ONE variant of a persisted plan onto a new
        `ExecutionJob(PENDING)`, commits it, and enqueues the launch task
        -- the execution module's missing front door. Rejects, creating
        nothing, without touching the plan, if:

          - the plan does not resolve to a caller-owned resource
            (`ExperimentPlanNotFoundError` -- "not yours" and "does not
            exist" are indistinguishable, matching every other plan route)
          - `variant_id` does not name a variant on this plan
          - that variant's `status` is not `PLANNED` (a variant that has
            already been executed, failed, or was cancelled is not
            re-run through this call)
          - the plan carries no `source_expression` -- today the ONLY
            operation this mapping can derive is `EVALUATE_EXPRESSION`,
            for plans created via `create_from_equation`. Nothing else on
            `ExperimentPlan` identifies which of the other six
            catalog operations (matmul/percentile/mean/std/corrcoef/
            groupby_agg/pivot_table) a plan intends -- guessing one would
            violate "reject rather than default", so any plan without a
            derivable operation is rejected outright rather than mapped
            to an arbitrary guess.

        `input_asset_ids` comes ONLY from `plan.execution_input_asset_ids`
        (Sprint 16 Phase 7B.24) -- NEVER from `plan.source_asset_ids`.
        The two are deliberately distinct: `source_asset_ids` is
        research/citation context (the paper(s) this plan cites), never
        an execution input; `execution_input_asset_ids` is explicitly
        declared by the caller at plan-creation time (currently only
        `create_from_equation` accepts it) and already validated there
        (owned by the caller, same project) -- so this function trusts
        it as-is rather than re-validating. A plan with no declared
        execution inputs produces `input_asset_ids=[]`, never an
        implicit guess.

        Parameters passed to the job come from `plan.variables` whose
        `role` is `VARIABLE`/`PARAMETER`/`CONSTANT` (an `INPUT`/`OUTPUT`/
        `DERIVED_QUANTITY`/`UNKNOWN` variable is not something the
        dispatcher sets, so it is excluded) -- each variable's
        `current_value`, overridden by the chosen variant's own
        `overrides` when present, exactly like `create_variant`'s own
        override semantics.
        """
        asset = await self._get_owned_asset(owner_id, plan_id)
        plan = self._plan_from_asset(asset)

        variant = next((v for v in plan.variants if v.id == variant_id), None)
        if variant is None:
            raise ExperimentPlanValidationError(f"'{variant_id}' is not a variant on this plan.")
        if variant.status != RunPlanStatus.PLANNED:
            raise ExperimentPlanValidationError(
                f"variant '{variant_id}' is not eligible for execution "
                f"(status={variant.status.value}); only PLANNED variants can be executed."
            )
        if not plan.source_expression:
            raise ExperimentPlanValidationError(
                "This experiment plan has no derivable execution operation. Only plans "
                "created from an equation (POST /experiments/from-equation) can be "
                "executed today."
            )

        parameters: dict[str, str] = {}
        for variable in plan.variables:
            if variable.role not in (VariableRole.VARIABLE, VariableRole.PARAMETER, VariableRole.CONSTANT):
                continue
            value = variant.overrides.get(variable.name, variable.current_value)
            if value is not None:
                parameters[variable.name] = value
        parameters["expression"] = plan.source_expression

        create_payload = ExecutionJobCreate(
            project_id=plan.project_id,
            owner_id=owner_id,
            experiment_plan_id=plan.id,
            experiment_plan_version=plan.version,
            capability=ExecutionCapability.EVALUATE_EXPRESSION,
            operation=ExecutionOperation.EVALUATE_EXPRESSION,
            resource_class=ResourceClass.CLASS_EXPR,
            parameters=parameters,
            input_asset_ids=plan.execution_input_asset_ids,
        )
        job_data = create_payload.model_dump()
        job_data["input_asset_ids"] = [str(asset_id) for asset_id in create_payload.input_asset_ids]
        job = ExecutionJob(**job_data)

        created = await ExecutionJobRepository(self._session).create(job)
        await self._session.commit()

        # Local import: the launch task lives in `app.workers.tasks`,
        # which this module must not import at module scope (matches
        # `reconciliation.reconcile_stale_launching_execution_jobs`'s
        # established local-import precedent for the same reason).
        from app.workers.tasks import launch_execution_job

        launch_execution_job.delay(str(created.id))

        return created


def _variant_from_request(
    variant_id: str, data: ExperimentVariantCreateRequest
) -> ExperimentVariant:
    return ExperimentVariant(
        id=variant_id,
        name=data.name,
        is_baseline=False,
        overrides=data.overrides,
        status=RunPlanStatus.PLANNED,
        notes=data.notes,
    )


def _baseline_variant() -> ExperimentVariant:
    return ExperimentVariant(
        id="baseline", name="Baseline", is_baseline=True, overrides={}, status=RunPlanStatus.PLANNED
    )


def _change(version, timestamp, field, old, new, reason) -> ExperimentPlanChange:
    return ExperimentPlanChange(
        version=version,
        changed_at=timestamp,
        changed_field=field,
        old_value=old,
        new_value=new,
        reason=reason,
    )


def _variable_from_research_variable(
    research_var: ResearchVariable, source_reference: SourceReference | None
) -> ExperimentVariable:
    role = classify_variable_role(research_var)
    mutable, reason, evidence = analyze_mutability(
        name=research_var.name, research_understanding_role=role
    )
    return ExperimentVariable(
        name=research_var.name,
        role=role,
        mutable=mutable,
        mutability_reason=reason,
        mutability_evidence=evidence,
        unit=research_var.unit,
        certainty=research_var.confidence,
        source_reference=source_reference,
    )


def _output_from_metric(
    metric_name: str, source_reference: SourceReference | None
) -> ExperimentOutput:
    return ExperimentOutput(
        name=metric_name, type="number", description=None, source_reference=source_reference
    )
