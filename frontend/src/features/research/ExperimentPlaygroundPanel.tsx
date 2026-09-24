import { lazy, Suspense, useRef, useState } from "react";
import { Beaker, FlaskConical, Loader2, LineChart, Plus, Upload } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import {
  createExperimentVariant,
  getExperimentPlan,
  getExperimentVisualization,
  importExperimentTestCases,
  updateExperimentPlan,
  type ExperimentPlan,
  type VisualizationData,
} from "@/services/experiments";

// Plotly (even the "-dist-min" build) is several MB -- loaded only
// when the visualization dialog actually opens, not on every page
// that happens to render this panel. Without this, the whole chart
// library ends up in ProjectDetail's main chunk (measured: it grew
// the chunk from ~35KB to ~4.3MB before this was split out).
const ExperimentVisualizationChart = lazy(() =>
  import("@/features/research/ExperimentVisualizationChart").then((m) => ({
    default: m.ExperimentVisualizationChart,
  }))
);

const MUTABILITY_BADGE_VARIANT: Record<string, "default" | "outline" | "destructive" | "secondary"> = {
  mutable: "default",
  fixed: "secondary",
  unknown: "outline",
};

const RUN_STATUS_BADGE_VARIANT: Record<string, "default" | "outline" | "destructive" | "secondary"> = {
  planned: "outline",
  executed: "default",
  failed: "destructive",
  cancelled: "secondary",
};

/** The generated OpenAPI type marks every `default_factory=list` field
 * optional (nothing in a JSON Schema distinguishes "always an array,
 * possibly empty" from "may be absent") -- the backend always sends
 * them, so this just gives the component real, non-optional arrays to
 * work with instead of `?? []` scattered through every render. */
type NormalizedPlan = ExperimentPlan & {
  variables: NonNullable<ExperimentPlan["variables"]>;
  inputs: NonNullable<ExperimentPlan["inputs"]>;
  outputs: NonNullable<ExperimentPlan["outputs"]>;
  constraints: NonNullable<ExperimentPlan["constraints"]>;
  test_cases: NonNullable<ExperimentPlan["test_cases"]>;
  variants: NonNullable<ExperimentPlan["variants"]>;
};

function normalizePlan(plan: ExperimentPlan): NormalizedPlan {
  return {
    ...plan,
    variables: plan.variables ?? [],
    inputs: plan.inputs ?? [],
    outputs: plan.outputs ?? [],
    constraints: plan.constraints ?? [],
    test_cases: plan.test_cases ?? [],
    variants: plan.variants ?? [],
  };
}

/**
 * The Experiment Playground (Sprint 16 Phase 6, Part Q). Every action
 * here produces or edits a PLAN -- nothing executes. "Planned runs"
 * always shows `status: planned` for anything created here; only a
 * future execution phase would ever change that.
 */
export function ExperimentPlaygroundPanel({ plan: initialPlan }: { plan: ExperimentPlan }) {
  const [plan, setPlan] = useState<NormalizedPlan>(() => normalizePlan(initialPlan));
  const [pendingValues, setPendingValues] = useState<Record<string, string>>({});
  const [savingVariable, setSavingVariable] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [variantName, setVariantName] = useState("");
  const [variantOverrides, setVariantOverrides] = useState<Record<string, string>>({});
  const [addingVariant, setAddingVariant] = useState(false);

  const [importing, setImporting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [vizOpen, setVizOpen] = useState(false);
  const [vizInput, setVizInput] = useState(plan.inputs[0]?.name ?? plan.variables[0]?.name ?? "");
  const [vizOutput, setVizOutput] = useState(plan.outputs[0]?.name ?? "");
  const [vizGroupBy, setVizGroupBy] = useState("");
  const [vizData, setVizData] = useState<VisualizationData | null>(null);
  const [vizLoading, setVizLoading] = useState(false);

  const fixedVariables = plan.variables.filter((v) => v.mutable === "fixed");
  const otherVariables = plan.variables.filter((v) => v.mutable !== "fixed");
  const baseline = plan.variants.find((v) => v.is_baseline);
  const otherVariants = plan.variants.filter((v) => !v.is_baseline);

  async function handleVariableSave(name: string) {
    const newValue = pendingValues[name];
    if (newValue === undefined) return;
    setSavingVariable(name);
    setError(null);
    try {
      const target = plan.variables.find((v) => v.name === name);
      if (!target) return;
      const updatedVariable = { ...target, current_value: newValue };
      const updated = await updateExperimentPlan(plan.id, {
        variables: [updatedVariable],
        reason: "Edited in the Experiment Playground",
        // The variable being edited here is never one of the FIXED
        // ones (its input is disabled in that case, see below), so
        // this never needs to be true from this form.
        allow_fixed_variable_change: false,
      });
      setPlan(normalizePlan(updated));
      setPendingValues((prev) => {
        const next = { ...prev };
        delete next[name];
        return next;
      });
    } catch (err: any) {
      setError(err.message || `Could not update ${name}.`);
    } finally {
      setSavingVariable(null);
    }
  }

  async function handleAddVariant() {
    if (!variantName.trim()) return;
    setAddingVariant(true);
    setError(null);
    try {
      const overrides = Object.fromEntries(
        Object.entries(variantOverrides).filter(([, v]) => v.trim() !== "")
      );
      const updated = await createExperimentVariant(plan.id, {
        name: variantName,
        overrides,
      });
      setPlan(normalizePlan(updated));
      setVariantName("");
      setVariantOverrides({});
    } catch (err: any) {
      setError(err.message || "Could not add variant.");
    } finally {
      setAddingVariant(false);
    }
  }

  async function handleImportFile(file: File) {
    setImporting(true);
    setError(null);
    try {
      await importExperimentTestCases(plan.id, file);
      // The import endpoint returns only the import result (imported +
      // rejected rows), not the updated plan -- re-fetch to pick up the
      // newly appended test cases rather than reconstructing them
      // client-side from a response that doesn't carry their final ids.
      const refreshed = await getExperimentPlan(plan.id);
      setPlan(normalizePlan(refreshed));
    } catch (err: any) {
      setError(err.message || "Could not import test cases.");
    } finally {
      setImporting(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  async function handleOpenVisualization() {
    setVizOpen(true);
    if (!vizInput || !vizOutput) return;
    setVizLoading(true);
    try {
      // Grouping by the x-axis input itself is meaningless (one point per series).
      const groupBy = vizGroupBy && vizGroupBy !== vizInput ? vizGroupBy : undefined;
      const data = await getExperimentVisualization(plan.id, vizInput, vizOutput, groupBy);
      setVizData(data);
    } catch (err: any) {
      setError(err.message || "Could not load visualization.");
    } finally {
      setVizLoading(false);
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <Card>
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <div className="flex items-center gap-2">
            <FlaskConical className="h-4 w-4 text-primary" />
            <CardTitle>Experiment Playground</CardTitle>
          </div>
          <Badge variant="outline">v{plan.version}</Badge>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div>
            <p className="text-sm font-semibold">{plan.title}</p>
            <p className="mt-1 text-sm text-muted-foreground">{plan.objective}</p>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs uppercase tracking-wide text-muted-foreground">Goal</span>
            <Badge variant="outline" className="capitalize">
              {plan.goal}
            </Badge>
          </div>
          {plan.hypothesis && (
            <div className="rounded-md bg-sunken p-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Hypothesis
                </span>
                <Badge variant={plan.hypothesis.origin === "ai_generated_unvalidated" ? "destructive" : "outline"}>
                  {plan.hypothesis.origin === "ai_generated_unvalidated"
                    ? "AI-generated, unvalidated"
                    : plan.hypothesis.origin === "user_created"
                      ? "User-created"
                      : "Stored hypothesis"}
                </Badge>
              </div>
              <p className="mt-1 text-sm">{plan.hypothesis.description}</p>
            </div>
          )}
        </CardContent>
      </Card>

      {error && (
        <div className="rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive-foreground">
          {error}
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Fixed Variables</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          {fixedVariables.length === 0 && (
            <p className="text-sm text-muted-foreground">No variables are marked fixed yet.</p>
          )}
          {fixedVariables.map((v) => (
            <div key={v.name} className="flex items-start justify-between rounded-md border p-2 text-sm">
              <div>
                <span className="font-medium">{v.name}</span>
                <p className="mt-0.5 text-xs text-muted-foreground">{v.mutability_reason}</p>
              </div>
              <span className="font-mono text-sm">{v.current_value ?? "—"}</span>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Mutable / Unknown Variables</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {otherVariables.length === 0 && (
            <p className="text-sm text-muted-foreground">No mutable or unknown variables.</p>
          )}
          {otherVariables.map((v) => (
            <div key={v.name} className="flex flex-col gap-1 rounded-md border p-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-sm">{v.name}</span>
                  <Badge variant={MUTABILITY_BADGE_VARIANT[v.mutable] ?? "outline"} className="capitalize">
                    {v.mutable}
                  </Badge>
                  <span className="text-xs text-muted-foreground capitalize">{v.role}</span>
                </div>
              </div>
              <p className="text-xs text-muted-foreground">{v.mutability_reason}</p>
              <div className="mt-1 flex items-center gap-2">
                <Input
                  className="h-8 max-w-[160px] font-mono text-sm"
                  value={pendingValues[v.name] ?? v.current_value ?? ""}
                  disabled={v.mutable === "fixed" || savingVariable === v.name}
                  onChange={(e) =>
                    setPendingValues((prev) => ({ ...prev, [v.name]: e.target.value }))
                  }
                />
                <Button
                  size="sm"
                  variant="outline"
                  disabled={
                    v.mutable === "fixed" ||
                    pendingValues[v.name] === undefined ||
                    savingVariable === v.name
                  }
                  onClick={() => handleVariableSave(v.name)}
                >
                  {savingVariable === v.name && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
                  Save
                </Button>
              </div>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Constraints</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          {plan.constraints.length === 0 && (
            <p className="text-sm text-muted-foreground">No constraints defined.</p>
          )}
          {plan.constraints.map((c, i) => (
            <div key={i} className="flex items-center justify-between rounded-md border p-2 text-sm">
              <span className="font-mono">{c.expression}</span>
              <Badge variant="outline" className="capitalize">
                {c.source.replace(/_/g, " ")}
              </Badge>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <CardTitle className="text-sm">Test Cases ({plan.test_cases.length})</CardTitle>
          <div>
            <input
              ref={fileInputRef}
              type="file"
              accept=".json,.csv,.xlsx"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void handleImportFile(file);
              }}
            />
            <Button
              size="sm"
              variant="outline"
              disabled={importing}
              onClick={() => fileInputRef.current?.click()}
            >
              {importing ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <Upload className="mr-1 h-3 w-3" />}
              Import
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {plan.test_cases.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Import JSON, CSV, or XLSX test cases to see them here.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b text-xs uppercase text-muted-foreground">
                    <th className="pb-1 pr-4">Case</th>
                    <th className="pb-1 pr-4">Inputs</th>
                    <th className="pb-1">Expected</th>
                  </tr>
                </thead>
                <tbody>
                  {plan.test_cases.slice(0, 20).map((tc) => (
                    <tr key={tc.id} className="border-b last:border-0">
                      <td className="py-1 pr-4 font-mono text-xs">{tc.id}</td>
                      <td className="py-1 pr-4 font-mono text-xs">
                        {Object.entries(tc.inputs)
                          .map(([k, v]) => `${k}=${v}`)
                          .join(", ")}
                      </td>
                      <td className="py-1 font-mono text-xs">
                        {(tc.expected_outputs ?? [])
                          .map((o) => `${o.output_name}=${o.value}`)
                          .join(", ") || "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {plan.test_cases.length > 20 && (
                <p className="mt-2 text-xs text-muted-foreground">
                  Showing 20 of {plan.test_cases.length} test cases.
                </p>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Baseline</CardTitle>
        </CardHeader>
        <CardContent>
          {baseline ? (
            <div className="rounded-md border p-2 text-sm">
              <span className="font-medium">{baseline.name}</span>
              <span className="ml-2 text-xs text-muted-foreground">
                (uses each variable's current value)
              </span>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No baseline defined yet.</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Variants</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {otherVariants.length === 0 && (
            <p className="text-sm text-muted-foreground">No variants planned yet.</p>
          )}
          {otherVariants.map((v) => (
            <div key={v.id} className="rounded-md border p-2 text-sm">
              <div className="flex items-center justify-between">
                <span className="font-medium">{v.name}</span>
                <Badge variant={RUN_STATUS_BADGE_VARIANT[v.status] ?? "outline"} className="capitalize">
                  {v.status}
                </Badge>
              </div>
              {Object.keys(v.overrides ?? {}).length > 0 && (
                <p className="mt-1 font-mono text-xs text-muted-foreground">
                  {Object.entries(v.overrides ?? {})
                    .map(([k, val]) => `${k}=${val}`)
                    .join(", ")}
                </p>
              )}
            </div>
          ))}

          <Separator />

          <div className="flex flex-col gap-2">
            <Label htmlFor="variant-name" className="text-xs">
              New variant
            </Label>
            <Input
              id="variant-name"
              placeholder="Variant name"
              value={variantName}
              onChange={(e) => setVariantName(e.target.value)}
            />
            {otherVariables.filter((v) => v.mutable === "mutable").map((v) => (
              <Input
                key={v.name}
                placeholder={`${v.name} override (optional)`}
                className="font-mono"
                value={variantOverrides[v.name] ?? ""}
                onChange={(e) =>
                  setVariantOverrides((prev) => ({ ...prev, [v.name]: e.target.value }))
                }
              />
            ))}
            <Button
              size="sm"
              disabled={!variantName.trim() || addingVariant}
              onClick={handleAddVariant}
            >
              {addingVariant ? (
                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
              ) : (
                <Plus className="mr-1 h-3 w-3" />
              )}
              Add Variant
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Planned Runs</CardTitle>
        </CardHeader>
        <CardContent>
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b text-xs uppercase text-muted-foreground">
                <th className="pb-1 pr-4">Run</th>
                <th className="pb-1">Status</th>
              </tr>
            </thead>
            <tbody>
              {plan.variants.map((v) => (
                <tr key={v.id} className="border-b last:border-0">
                  <td className="py-1 pr-4">{v.name}</td>
                  <td className="py-1">
                    <Badge variant={RUN_STATUS_BADGE_VARIANT[v.status] ?? "outline"} className="capitalize">
                      {v.status}
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-xs text-muted-foreground">
            Every run above is planned only -- nothing has been executed. Execution is a future
            phase.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="flex items-center justify-between py-4">
          <div className="flex items-center gap-2">
            <LineChart className="h-4 w-4 text-primary" />
            <span className="text-sm font-medium">Visualize Test Cases</span>
          </div>
          <Button
            size="sm"
            variant="outline"
            disabled={plan.test_cases.length === 0 || !vizInput || !vizOutput}
            onClick={handleOpenVisualization}
          >
            Open Visualization
          </Button>
        </CardContent>
      </Card>

      <Dialog open={vizOpen} onOpenChange={setVizOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Beaker className="h-4 w-4" />
              Test Case Visualization
            </DialogTitle>
          </DialogHeader>
          <div className="grid gap-3 sm:grid-cols-[1fr_1fr_1fr_auto]">
            <div className="flex-1">
              <Label className="text-xs">Input (x-axis)</Label>
              <select
                className="mt-1 w-full rounded-md border border-input bg-background px-2 py-1 text-sm"
                value={vizInput}
                onChange={(e) => setVizInput(e.target.value)}
              >
                {[...plan.inputs.map((i) => i.name), ...plan.variables.map((v) => v.name)].map(
                  (name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  )
                )}
              </select>
            </div>
            <div className="flex-1">
              <Label className="text-xs">Output (y-axis)</Label>
              <select
                className="mt-1 w-full rounded-md border border-input bg-background px-2 py-1 text-sm"
                value={vizOutput}
                onChange={(e) => setVizOutput(e.target.value)}
              >
                {plan.outputs.map((o) => (
                  <option key={o.name} value={o.name}>
                    {o.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex-1">
              <Label htmlFor="viz-group-by" className="text-xs">
                Compare by
              </Label>
              <select
                id="viz-group-by"
                className="mt-1 w-full rounded-md border border-input bg-background px-2 py-1 text-sm"
                value={vizGroupBy === vizInput ? "" : vizGroupBy}
                onChange={(e) => setVizGroupBy(e.target.value)}
              >
                <option value="">None</option>
                {[...plan.inputs.map((i) => i.name), ...plan.variables.map((v) => v.name)]
                  .filter((name) => name !== vizInput)
                  .map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
              </select>
            </div>
            <Button size="sm" className="mt-auto" onClick={handleOpenVisualization} disabled={vizLoading}>
              {vizLoading && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
              Refresh
            </Button>
          </div>
          {vizLoading && !vizData ? (
            <p className="text-sm text-muted-foreground">Loading...</p>
          ) : vizData ? (
            <Suspense fallback={<p className="text-sm text-muted-foreground">Loading chart...</p>}>
              <ExperimentVisualizationChart data={vizData} />
            </Suspense>
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  );
}
