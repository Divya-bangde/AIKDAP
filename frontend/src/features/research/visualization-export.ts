import type { ChartContext } from "./ChartFrame";

const NS = "http://www.w3.org/2000/svg";
/** All added source/context text uses DOM textContent, never markup interpolation. */
export function contextualSvg(svg: string, context: ChartContext, metadata: unknown, colors = { ink: "#111827", background: "#ffffff" }): string {
  const parser = new DOMParser();
  const original = parser.parseFromString(svg, "image/svg+xml").documentElement;
  if (original.localName !== "svg" || original.querySelector("parsererror")) throw new Error("The renderer did not produce valid SVG.");
  const view = original.getAttribute("viewBox")?.split(/[\s,]+/).map(Number);
  const width = view?.[2] || Number.parseFloat(original.getAttribute("width") ?? "") || 1000;
  const height = view?.[3] || Number.parseFloat(original.getAttribute("height") ?? "") || 600;
  const sourceLines = (context.sources ?? []).map((source) => `Source: ${source.title}; ${source.reference ?? source.url ?? source.id}`);
  const lines = [context.title, `Origin: ${context.origin}`, ...(context.finding ? [context.finding] : []), ...(context.context ?? []), ...(context.scope ?? []), ...sourceLines];
  const maxChars = Math.max(30, Math.floor(width / 8));
  const wrapped = lines.flatMap((line) => Array.from({ length: Math.max(1, Math.ceil(Array.from(line).length / maxChars)) }, (_, index) => Array.from(line).slice(index * maxChars, (index + 1) * maxChars).join("")));
  const footer = wrapped.length * 21 + 32;
  const doc = document.implementation.createDocument(NS, "svg", null);
  const root = doc.documentElement;
  root.setAttribute("width", String(width)); root.setAttribute("height", String(height + footer)); root.setAttribute("viewBox", `0 0 ${width} ${height + footer}`);
  const title = doc.createElementNS(NS, "title"); title.textContent = context.title; root.append(title);
  const background = doc.createElementNS(NS, "rect"); background.setAttribute("width", "100%"); background.setAttribute("height", "100%"); background.setAttribute("fill", colors.background); root.append(background);
  const drawing = doc.importNode(original, true); drawing.setAttribute("width", String(width)); drawing.setAttribute("height", String(height)); drawing.setAttribute("x", "0"); drawing.setAttribute("y", "0"); root.append(drawing);
  wrapped.forEach((line, index) => { const text = doc.createElementNS(NS, "text"); text.setAttribute("x", "16"); text.setAttribute("y", String(height + 24 + index * 21)); text.setAttribute("fill", colors.ink); text.setAttribute("font-family", "sans-serif"); text.setAttribute("font-size", "14"); text.textContent = line; root.append(text); });
  const details = doc.createElementNS(NS, "metadata"); details.textContent = JSON.stringify(metadata); root.append(details);
  return new XMLSerializer().serializeToString(root);
}

export function svgFromDataUrl(url: string): string {
  const split = url.indexOf(",");
  if (!url.startsWith("data:image/svg+xml") || split < 0) throw new Error("Unexpected image export format.");
  return url.slice(0, split).includes(";base64") ? atob(url.slice(split + 1)) : decodeURIComponent(url.slice(split + 1));
}
