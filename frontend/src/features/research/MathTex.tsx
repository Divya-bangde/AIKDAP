import katex from "katex";
import "katex/dist/katex.min.css";

/** Typesets one LaTeX string with KaTeX.
 *
 * `throwOnError: false`: LaTeX the model got wrong renders as KaTeX's
 * red source text rather than breaking the answer. KaTeX's default
 * `trust: false` keeps commands like `\href` inert, so its HTML output
 * is safe to inject. */
export function MathTex({ tex, display = false }: { tex: string; display?: boolean }) {
  const html = katex.renderToString(tex, { throwOnError: false, displayMode: display });
  return <span dangerouslySetInnerHTML={{ __html: html }} />;
}
