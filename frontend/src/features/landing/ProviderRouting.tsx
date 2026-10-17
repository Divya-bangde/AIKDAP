import { motion } from "motion/react";

import { diagramStage, inViewOnce } from "@/lib/motion";

/** An illustrative provider chain, independent of runtime configuration. Names
 * only — no endpoints, no models, no keys, nothing that describes how
 * to reach any of them. */
const PROVIDERS = [
  { name: "Gemini", role: "Primary" },
  { name: "Groq", role: "First fallback" },
  { name: "OpenRouter", role: "Second fallback" },
];

/**
 * The gateway's provider chain, drawn as an ordered path.
 *
 * What this figure claims is narrow and true: the gateway is
 * configured with an ordered list of providers, and when one is
 * unavailable it continues to the next. It does not claim any provider
 * is up or down right now — the platform *does* track that, and shows
 * it on the System Health page from real backend state, which is where
 * a live claim belongs.
 *
 * No credential, endpoint or model identifier appears here or anywhere
 * else in the landing bundle.
 */
export function ProviderRouting() {
  return (
    <figure>
      <ol className="flex flex-col gap-3 sm:flex-row sm:items-stretch">
        {PROVIDERS.map((provider, index) => (
          <motion.li
            key={provider.name}
            initial="hidden"
            whileInView="visible"
            viewport={inViewOnce}
            variants={diagramStage(index)}
            className="flex flex-1 items-center gap-3"
          >
            <div className="flex-1 rounded-lg border border-border bg-card px-5 py-4 shadow-subtle">
              <p className="text-label uppercase text-muted-foreground">{provider.role}</p>
              <p className="mt-1.5 font-display text-lg tracking-tight text-foreground">
                {provider.name}
              </p>
            </div>

            {index < PROVIDERS.length - 1 && (
              <span
                aria-hidden="true"
                className="relative hidden h-px w-8 shrink-0 bg-border sm:block"
              >
                {/* A short travelling highlight along the connector —
                 * the visual language the application already uses for
                 * "work is moving through here". Decorative: it is
                 * disabled entirely under reduced motion. */}
                <motion.span
                  initial={{ x: "-100%" }}
                  whileInView={{ x: "220%" }}
                  viewport={{ once: false }}
                  transition={{
                    duration: 1.6,
                    ease: "linear",
                    repeat: Infinity,
                    repeatDelay: 1.2,
                    delay: index * 0.4,
                  }}
                  className="absolute inset-y-0 left-0 w-4 bg-gradient-to-r from-transparent via-ai to-transparent motion-reduce:hidden"
                />
              </span>
            )}
          </motion.li>
        ))}
      </ol>

      <figcaption className="mt-8 border-l-2 border-ai/40 pl-4 text-sm leading-relaxed text-muted-foreground">
        Illustrative provider chain. Actual providers and their order depend on configuration.
        This diagram does not report availability or guarantee that a run succeeds. Current
        provider health appears on the System Health page.
      </figcaption>
    </figure>
  );
}
