import { useQuery } from "@tanstack/react-query";
import { FolderKanban } from "lucide-react";

import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState } from "@/components/common/ErrorState";
import { ProjectGridSkeleton } from "@/components/common/Skeletons";
import { PageTransition, Stagger, StaggerItem } from "@/components/motion/PageTransition";
import { CreateProjectDialog } from "@/features/projects/CreateProjectDialog";
import { ProjectCard, type ProjectStats } from "@/features/projects/ProjectCard";
import { isGroundedAnswer } from "@/features/research/research-presentation";
import { isSource } from "@/features/assets/asset-state";
import * as assetsService from "@/services/assets";
import * as projectsService from "@/services/projects";
import * as researchService from "@/services/research";

export function Projects() {
  const projectsQuery = useQuery({
    queryKey: ["projects"],
    queryFn: projectsService.listProjects,
  });

  // One unfiltered fetch each, then grouped by `project_id` on the
  // client — rather than N requests, one per card. Both endpoints are
  // already owner-scoped by the backend.
  const assetsQuery = useQuery({
    queryKey: ["assets", "all"],
    queryFn: () => assetsService.listAssets(),
  });
  const runsQuery = useQuery({
    queryKey: ["research", "runs", "all"],
    queryFn: () => researchService.listResearchRuns(),
  });

  /** Real per-project counts, or `undefined` while unknown.
   *
   * `undefined` matters: the card renders nothing at all rather than
   * "0 documents" for a project whose assets simply haven't loaded (or
   * failed to load). A zero that means "we don't know yet" is exactly
   * the kind of fabricated figure this product doesn't ship. */
  function statsFor(projectId: string): ProjectStats {
    return {
      documents: assetsQuery.isSuccess
        ? assetsQuery.data.filter((asset) => asset.project_id === projectId && isSource(asset)).length
        : undefined,
      groundedAnswers: runsQuery.isSuccess
        ? runsQuery.data.filter(
            (run) => run.project_id === projectId && isGroundedAnswer(run),
          ).length
        : undefined,
    };
  }

  return (
    <PageTransition>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-label uppercase text-muted-foreground">Workspace</p>
          <h1 className="mt-1 font-display text-display">Projects</h1>
          <p className="mt-2 max-w-xl text-sm text-muted-foreground">
            Each project is its own knowledge base. Documents uploaded here are the only
            evidence AIKDAP will answer from.
          </p>
        </div>
        <CreateProjectDialog />
      </div>

      <h2 className="sr-only">Your projects</h2>

      {projectsQuery.isLoading && <ProjectGridSkeleton />}

      {projectsQuery.isError && <ErrorState error={projectsQuery.error} />}

      {projectsQuery.isSuccess && projectsQuery.data.length === 0 && (
        <EmptyState
          icon={FolderKanban}
          title="No research projects yet."
          description="Create a project to start uploading documents and running grounded research."
        />
      )}

      {projectsQuery.isSuccess && projectsQuery.data.length > 0 && (
        <Stagger className="grid grid-cols-1 items-stretch gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {projectsQuery.data.map((project) => (
            <StaggerItem key={project.id} className="h-full">
              <ProjectCard project={project} stats={statsFor(project.id)} />
            </StaggerItem>
          ))}
        </Stagger>
      )}
    </PageTransition>
  );
}
