import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import { useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";

import { ErrorState } from "@/components/common/ErrorState";
import { ProjectWorkspaceSkeleton, RowListSkeleton } from "@/components/common/Skeletons";
import { PageTransition } from "@/components/motion/PageTransition";
import { AnimatedNumber } from "@/components/motion/AnimatedNumber";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { DocumentsSection } from "@/features/assets/DocumentsSection";
import { AssetsLibrary } from "@/features/assets/AssetsLibrary";
import { allSettled, isSource, knowledgeState } from "@/features/assets/asset-state";
import { ProjectHeader } from "@/features/projects/ProjectHeader";
import { ReportsList } from "@/features/reports/ReportsList";
import { ResearchHistoryList } from "@/features/research/ResearchHistoryList";
import { LazyRenderer } from "@/features/research/RendererBoundary";
import { usePolling } from "@/hooks/usePolling";
import { usePagedResearch } from "@/hooks/usePagedLists";
import { LoadMore } from "@/components/common/LoadMore";
import { KnowledgeReadiness, useKnowledgeReadiness } from "@/features/assets/KnowledgeReadiness";
import { fadeUp } from "@/lib/motion";
import * as assetsService from "@/services/assets";
import * as projectsService from "@/services/projects";
import * as researchService from "@/services/research";

const TABS = [
  { value: "overview", label: "Overview" },
  { value: "documents", label: "Documents" },
  { value: "assets", label: "Assets" },
  { value: "research", label: "Research" },
  { value: "papers", label: "Paper map" },
  { value: "analytics", label: "Analytics" },
  { value: "reports", label: "Reports" },
];

// The force-graph canvas library loads only when this tab is opened.
const loadPaperMap = () => import("@/features/paper-map/PaperMap");
// Plotly and the analytics workspace load only when this tab is opened.
const loadAnalyticsWorkspace = () =>
  import("@/features/business-analytics/AnalyticsWorkspace").then((m) => ({
    default: m.AnalyticsWorkspace,
  }));

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

  const historyQuery = usePagedResearch(id);
  const summaryQuery = useQuery({ queryKey: ["projects", "summary", id], queryFn: () => projectsService.getWorkspaceSummary(id), enabled: Boolean(id) });
  const readiness = useKnowledgeReadiness(id!);

  if (projectQuery.isLoading) {
    return <ProjectWorkspaceSkeleton />;
  }

  if (projectQuery.isError) {
    return <ErrorState error={projectQuery.error} title="Project not found" />;
  }

  if (!projectQuery.data) return null;

  const project = projectQuery.data;
  // Sources only: generated outputs are not documents (see Assets tab).
  const assets = (assetsQuery.data ?? []).filter(isSource);
  const stats = knowledgeState(assets);
  const runs = runsQuery.data ?? [];

  return (
    <PageTransition>
      <ProjectHeader
        project={project}
        assets={assets}
        runs={runs}
        readiness={readiness.data}
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
              {summaryQuery.data && <KnowledgeStat label="Documents" value={summaryQuery.data.documents} detail="All uploaded sources" />}
              <KnowledgeStat label="Extracted" value={stats.extracted} detail="Loaded sources" />
              <KnowledgeStat label="Understood" value={stats.understood} detail="Loaded sources" />
              <KnowledgeStat label="Embedded" value={stats.embedded} detail="Loaded sources" />
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              Pipeline counts describe the loaded document sample; Documents is the full project total.
            </p>
            <KnowledgeReadiness projectId={project.id} />
          </motion.div>
        </TabsContent>

        <TabsContent value="documents">
          <motion.div initial="hidden" animate="visible" variants={fadeUp}>
            <DocumentsSection projectId={project.id} />
          </motion.div>
        </TabsContent>

        <TabsContent value="assets">
          {tab === "assets" && (
            <motion.div initial="hidden" animate="visible" variants={fadeUp}>
              <AssetsLibrary projectId={project.id} onOpenAnalytics={() => setTab("analytics")} />
            </motion.div>
          )}
        </TabsContent>

        <TabsContent value="research">
          <motion.div initial="hidden" animate="visible" variants={fadeUp}>
            {historyQuery.isLoading ? (
              <RowListSkeleton label="Loading research history" />
            ) : (
              <ResearchHistoryList runs={historyQuery.items} />
            )}
            {historyQuery.isError && !historyQuery.isFetchNextPageError && <ErrorState error={historyQuery.error} />}
            <LoadMore query={historyQuery} label="research runs" />
          </motion.div>
        </TabsContent>

        <TabsContent value="papers">
          {tab === "papers" && (
            <LazyRenderer label="paper map" load={loadPaperMap} rendererProps={{projectId:project.id}} />
          )}
        </TabsContent>

        <TabsContent value="analytics">
          {tab === "analytics" && (
            <LazyRenderer label="analytics workspace" load={loadAnalyticsWorkspace} rendererProps={{projectId:project.id}} />
          )}
        </TabsContent>

        <TabsContent value="reports">
          {tab === "reports" && (
            <motion.div initial="hidden" animate="visible" variants={fadeUp}>
              <ReportsList projectId={project.id} />
            </motion.div>
          )}
        </TabsContent>
      </Tabs>
    </PageTransition>
  );
}
