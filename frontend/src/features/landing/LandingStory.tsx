import { motion } from "motion/react";
import { ArrowRight, ListTree, Quote, SlidersHorizontal } from "lucide-react";
import { Link } from "react-router-dom";

import { FlowChain } from "@/features/landing/FlowChain";
import { LandingFooter } from "@/features/landing/LandingFooter";
import { LandingItem, LandingSection } from "@/features/landing/LandingSection";
import { PipelineRail, type PipelineStage } from "@/features/landing/PipelineRail";
import { ProviderRouting } from "@/features/landing/ProviderRouting";
import { RetrievalNarrowing } from "@/features/landing/RetrievalNarrowing";
import { inViewOnce, sectionItem, sectionReveal } from "@/lib/motion";

/* Sections 02–09 of the entry experience.
 *
 * Split into its own module so it can be code-split away from the hero
 * (Phase 21): the first screen a visitor waits for does not include
 * the eight sections below it. This file loads while they are reading
 * the hero.
 *
 * Everything on this page is explanatory. Each stage, model and
 * component named below exists in the platform and is named the way
 * the codebase names it — but nothing here is read from a running
 * system. There is no live count, no status, no score, and no
 * assertion about performance or accuracy anywhere in this file. */

/** Conceptual paths; optional work and fallbacks are explained beside them. */
const INGESTION: PipelineStage[] = [
  {
    name: "Collect",
    detail:
      "Documents are uploaded into a project. Owner and project scope determine access before document processing begins.",
  },
  {
    name: "Extract and chunk",
    detail:
      "Text and passage positions are stored. Deterministic document processing can finish before optional model work.",
  },
  {
    name: "Embed",
    detail:
      "Passage embedding is attempted before AI understanding is queued. Embedding has its own status; a failed attempt does not discard extracted text.",
  },
];
const QUERY: PipelineStage[] = [
  { name: "Plan and route", detail: "The question determines which retrieval agents are selected. Some requests proceed directly to context building." },
  {
    name: "Retrieve",
    detail:
      "When selected, project-document or web retrieval supplies candidate evidence. Document retrieval requires ready embeddings; it does not wait for an AI document profile.",
  },
  {
    name: "Assess evidence",
    detail:
      "A configured reranker can score candidates against the question. If scores are unavailable, retrieval ordering is retained and relevance thresholding is reported as not applied.",
  },
  {
    name: "Synthesize",
    detail:
      "The answer records its evidence outcome. Insufficient evidence may trigger one web fallback, an explicitly labelled general-knowledge answer, or an insufficient-evidence response. Citation and visualization verification are separate checks.",
  },
];

/** What the document-understanding stage records for each document. */
const PROFILE_FIELDS = ["Summary", "Keywords", "Entities", "Topics", "Language"];

const WORKSPACE_FEATURES = [
  {
    icon: ListTree,
    title: "Pipeline timeline",
    body: "Every stage of a run, in the order it executed, with what each one produced.",
  },
  {
    icon: SlidersHorizontal,
    title: "Evidence funnel",
    body: "How many passages were retrieved, how many reached the model, and how many were cited — for that run.",
  },
  {
    icon: Quote,
    title: "Citation to source",
    body: "Open any citation to see the passage behind it, the document it came from, and how it scored.",
  },
];

export default function LandingStory() {
  return (
    <>
      {/* ---- 02 ---------------------------------------------------- */}
      <LandingSection
        index="02"
        eyebrow="The problem"
        heading="Collecting information was never the hard part."
        standfirst={
          <>
            Most organisations already hold far more material than anyone can read. The
            difficulty is not storage or search. It is knowing what your own documents
            actually say — and being able to show why you believe it.
          </>
        }
      >
        <motion.div
          initial="hidden"
          whileInView="visible"
          viewport={inViewOnce}
          variants={sectionReveal}
          className="grid gap-px overflow-hidden rounded-xl border border-border bg-border md:grid-cols-3"
        >
          {[
            { title: "Search finds documents.", body: "You still have to read them." },
            {
              title: "Assistants generate text.",
              body: "Fluent, confident, and accountable to nothing you own.",
            },
            {
              title: "Neither shows its evidence.",
              body: "So neither can be checked, and neither can be trusted with a decision.",
            },
          ].map((item) => (
            <motion.div key={item.title} variants={sectionItem} className="bg-background p-7">
              <p className="font-display text-lg leading-snug tracking-tight text-foreground">
                {item.title}
              </p>
              <p className="mt-2.5 text-sm leading-relaxed text-muted-foreground">{item.body}</p>
            </motion.div>
          ))}
        </motion.div>
      </LandingSection>

      {/* ---- 03 ---------------------------------------------------- */}
      <LandingSection
        id="pipeline"
        index="03"
        eyebrow="What AIKDAP does"
        heading="Document preparation and research follow different paths."
        standfirst={
          <>
            Simplified conceptual overview — not live progress or measured production metrics.
            Ingestion prepares documents; query-time orchestration selects work and records
            the stages, optional steps and fallbacks that actually ran.
          </>
        }
      >
        <div className="grid gap-12 lg:grid-cols-2">
          <section aria-label="Document ingestion path"><h3 className="mb-6 text-section">Document ingestion</h3><PipelineRail stages={INGESTION}/></section>
          <section aria-label="Research query path"><h3 className="mb-6 text-section">Research query</h3><PipelineRail stages={QUERY}/></section>
        </div>
        <p className="mt-8 text-sm text-muted-foreground">AI understanding is a separate, optional background task after embedding is attempted. It is not a prerequisite for retrieval. The run timeline shows actual execution, including skipped or failed work.</p>
      </LandingSection>

      {/* ---- 04 ---------------------------------------------------- */}
      <LandingSection
        index="04"
        eyebrow="AI understanding"
        heading="Optional document profiles add context in the background."
        standfirst={
          <>
            Extraction and passage storage come first. AI understanding can later add a
            document profile alongside the text. A pending or failed profile does not prevent
            retrieval of passages with ready embeddings.
          </>
        }
      >
        <div className="flex flex-col gap-10">
          <FlowChain
            align="start"
            nodes={[
              { label: "Embedding attempted", note: "Separate readiness status" },
              { label: "Optional understanding", note: "Queued background work", tone: "ai" },
              { label: "Document profile", note: "When understanding succeeds", tone: "primary" },
            ]}
          />

          <LandingItem>
            <div className="rounded-xl border border-border bg-card/60 p-7">
              <p className="text-label uppercase text-muted-foreground">
                What the profile records
              </p>
              <ul className="mt-4 flex flex-wrap gap-2">
                {PROFILE_FIELDS.map((field) => (
                  <li
                    key={field}
                    className="rounded-md border border-border-strong/60 bg-sunken px-3 py-1.5 text-sm text-foreground"
                  >
                    {field}
                  </li>
                ))}
              </ul>
              <p className="mt-5 max-w-2xl text-sm leading-relaxed text-muted-foreground">
                Understanding and embedding are tracked separately, because they can fail
                separately. A document whose profile is still being written is shown as exactly
                that, rather than as finished.
              </p>
            </div>
          </LandingItem>
        </div>
      </LandingSection>

      {/* ---- 05 ---------------------------------------------------- */}
      <LandingSection
        index="05"
        eyebrow="Knowledge retrieval"
        heading="Retrieval is a filter, not a search box."
        standfirst={
          <>
            Semantic search gathers candidates. When available, a reranker scores them
            against the question and a relevance gate applies the configured threshold.
            If scoring is unavailable, the run records that fallback rather than implying
            that every passage passed a relevance score check.
          </>
        }
      >
        <div className="flex flex-col gap-14">
          <FlowChain
            align="start"
            nodes={[
              { label: "Configured embeddings", note: "Compatible passage and query space" },
              { label: "Semantic retrieval", note: "Nearest candidates" },
              { label: "Optional reranking", note: "Retrieval order if unavailable", tone: "ai" },
              { label: "Relevance gate", note: "Applied when scores are available", tone: "primary" },
            ]}
          />
          <RetrievalNarrowing />
        </div>
      </LandingSection>

      {/* ---- 06 ---------------------------------------------------- */}
      <LandingSection
        id="grounding"
        index="06"
        eyebrow="Grounded intelligence"
        heading="An answer is only as good as the evidence it was allowed to use."
        standfirst={
          <>
            In the grounded-answer path, the synthesis model receives the selected passages
            and citation identifiers are checked against that supplied evidence. Other
            outcomes are labelled separately when evidence cannot support the request.
          </>
        }
      >
        <div className="flex flex-col gap-12">
          <FlowChain
            align="start"
            nodes={[
              { label: "Question" },
              { label: "Evidence", note: "Selected passages, when available" },
              { label: "Synthesis", note: "Answer written from evidence", tone: "ai" },
              { label: "Citation validation", note: "Checked against what was supplied" },
              { label: "Grounded answer", tone: "primary" },
            ]}
          />

          <motion.div
            initial="hidden"
            whileInView="visible"
            viewport={inViewOnce}
            variants={sectionReveal}
            className="grid gap-5 md:grid-cols-2"
          >
            <motion.div
              variants={sectionItem}
              className="rounded-xl border border-border bg-card p-7"
            >
              <h3 className="font-display text-xl tracking-tight text-foreground">
                Citation identifiers are checked against supplied evidence
              </h3>
              <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
                Every citation the model returns is matched back to a passage that was actually
                supplied to it. A citation pointing at something that was never in the evidence
                is rejected from the accepted citation list. The answer can still disclose
                unverified markers; identifier validation alone does not verify every claim
                or plotted value.
              </p>
            </motion.div>

            <motion.div
              variants={sectionItem}
              className="rounded-xl border border-border bg-card p-7"
            >
              <h3 className="font-display text-xl tracking-tight text-foreground">
                Every answer says where it came from
              </h3>
              <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
                AIKDAP grounds answers in your documents first, searches the web when they fall
                short, and clearly labels anything answered from general knowledge &mdash; that
                answer never carries a citation it does not have.
              </p>
            </motion.div>
          </motion.div>
        </div>
      </LandingSection>

      {/* ---- 07 ---------------------------------------------------- */}
      <LandingSection
        id="resilience"
        index="07"
        eyebrow="Provider resilience"
        heading="Configured fallback providers can keep research moving."
        standfirst={
          <>
            Synthesis talks to an internal gateway rather than to a vendor. The gateway holds an
            ordered chain of providers and can try the next configured option when one cannot
            serve a request. The run records the provider used or reports failure if the chain
            is exhausted.
          </>
        }
      >
        <ProviderRouting />
      </LandingSection>

      {/* ---- 08 ---------------------------------------------------- */}
      <LandingSection
        index="08"
        eyebrow="Research workspace"
        heading="The workspace shows its working."
        standfirst={
          <>
            Every run is inspectable after the fact. The stages it executed, the passages that
            survived each of them, and available citation sources can be inspected alongside
            the answer. Supporting detail can be expanded when needed.
          </>
        }
      >
        <motion.ul
          initial="hidden"
          whileInView="visible"
          viewport={inViewOnce}
          variants={sectionReveal}
          className="grid gap-5 md:grid-cols-3"
        >
          {WORKSPACE_FEATURES.map(({ icon: Icon, title, body }) => (
            <motion.li
              key={title}
              variants={sectionItem}
              className="rounded-xl border border-border bg-card p-7"
            >
              <span className="flex h-10 w-10 items-center justify-center rounded-lg border border-ai/30 bg-ai/[0.08] text-ai">
                <Icon aria-hidden="true" className="h-4 w-4" />
              </span>
              <h3 className="mt-5 font-display text-lg tracking-tight text-foreground">{title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{body}</p>
            </motion.li>
          ))}
        </motion.ul>
      </LandingSection>

      {/* ---- 09 ---------------------------------------------------- */}
      <motion.section
        initial="hidden"
        whileInView="visible"
        viewport={inViewOnce}
        variants={sectionReveal}
        aria-label="Get started"
        className="relative border-t border-border/60 py-32 md:py-44"
      >
        <div className="mx-auto flex w-full max-w-6xl flex-col items-center px-6 text-center md:px-10">
          <motion.h2
            variants={sectionItem}
            className="max-w-4xl font-display text-statement text-foreground"
          >
            Turn information
            <br />
            into intelligence.
          </motion.h2>

          <motion.div variants={sectionItem} className="mt-12">
            <Link
              to="/login"
              className="group inline-flex items-center gap-3 rounded-xl bg-foreground px-8 py-4 font-display text-base font-medium tracking-tight text-background transition-colors duration-200 hover:bg-primary hover:text-primary-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
            >
              Enter AIKDAP
              <ArrowRight
                aria-hidden="true"
                className="h-4 w-4 transition-transform duration-200 group-hover:translate-x-1 motion-reduce:transition-none motion-reduce:group-hover:translate-x-0"
              />
            </Link>
          </motion.div>

          <motion.p
            variants={sectionItem}
            className="mt-10 max-w-md text-sm leading-relaxed text-muted-foreground"
          >
            Answers are grounded in your own documents. When the evidence isn&rsquo;t there,
            AIKDAP says so.
          </motion.p>
        </div>
      </motion.section>

      <LandingFooter />
    </>
  );
}
