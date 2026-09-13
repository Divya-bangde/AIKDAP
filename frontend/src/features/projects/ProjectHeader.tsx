import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "motion/react";
import { AlertTriangle, FolderKanban, Search, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { layoutSpring } from "@/lib/motion";
import { projectLayoutIds } from "@/features/projects/ProjectCard";
import { ProjectPersonaSelect } from "@/features/projects/ProjectPersonaSelect";

import { ExportMenu } from "@/components/common/ExportMenu";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { knowledgeState } from "@/features/assets/asset-state";
import { fileSlug, projectMarkdown } from "@/lib/export";
import * as projectsService from "@/services/projects";
import type { components } from "@/types/api";

type AssetRead = components["schemas"]["AssetRead"];
type ProjectRead = components["schemas"]["ProjectRead"];
type ResearchRunRead = components["schemas"]["ResearchRunRead"];

const WARNING_MS = 6000;

interface ResearchWarning {
  message: string;
  canUpload: boolean;
}

export function ProjectHeader({
  project,
  assets,
  runs,
  onRequestUpload,
}: {
  project: ProjectRead;
  assets: AssetRead[];
  runs: ResearchRunRead[];
  /** Takes the user to where documents are uploaded. */
  onRequestUpload: () => void;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [warning, setWarning] = useState<ResearchWarning | null>(null);

  useEffect(() => {
    if (!warning) return;
    const timer = setTimeout(() => setWarning(null), WARNING_MS);
    return () => clearTimeout(timer);
  }, [warning]);

  const deleteMutation = useMutation({
    mutationFn: () => projectsService.deleteProject(project.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      navigate("/projects", { replace: true });
    },
  });

  /** Research answers only from embedded documents, so a project with
   * none uploaded — or none finished processing — has nothing to search. */
  function startResearch() {
    const { total, embedded } = knowledgeState(assets);
    if (total === 0) {
      setWarning({
        message: "Upload documents first. Research answers only from this project's documents.",
        canUpload: true,
      });
      return;
    }
    if (embedded === 0) {
      setWarning({
        message: "Your documents are still processing. Research unlocks once one is ready.",
        canUpload: false,
      });
      return;
    }
    navigate(`/research?project=${project.id}`);
  }

  const ids = projectLayoutIds(project.id);

  return (
    <div className="flex flex-col gap-4 pb-2">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 items-start gap-4">
          {/* Shares `layoutId` with the project card on /projects, so
           * arriving here animates that card's icon and title into
           * place rather than swapping one page for another. */}
          <motion.div
            layoutId={ids.icon}
            transition={layoutSpring}
            className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl shadow-subtle"
            style={{ backgroundColor: project.color ?? "hsl(var(--primary))" }}
          >
            <FolderKanban className="h-5 w-5 text-white" />
          </motion.div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-3">
              <motion.h1 layoutId={ids.title} transition={layoutSpring} className="font-display text-title">
                {project.name}
              </motion.h1>
              <Badge variant={project.status === "active" ? "success" : "muted"}>
                {project.status}
              </Badge>
            </div>
            <p className="mt-1.5 text-sm text-muted-foreground">
              {project.description ?? "No description."}
            </p>
            <p className="mt-1 text-label uppercase text-muted-foreground">
              {project.project_type.replace(/_/g, " ")} project
            </p>
            <div className="mt-2">
              <ProjectPersonaSelect project={project} />
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 sm:shrink-0 sm:justify-end">
          <Button onClick={startResearch}>
            <Search className="h-4 w-4" />
            Start Research
          </Button>
          <ExportMenu
            filename={fileSlug(project.name)}
            toMarkdown={() => projectMarkdown(project, assets, runs)}
            toJson={() => ({ project, documents: assets, research_runs: runs })}
          />
          <Button variant="outline" size="icon" onClick={() => setConfirmOpen(true)}>
            <Trash2 className="h-4 w-4" />
            <span className="sr-only">Delete project</span>
          </Button>
        </div>
      </div>

      {/* Centred under the top bar; the wrapper does the centring so
       * Motion's `y` transform doesn't fight a translate class. */}
      <div className="pointer-events-none fixed inset-x-0 top-20 z-50 flex justify-center px-4">
        <AnimatePresence>
          {warning && (
            <motion.div
              role="alert"
              initial={{ opacity: 0, y: -12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -12 }}
              transition={{ type: "spring", stiffness: 380, damping: 30 }}
              className="pointer-events-auto w-full max-w-md overflow-hidden rounded-card bg-card shadow-float"
            >
              <div className="flex items-start gap-3 bg-warning/15 p-5">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-foreground">{warning.message}</p>
                  {warning.canUpload && (
                    <Button
                      size="sm"
                      variant="outline"
                      className="mt-3"
                      onClick={() => {
                        setWarning(null);
                        onRequestUpload();
                      }}
                    >
                      Upload documents
                    </Button>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => setWarning(null)}
                  aria-label="Dismiss warning"
                  className="press rounded-md p-1 text-muted-foreground hover:text-foreground"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete "{project.name}"?</DialogTitle>
            <DialogDescription>
              This permanently deletes the project and every document, embedding, and
              research run inside it. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={deleteMutation.isPending}
              onClick={() => deleteMutation.mutate()}
            >
              {deleteMutation.isPending ? "Deleting…" : "Delete Project"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
