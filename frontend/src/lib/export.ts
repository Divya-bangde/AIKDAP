import { asCitation, type Citation } from "@/types/citation";
import type { components } from "@/types/api";

type AssetRead = components["schemas"]["AssetRead"];
type ProjectRead = components["schemas"]["ProjectRead"];
type ResearchRunRead = components["schemas"]["ResearchRunRead"];

/** The fields an export reads — satisfied by both the list and the
 * detail shape of a research run. */
type ExportableRun = Pick<
  ResearchRunRead,
  "query" | "status" | "grounding_status" | "final_answer" | "citations" | "created_at"
>;

/** Saves `blob` as a file through the browser's own download. */
export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

/** Saves `content` as a file through the browser's own download. */
export function downloadFile(filename: string, content: string, type: string) {
  saveBlob(new Blob([content], { type }), filename);
}

/** A filesystem-safe file name stem from free text. */
export function fileSlug(text: string): string {
  const slug = text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 60);
  return slug || "aikdap-export";
}

function citationLine(citation: Citation, index: number): string {
  const name = citation.title ?? citation.file_name ?? citation.source ?? "Untitled source";
  return `${index + 1}. ${name}${citation.reference ? ` — ${citation.reference}` : ""}`;
}

/** One research run as Markdown: the question, its real outcome, the
 * answer and the sources it cites — only fields the backend returned. */
function runSection(run: ExportableRun, heading: string): string[] {
  const citations = (run.citations ?? []).map(asCitation);
  return [
    `${heading} ${run.query}`,
    "",
    `- Status: ${run.status}`,
    ...(run.grounding_status ? [`- Grounding: ${run.grounding_status}`] : []),
    `- Asked: ${new Date(run.created_at).toLocaleString()}`,
    "",
    run.final_answer ?? "_No answer was generated._",
    "",
    ...(citations.length > 0 ? ["**Sources**", "", ...citations.map(citationLine), ""] : []),
  ];
}

export function researchRunMarkdown(run: ExportableRun): string {
  return runSection(run, "#").join("\n");
}

export function projectMarkdown(
  project: ProjectRead,
  assets: AssetRead[],
  runs: ExportableRun[],
): string {
  return [
    `# ${project.name}`,
    "",
    ...(project.description ? [project.description, ""] : []),
    `## Documents (${assets.length})`,
    "",
    ...(assets.length > 0 ? assets.map((asset) => `- ${asset.title}`) : ["_None uploaded._"]),
    "",
    `## Research (${runs.length})`,
    "",
    ...runs.flatMap((run) => runSection(run, "###")),
  ].join("\n");
}
