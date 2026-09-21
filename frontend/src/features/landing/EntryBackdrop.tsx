import Velaris from "@/components/ui/velaris";

/** The static aurora's own two washes, pre-blended against the page
 * background because WebGL takes neither CSS variables nor alpha here.
 *
 * `.landing-aurora` paints `--primary` at 0.16 and `--ai` at 0.10 over
 * `--background`, and that restraint is the point: the entry page
 * describes its one moving element as registering "as alive rather than
 * as an animation demanding attention". Handing the shader the tokens
 * at full strength broke that -- it mixes them in at weights up to 0.85
 * and adds a centre glow on top, so saturated input became a surface
 * that competed with the hero instead of sitting behind it. Blended to
 * the same alphas the CSS uses, the motion reads at the intensity the
 * rest of the page was designed around.
 *
 * Dark-theme values, which is sound while `animated` is only set by the
 * two pages that force dark. A light-theme caller would need these
 * re-derived.
 *
 * Order is not cosmetic: the shader weights the first colour most
 * heavily and draws its centre glow from the second. */
const ENTRY_COLORS = ["#1A1B36", "#0B1F29", "#131428", "#070A13"];
const ENTRY_BACKGROUND = "#070A13";

/**
 * The structural backdrop shared by the public entry experience, the
 * sign-in screen (Sprint 9K.4) and the application shell.
 *
 * Three stacked layers, all decorative and all `aria-hidden`: two very
 * wide colour washes, a fine structural grid, and a vignette that
 * darkens the frame so the centre composition holds the eye.
 *
 * It is a *component* rather than three copies of the same markup
 * specifically so the landing page and the login page paint the same
 * surface. Crossing from one to the other, the background does not
 * change — only the content on top of it does, which is what makes the
 * handoff read as one continuous place rather than two pages. Every
 * layer is driven by palette tokens, so it renders correctly in both
 * themes even though the landing itself is always dark.
 *
 * Pure CSS by default: no canvas, no image, no runtime cost beyond
 * compositing. `animated` swaps the static colour wash for the WebGL
 * aurora in `components/ui/velaris` -- opt-in, because the application
 * shell renders this backdrop on every authenticated screen and must
 * not pay for a permanent render loop. The grid and vignette sit on
 * top either way, so the two modes read as the same surface.
 *
 * Fixed to the viewport rather than to the document. The grid's mask
 * and the vignette are both radial gradients sized in percentages, and
 * once the entry page became a nine-section scrolling document those
 * percentages resolved against ~8,500px of page — which stretched the
 * vignette into a single enormous ellipse and left visible horizontal
 * bands where its stops landed (seen at 1440×900, section 09). Pinned
 * to the viewport, every gradient resolves against one screen, so the
 * texture is identical at every scroll position and there is no edge
 * to notice.
 */
export function EntryBackdrop({
  vignette = true,
  animated = false,
}: {
  vignette?: boolean;
  animated?: boolean;
}) {
  return (
    <div aria-hidden="true" className="pointer-events-none fixed inset-0 z-0 overflow-hidden print:hidden">
      {animated ? (
        <Velaris
          height="100%"
          className="absolute inset-0"
          bg={ENTRY_BACKGROUND}
          colors={ENTRY_COLORS}
          speed={0.35}
          grain={0.12}
        />
      ) : (
        <div className="landing-aurora absolute inset-0" />
      )}
      <div className="landing-grid absolute inset-0" />
      {vignette && <div className="landing-vignette absolute inset-0" />}
    </div>
  );
}
