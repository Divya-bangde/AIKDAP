import { BarChart3, Database, File, FileText, Layers, Search, type LucideIcon } from "lucide-react";
import { useState } from "react";

import { EmptyState } from "@/components/common/EmptyState";
import { FilterChip } from "@/components/common/FilterChip";
import { ErrorState } from "@/components/common/ErrorState";
import { RowListSkeleton } from "@/components/common/Skeletons";
import { StatusBadge } from "@/components/common/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { usePagedAssets } from "@/hooks/usePagedLists";
import { LoadMore } from "@/components/common/LoadMore";
import { messageFor } from "@/lib/api-error";
import { saveBlob } from "@/lib/export";
import { formatBytes, formatRelativeTime } from "@/lib/format";
import * as assetsService from "@/services/assets";
import * as reportsService from "@/services/reports";
import type { components } from "@/types/api";

type AssetRead = components["schemas"]["AssetRead"];
type Category = "documents" | "reports" | "analyses" | "datasets" | "other";

const CATEGORIES: Record<Category, { label: string; icon: LucideIcon }> = {
  documents: { label: "Documents", icon: FileText },
  reports: { label: "Reports", icon: Layers },
  analyses: { label: "Analyses", icon: BarChart3 },
  datasets: { label: "Datasets", icon: Database },
  other: { label: "Other", icon: File },
};

const SOURCE_LABELS: Record<string, string> = {
  upload: "Uploaded",
  imported: "Imported",
  generated: "Generated",
};

export function assetCategory(asset: AssetRead): Category {
  switch (asset.asset_type) {
    case "document":
      return "documents";
    case "report":
    case "summary":
      return "reports";
    case "chart":
      return "analyses";
    case "dataset":
      return "datasets";
    default:
      return "other";
  }
}

/** The project's asset library: everything it holds -- the sources the
 * user brought in and everything AIKDAP generated from them -- in one
 * filterable list, each with the action that fits its kind. Shares the
 * `["assets", projectId]` query (and its polling) with the rest of the
 * project page. */
export function AssetsLibrary({
  projectId,
  onOpenAnalytics,
}: {
  projectId: string;
  onOpenAnalytics: () => void;
}) {
  const [category, setCategory] = useState<Category | null>(null);
  const [search, setSearch] = useState("");
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const assetsQuery = usePagedAssets(projectId);

  if (assetsQuery.isLoading) return <RowListSkeleton label="Loading assets" />;
  if (assetsQuery.isError && !assetsQuery.isFetchNextPageError) return <ErrorState error={assetsQuery.error} title="Couldn't load assets" />;

  const assets = assetsQuery.items;
  if (assets.length === 0) {
    return (
      <EmptyState
        icon={Layers}
        title="No assets yet."
        description="Uploaded documents, reports, analyses and datasets all collect here."
      />
    );
  }

  const present = (Object.keys(CATEGORIES) as Category[]).filter((key) =>
    assets.some((asset) => assetCategory(asset) === key),
  );
  const needle = search.trim().toLowerCase();
  const filtered = assets.filter(
    (asset) =>
      (category === null || assetCategory(asset) === category) &&
      (needle === "" || asset.title.toLowerCase().includes(needle)),
  );

  function download(action: () => Promise<void>) {
    setDownloadError(null);
    action().catch((error: unknown) => setDownloadError(messageFor(error)));
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 sm:max-w-xs">
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
          />
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search assets"
            aria-label="Search assets"
            className="h-8 w-full rounded-md border border-input bg-background pl-8 pr-3 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          />
        </div>
        {present.length > 1 && (
          <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Filter by type">
            <FilterChip label="All" active={category === null} onClick={() => setCategory(null)} />
            {present.map((key) => (
              <FilterChip
                key={key}
                label={CATEGORIES[key].label}
                active={category === key}
                onClick={() => setCategory(key)}
              />
            ))}
          </div>
        )}
      </div>

      {downloadError && (
        <p role="alert" className="text-sm text-destructive">
          {downloadError}
        </p>
      )}

      {assetsQuery.hasNextPage && <p className="text-xs text-muted-foreground">Filters apply to loaded assets. Load more to include older items.</p>}
      {filtered.length === 0 ? (
        <EmptyState icon={Search} title="No assets match this filter." />
      ) : (
        <Card>
          <CardContent className="flex flex-col gap-1 p-3">
            {filtered.map((asset) => (
              <AssetRow
                key={asset.id}
                asset={asset}
                onOpenAnalytics={onOpenAnalytics}
                onDownload={download}
              />
            ))}
          </CardContent>
        </Card>
      )}
      <LoadMore query={assetsQuery} label="assets" />
    </div>
  );
}

function AssetRow({
  asset,
  onOpenAnalytics,
  onDownload,
}: {
  asset: AssetRead;
  onOpenAnalytics: () => void;
  onDownload: (action: () => Promise<void>) => void;
}) {
  const category = assetCategory(asset);
  const { icon: Icon, label } = CATEGORIES[category];
  const completed = asset.processing_status === "completed";
  const isReport = asset.source === "generated" && typeof asset.metadata?.kind === "string";
  // Generated reports and analyses store no file (`file_size` 0); they
  // are rendered from their metadata instead.
  const hasFile = asset.file_size > 0;

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg px-3 py-2.5 transition-colors hover:bg-sunken">
      <div className="flex min-w-0 items-center gap-3">
        <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0">
          <p className="line-clamp-1 text-sm font-medium">{asset.title}</p>
          <p className="text-xs text-muted-foreground">
            {label} · {SOURCE_LABELS[asset.source] ?? asset.source} · {formatRelativeTime(asset.created_at)}
            {hasFile && ` · ${formatBytes(asset.file_size)}`}
          </p>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {!completed && <StatusBadge domain="assetProcessing" value={asset.processing_status} />}
        {completed && isReport && (
          <>
            <Button
              size="sm"
              variant="outline"
              onClick={() =>
                onDownload(() => reportsService.downloadReport(asset.id, "docx", `${asset.title}.docx`))
              }
            >
              DOCX
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() =>
                onDownload(() => reportsService.downloadReport(asset.id, "pdf", `${asset.title}.pdf`))
              }
            >
              PDF
            </Button>
          </>
        )}
        {category === "analyses" && (
          <Button size="sm" variant="outline" onClick={onOpenAnalytics}>
            Open in Analytics
          </Button>
        )}
        {hasFile && (
          <Button
            size="sm"
            variant="outline"
            onClick={() =>
              onDownload(async () => saveBlob(await assetsService.downloadAssetFile(asset.id), asset.file_name))
            }
          >
            Download
          </Button>
        )}
      </div>
    </div>
  );
}
