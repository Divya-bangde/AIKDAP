import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import {
  ArrowRight,
  FileText,
  FolderKanban,
  Plus,
  Quote,
  Search,
  Upload,
} from "lucide-react";
import { Link } from "react-router-dom";

import { EmptyState } from "@/components/common/EmptyState";
import { StatusBadge } from "@/components/common/StatusBadge";
import { PageTransition, Stagger, StaggerItem } from "@/components/motion/PageTransition";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SummaryCard } from "@/features/command-center/SummaryCard";
import { isGroundedAnswer, runOutcome } from "@/features/research/research-presentation";
import { formatDuration, formatRelativeTime } from "@/lib/format";
import { hoverLift } from "@/lib/motion";
import * as assetsService from "@/services/assets";
import * as projectsService from "@/services/projects";
import * as researchService from "@/services/research";

export function Dashboard() {
  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: projectsService.listProjects });
  const assetsQuery = useQuery({
    queryKey: ["assets", "all"],
    queryFn: () => assetsService.listAssets(),
  });
  const runsQuery = useQuery({
    queryKey: ["research", "runs", "all"],
    queryFn: () => researchService.listResearchRuns(),
  });

  const recentProjects = projectsQuery.data?.slice(0, 4) ?? [];
  const recentRuns = runsQuery.data?.slice(0, 5) ?? [];

  // Real project name per run, looked up rather than re-fetched — the
  // dashboard already holds the full project list.
  const projectNameById = new Map(
    (projectsQuery.data ?? []).map((project) => [project.id, project.name]),
  );

  // Counted from the real `grounding_status` the backend stores on each
  // run — not inferred from `status`, which only says the run finished.
  // `isGroundedAnswer` is the shared rule (Sprint 9K.10); the count it
  // produces is identical to what this line computed inline before.
  const groundedRuns = runsQuery.data?.filter(isGroundedAnswer).length;

  return (
    <PageTransition>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-display">Command Center</h1>
          <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
            Every project, document and grounded research run in AIKDAP, in one place.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button asChild>
            <Link to="/projects">
              <Plus className="h-4 w-4" />
              Create Project
            </Link>
          </Button>
          <Button variant="outline" asChild>
            <Link to="/projects">
              <Upload className="h-4 w-4" />
              Upload Document
            </Link>
          </Button>
          <Button variant="outline" asChild>
            <Link to="/research">
              <Search className="h-4 w-4" />
              Start Research
            </Link>
          </Button>
        </div>
      </div>

      <h2 className="sr-only">Workspace overview</h2>
      <Stagger className="grid grid-cols-1 items-stretch gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StaggerItem className="h-full">
          <SummaryCard
            icon={FolderKanban}
            label="Projects"
            to="/projects"
            value={projectsQuery.data?.length}
            isLoading={projectsQuery.isLoading}
            isError={projectsQuery.isError}
          />
        </StaggerItem>
        <StaggerItem className="h-full">
          <SummaryCard
            icon={FileText}
            label="Documents"
            to="/documents"
            value={assetsQuery.data?.length}
            isLoading={assetsQuery.isLoading}
            isError={assetsQuery.isError}
            tone="ai"
          />
        </StaggerItem>
        <StaggerItem className="h-full">
          <SummaryCard
            icon={Search}
            label="Research Runs"
            to="/research?history=all"
            value={runsQuery.data?.length}
            isLoading={runsQuery.isLoading}
            isError={runsQuery.isError}
          />
        </StaggerItem>
        <StaggerItem className="h-full">
          <SummaryCard
            icon={Quote}
            label="Grounded Answers"
            to="/research?history=grounded"
            value={groundedRuns}
            isLoading={runsQuery.isLoading}
            isError={runsQuery.isError}
            tone="success"
            detail="Backed by cited evidence"
          />
        </StaggerItem>
      </Stagger>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <CardTitle className="text-section">Recent Projects</CardTitle>
            <Link
              to="/projects"
              className="flex items-center gap-1 text-xs font-medium text-primary hover:underline"
            >
              View all
              <ArrowRight className="h-3 w-3" />
            </Link>
          </CardHeader>
          <CardContent>
            {recentProjects.length === 0 ? (
              <EmptyState icon={FolderKanban} title="No research projects yet." />
            ) : (
              <Stagger className="flex flex-col gap-1">
                {recentProjects.map((project) => (
                  <StaggerItem key={project.id}>
                    <motion.div {...hoverLift}>
                      <Link
                        to={`/projects/${project.id}`}
                        className="group flex items-center gap-3 rounded-lg px-3 py-2.5 transition-colors hover:bg-sunken"
                      >
                        <div
                          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md"
                          style={{ backgroundColor: project.color ?? "hsl(var(--accent))" }}
                        >
                          <FolderKanban className="h-3.5 w-3.5 text-white" />
                        </div>
                        <span className="min-w-0 flex-1 truncate text-sm font-medium">
                          {project.name}
                        </span>
                        <span className="shrink-0 text-xs text-muted-foreground">
                          {formatRelativeTime(project.updated_at)}
                        </span>
                      </Link>
                    </motion.div>
                  </StaggerItem>
                ))}
              </Stagger>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <CardTitle className="text-section">Recent Research Runs</CardTitle>
            <Link
              to="/research"
              className="flex items-center gap-1 text-xs font-medium text-primary hover:underline"
            >
              New run
              <ArrowRight className="h-3 w-3" />
            </Link>
          </CardHeader>
          <CardContent>
            {recentRuns.length === 0 ? (
              <EmptyState icon={Search} title="Ask your first research question." />
            ) : (
              <Stagger className="flex flex-col gap-1">
                {recentRuns.map((run) => {
                  const isTerminal = run.status === "completed" || run.status === "failed";
                  const outcome = runOutcome(run);
                  // A real count, not a guess: `citations` is already
                  // part of `GET /research/runs` — no extra fetch, and
                  // never shown for zero (a "0 citations" line would
                  // read as reassurance for an answer that has none).
                  const citationCount = run.citations?.length ?? 0;
                  return (
                    <StaggerItem key={run.id}>
                      <motion.div {...hoverLift}>
                        <Link
                          to={`/research/${run.id}`}
                          className="flex items-center justify-between gap-3 rounded-lg px-3 py-2.5 transition-colors hover:bg-sunken"
                        >
                          <span className="min-w-0 flex-1">
                            <span className="line-clamp-1 block text-sm font-medium">
                              {run.query}
                            </span>
                            {/* The dashboard aggregates runs across every
                             * project, so — unlike the project workspace's
                             * own research tab — which project each run
                             * belongs to is real information, not noise. */}
                            <span className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs text-muted-foreground">
                              {projectNameById.get(run.project_id) && (
                                <span className="truncate">{projectNameById.get(run.project_id)}</span>
                              )}
                              <span aria-hidden="true">·</span>
                              <span>{formatRelativeTime(run.created_at)}</span>
                              {isTerminal && run.duration_ms !== null && (
                                <>
                                  <span aria-hidden="true">·</span>
                                  <span className="tabular">{formatDuration(run.duration_ms)}</span>
                                </>
                              )}
                              {citationCount > 0 && (
                                <>
                                  <span aria-hidden="true">·</span>
                                  <span className="tabular">
                                    {citationCount} citation{citationCount === 1 ? "" : "s"}
                                  </span>
                                </>
                              )}
                            </span>
                          </span>
                          <div className="flex shrink-0 items-center gap-2">
                            <StatusBadge domain={outcome.domain} value={outcome.value} />
                          </div>
                        </Link>
                      </motion.div>
                    </StaggerItem>
                  );
                })}
              </Stagger>
            )}
          </CardContent>
        </Card>
      </div>
    </PageTransition>
  );
}
