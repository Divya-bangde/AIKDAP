import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import { lazy, Suspense, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";

import { ErrorState } from "@/components/common/ErrorState";
import { ProjectWorkspaceSkeleton, RowListSkeleton } from "@/components/common/Skeletons";
import { PageTransition } from "@/components/motion/PageTransition";
import { AnimatedNumber } from "@/components/motion/AnimatedNumber";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { DocumentsSection } from "@/features/assets/DocumentsSection";
import { allSettled, knowledgeState } from "@/features/assets/asset-state";
import { ProjectHeader } from "@/features/projects/ProjectHeader";
import { ResearchHistoryList } from "@/features/research/ResearchHistoryList";
import { usePolling } from "@/hooks/usePolling";
import { fadeUp } from "@/lib/motion";
import * as assetsService from "@/services/assets";
import * as projectsService from "@/services/projects";
import * as researchService from "@/services/research";

const TABS = [
  { value: "overview", label: "Overview" },
  { value: "documents", label: "Documents" },
  { value: "research", label: "Research" },
  { value: "papers", label: "Paper map" },
];

// The force-graph canvas library loads only when this tab is opened.
const PaperMap = lazy(() => import("@/features/paper-map/PaperMap"));

/** One real, backend-counted figure about this project's knowledge base. */
function KnowledgeStat({ label, value, detail }: { label: string; value: number; detail: string }) {
  return (
    <div className="rounded-card bg-card p-6 shadow-subtle">
      <p className="text-label uppercase text-muted-foreground">{label}</p>
      <AnimatedNumber value={value} className="tabular mt-1.5 block text-3xl font-semibold tracking-tight" />
      <p className="mt-1 text-xs text-muted-foreground">{detail}</p>
    </div>
  );
}

export function ProjectDetail() {
  const { id } = useParams<{ id: string }>();
  const [searchParams] = useSearchParams();
  // `?tab=documents` is how the Active Work toast deep-links here.
  const [tab, setTab] = useState(() => {
    const requested = searchParams.get("tab");
    return TABS.some((item) => item.value === requested) ? requested! : "overview";
  });

  const projectQuery = useQuery({
    queryKey: ["projects", id],
    queryFn: () => projectsService.getProject(id!),
    enabled: Boolean(id),
  });

  // Same query key and same terminal condition as `DocumentsSection`,
  // so both observers share one cached response and one polling
  // schedule rather than competing.
  const assetsQuery = usePolling({
    queryKey: ["assets", id],
    queryFn: () => assetsService.listAssets(id!),
    isTerminal: allSettled,
    enabled: Boolean(id),
  });

  const runsQuery = useQuery({
    queryKey: ["research", "runs", id],
    queryFn: () => researchService.listResearchRuns(id!),
    enabled: Boolean(id),
  });

  if (projectQuery.isLoading) {
    return <ProjectWorkspaceSkeleton />;
  }

  if (projectQuery.isError) {
    return <ErrorState error={projectQuery.error} title="Project not found" />;
  }

  if (!projectQuery.data) return null;

  const project = projectQuery.data;
  const assets = assetsQuery.data ?? [];
  const stats = knowledgeState(assets);
  const runs = runsQuery.data ?? [];

  return (
    <PageTransition>
      <ProjectHeader
        project={project}
        assets={assets}
        runs={runs}
        onRequestUpload={() => setTab("documents")}
      />

      <Tabs value={tab} onValueChange={setTab} className="flex flex-col gap-6">
        <TabsList>
          {TABS.map((item) => (
            <TabsTrigger key={item.value} value={item.value} isActive={tab === item.value}>
              {item.label}
            </TabsTrigger>
          ))}
        </TabsList>

        <TabsContent value="overview">
          <motion.div initial="hidden" animate="visible" variants={fadeUp}>
            <h2 className="mb-3 text-section">Knowledge state</h2>
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <KnowledgeStat label="Documents" value={stats.total} detail="Uploaded" />
              <KnowledgeStat label="Extracted" value={stats.extracted} detail="Pipeline" />
              <KnowledgeStat label="Understood" value={stats.understood} detail="Qwen 3.5" />
              <KnowledgeStat label="Embedded" value={stats.embedded} detail="BGE-M3" />
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              Counts reflect the backend's reported state for each document.
            </p>
          </motion.div>
        </TabsContent>

        <TabsContent value="documents">
          <motion.div initial="hidden" animate="visible" variants={fadeUp}>
            <DocumentsSection projectId={project.id} />
          </motion.div>
        </TabsContent>

        <TabsContent value="research">
          <motion.div initial="hidden" animate="visible" variants={fadeUp}>
            {runsQuery.isLoading ? (
              <RowListSkeleton label="Loading research history" />
            ) : (
              <ResearchHistoryList runs={runs} />
            )}
          </motion.div>
        </TabsContent>

        <TabsContent value="papers">
          {tab === "papers" && (
            <Suspense fallback={<RowListSkeleton label="Loading paper map" />}>
              <PaperMap projectId={project.id} />
            </Suspense>
          )}
        </TabsContent>
      </Tabs>
    </PageTransition>
  );
}
