import { useQuery } from "@tanstack/react-query";
import { usePagedProjects } from "@/hooks/usePagedLists";
import { LoadMore } from "@/components/common/LoadMore";
import { FolderKanban } from "lucide-react";

import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState } from "@/components/common/ErrorState";
import { ProjectGridSkeleton } from "@/components/common/Skeletons";
import { PageTransition, Stagger, StaggerItem } from "@/components/motion/PageTransition";
import { CreateProjectDialog } from "@/features/projects/CreateProjectDialog";
import { ProjectCard } from "@/features/projects/ProjectCard";
import * as projectsService from "@/services/projects";

export function Projects() {
  const projectsQuery = usePagedProjects();

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

      {projectsQuery.isError && !projectsQuery.isFetchNextPageError && <ErrorState error={projectsQuery.error} />}

      {projectsQuery.isSuccess && projectsQuery.items.length === 0 && (
        <EmptyState
          icon={FolderKanban}
          title="No research projects yet."
          description="Create a project to start uploading documents and running grounded research."
        />
      )}

      {projectsQuery.items.length > 0 && (
        <Stagger className="grid grid-cols-1 items-stretch gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {projectsQuery.items.map((project) => (
            <StaggerItem key={project.id} className="h-full">
              <CountedProjectCard project={project} />
            </StaggerItem>
          ))}
        </Stagger>
      )}
      <LoadMore query={projectsQuery} label="projects" />
    </PageTransition>
  );
}

function CountedProjectCard({ project }: { project: Parameters<typeof ProjectCard>[0]["project"] }) {
  const summary = useQuery({ queryKey: ["projects", "summary", project.id], queryFn: () => projectsService.getWorkspaceSummary(project.id) });
  return <ProjectCard project={project} stats={{ documents: summary.data?.documents, groundedAnswers: summary.data?.grounded_answers }} />;
}
