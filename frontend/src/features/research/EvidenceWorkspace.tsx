import { useMemo, useState } from "react";
import { motion } from "motion/react";
import {
  AlertOctagon,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  FileQuestion,
  FileText,
  HelpCircle,
  Link2,
  MessageSquareQuote,
} from "lucide-react";

import { StatusBadge } from "@/components/common/StatusBadge";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardTitle } from "@/components/ui/card";
import { cardTintFor, EvidenceChip } from "@/features/research/ClaimEvidencePanel";
import { fadeUp } from "@/lib/motion";
import { cn } from "@/lib/utils";
import type { Citation } from "@/types/citation";
import type { components } from "@/types/api";

type VerifiedClaim = components["schemas"]["VerifiedClaimRead"];

/** One icon per `verdict`, always paired with its text label -- the
 * point of this whole phase is that these four outcomes never lean on
 * badge color as the only signal. `evidence_state` keeps the second
 * `StatusBadge` it already had (Sprint 16 Phase 8.7/8.8); this icon
 * answers the coarser, more load-bearing question ("was this claim
 * checked, and how") at a glance, in the tree and in the detail panel
 * alike. */
const VERDICT_ICON: Record<VerifiedClaim["verdict"], typeof CheckCircle2> = {
  supported: CheckCircle2,
  contradicted: AlertOctagon,
  unverifiable: HelpCircle,
  insufficient_evidence: FileQuestion,
};

const VERDICT_ICON_TONE: Record<VerifiedClaim["verdict"], string> = {
  supported: "text-success",
  contradicted: "text-destructive",
  unverifiable: "text-warning",
  insufficient_evidence: "text-warning",
};

/** A disclosure row shared by every tree level -- a claim, an evidence
 * item -- so expand/collapse behaves identically at every depth rather
 * than each level reinventing its own toggle. */
function TreeRow({
  depth,
  expanded,
  onToggleExpanded,
  selected,
  onSelect,
  icon,
  children,
}: {
  depth: number;
  expanded?: boolean;
  onToggleExpanded?: () => void;
  selected?: boolean;
  onSelect?: () => void;
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  const Chevron = expanded ? ChevronDown : ChevronRight;
  return (
    <div
      className={cn(
        "flex items-center gap-1.5 rounded-md py-1.5 pr-2 text-sm transition-colors",
        selected && "bg-accent text-accent-foreground",
        !selected && onSelect && "hover:bg-secondary",
      )}
      style={{ paddingLeft: `${depth * 1.25 + 0.375}rem` }}
    >
      {onToggleExpanded ? (
        <button
          type="button"
          onClick={onToggleExpanded}
          className="shrink-0 rounded p-0.5 text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-label={expanded ? "Collapse" : "Expand"}
        >
          <Chevron className="h-3.5 w-3.5" />
        </button>
      ) : (
        <span className="w-4 shrink-0" />
      )}
      {icon}
      {onSelect ? (
        <button
          type="button"
          onClick={onSelect}
          className="min-w-0 flex-1 truncate text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {children}
        </button>
      ) : (
        <div className="min-w-0 flex-1 truncate">{children}</div>
      )}
    </div>
  );
}

/** One evidence item's chunk reference + snippet, the same fields
 * `EvidenceDrawer` shows in full -- shown inline here so a contradiction
 * can be read without opening two drawers back to back. */
function EvidenceCard({
  citation,
  tone,
}: {
  citation: Citation;
  tone: "neutral" | "destructive";
}) {
  return (
    <div
      className={cn(
        "flex min-w-0 flex-col gap-2 rounded-lg border p-3",
        tone === "destructive" ? "border-destructive/30 bg-destructive/5" : "border-border bg-sunken",
      )}
    >
      <p className="truncate text-xs font-medium text-foreground" title={citation.title}>
        {citation.title ?? citation.file_name ?? "Untitled source"}
      </p>
      {citation.reference && (
        <p className="truncate font-mono text-xs text-muted-foreground" title={citation.reference}>
          {citation.reference}
        </p>
      )}
      {citation.snippet && (
        <p className="line-clamp-4 text-xs leading-relaxed text-foreground">{citation.snippet}</p>
      )}
    </div>
  );
}

/** Read-only tree + detail view over one run's claims and citations
 * (Sprint 16 Phase 8.10) -- a second, structural presentation of
 * exactly the data `ClaimEvidencePanel` already renders as a flat list,
 * never a second source of truth. No new API shape: everything here
 * comes from `ResearchRunDetail.claims`/`citations`, the same contract
 * the panel reads.
 *
 * Deliberately a tree, not a graph: LEFT is Question -> Claims ->
 * Evidence -> Source, each level collapsible; RIGHT is the selected
 * claim's full detail. Evidence cited by more than one claim is marked
 * with a badge on each occurrence ("also cited by ..."), never drawn as
 * an edge between nodes -- there is no graph renderer, node-position
 * layout, or edge model here, on purpose (no graph library, no graph
 * DB, per this phase's scope).
 *
 * `SUPPORTING`/`DERIVED` `evidence_state` values do not appear anywhere
 * in this component: the backend always sets `attributed_to_primary =
 * true` (Sprint 16), so `evidence_state_from_claim_verification` can
 * only ever produce `verified`, `contradicted`, or `unknown` in
 * practice. Building a tree node for a state nothing can produce would
 * be exactly the kind of fabricated coverage this phase forbids. */
export function EvidenceWorkspace({
  query,
  claims,
  citations,
  onSelectCitation,
}: {
  query: string;
  claims: VerifiedClaim[];
  citations: Citation[];
  onSelectCitation: (citation: Citation, index: number) => void;
}) {
  const [selectedClaim, setSelectedClaim] = useState<number | null>(claims.length > 0 ? 0 : null);
  const [expandedClaims, setExpandedClaims] = useState<Set<number>>(
    new Set(claims.length > 0 ? [0] : []),
  );

  const citationByIndex = useMemo(
    () => new Map(citations.map((citation, index) => [citation.id, { citation, index }])),
    [citations],
  );

  // Which OTHER claims also cite a given evidence id -- the shared-
  // evidence badge, computed once rather than re-scanned per row.
  const claimsCitingId = useMemo(() => {
    const map = new Map<string, number[]>();
    claims.forEach((claim, claimIndex) => {
      for (const id of claim.source_reference_ids ?? []) {
        map.set(id, [...(map.get(id) ?? []), claimIndex]);
      }
    });
    return map;
  }, [claims]);

  if (claims.length === 0) return null;

  const toggleClaim = (index: number) => {
    setExpandedClaims((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  };

  const selectClaim = (index: number) => {
    setSelectedClaim(index);
    setExpandedClaims((prev) => new Set(prev).add(index));
  };

  const active = selectedClaim !== null ? claims[selectedClaim] : null;
  // For a CONTRADICTED claim, the evidence that actually drove the
  // verdict (`matched_evidence_ids`) is what conflicts -- shown side by
  // side rather than the full `source_reference_ids` list, which may
  // also include evidence the check never found a problem with.
  const conflicting =
    active?.verdict === "contradicted"
      ? (active.matched_evidence_ids ?? []).map((id) => citationByIndex.get(id)).filter(Boolean)
      : [];

  return (
    <Card>
      {/* Collapsed by default: supporting detail, not the answer. */}
      <details className="group">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-5 py-4 hover:bg-sunken/50 [&::-webkit-details-marker]:hidden">
          <span>
            <CardTitle className="text-sm">Evidence Workspace</CardTitle>
            <span className="text-xs text-muted-foreground">
              {claims.length} claim{claims.length === 1 ? "" : "s"} checked against cited evidence
            </span>
          </span>
          <ChevronDown aria-hidden="true" className="h-4 w-4 text-muted-foreground transition-transform group-open:rotate-180" />
        </summary>
      <CardContent className="pt-0">
        <motion.div
          initial="hidden"
          animate="visible"
          variants={fadeUp}
          className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,280px)_1fr]"
        >
          {/* LEFT: the tree. */}
          <nav
            aria-label="Claim and evidence tree"
            className="flex flex-col gap-0.5 overflow-y-auto rounded-lg bg-sunken p-1.5 lg:max-h-[20rem]"
          >
            <TreeRow depth={0} icon={<MessageSquareQuote className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />}>
              <span className="truncate text-xs font-medium uppercase tracking-wide text-muted-foreground" title={query}>
                {query}
              </span>
            </TreeRow>

            {claims.map((claim, claimIndex) => {
              const Icon = VERDICT_ICON[claim.verdict];
              const claimExpanded = expandedClaims.has(claimIndex);
              const evidenceIds = claim.source_reference_ids ?? [];
              const unresolvedIds = claim.unresolved_citation_ids ?? [];

              return (
                <div key={claimIndex}>
                  <TreeRow
                    depth={1}
                    expanded={claimExpanded}
                    onToggleExpanded={() => toggleClaim(claimIndex)}
                    selected={selectedClaim === claimIndex}
                    onSelect={() => selectClaim(claimIndex)}
                    icon={<Icon className={cn("h-3.5 w-3.5 shrink-0", VERDICT_ICON_TONE[claim.verdict])} aria-hidden="true" />}
                  >
                    {claim.claim_text}
                  </TreeRow>

                  {claimExpanded &&
                    evidenceIds.map((id) => {
                      const match = citationByIndex.get(id);
                      const sharedWith = (claimsCitingId.get(id) ?? []).filter((i) => i !== claimIndex);
                      return (
                        // No `onSelect` here (which would render the row
                        // itself as a <button>): `EvidenceChip` below is
                        // already a <button> in its "resolved" state, and
                        // a <button> cannot legally nest inside another
                        // one -- browsers do render it, but inconsistently,
                        // and screen readers announce it wrong.
                        <TreeRow key={id} depth={2}>
                          <span className="flex flex-wrap items-center gap-1.5">
                            <EvidenceChip
                              id={id}
                              state={match ? "resolved" : "unavailable"}
                              onOpen={match ? () => onSelectCitation(match.citation, match.index) : undefined}
                            />
                            {match?.citation.title && (
                              <span className="truncate text-xs text-muted-foreground">
                                {match.citation.title}
                              </span>
                            )}
                            {sharedWith.length > 0 && (
                              <Badge variant="outline" className="text-xs">
                                also cited by claim {sharedWith.map((i) => i + 1).join(", ")}
                              </Badge>
                            )}
                          </span>
                        </TreeRow>
                      );
                    })}
                  {claimExpanded &&
                    unresolvedIds.map((id) => (
                      <TreeRow key={id} depth={2}>
                        <EvidenceChip id={id} state="not_supplied" />
                      </TreeRow>
                    ))}
                  {claimExpanded && evidenceIds.length === 0 && unresolvedIds.length === 0 && (
                    <TreeRow depth={2}>
                      <span className="text-xs italic text-muted-foreground">No evidence was cited</span>
                    </TreeRow>
                  )}
                </div>
              );
            })}
          </nav>

          {/* RIGHT: the selected claim's full detail. */}
          <div
            className={cn(
              "rounded-lg border p-4 transition-colors",
              active ? cardTintFor(active.verdict) : "border-border",
            )}
          >
            {!active ? (
              <p className="text-sm text-muted-foreground">Select a claim from the tree to see its evidence.</p>
            ) : (
              <div className="flex flex-col gap-4">
                <div className="flex items-start justify-between gap-3">
                  <p className="text-sm font-medium leading-relaxed text-foreground">{active.claim_text}</p>
                  <span
                    className="flex shrink-0 items-center gap-1 whitespace-nowrap text-xs font-medium text-muted-foreground"
                    title={
                      active.attributed_to_primary
                        ? "Attributed to the primary source"
                        : "Attributed to a supporting source"
                    }
                  >
                    {active.attributed_to_primary ? (
                      <FileText className="h-3 w-3" aria-hidden="true" />
                    ) : (
                      <Link2 className="h-3 w-3" aria-hidden="true" />
                    )}
                    {active.attributed_to_primary ? "Primary" : "Supporting"}
                  </span>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  {(() => {
                    const Icon = VERDICT_ICON[active.verdict];
                    return <Icon className={cn("h-4 w-4", VERDICT_ICON_TONE[active.verdict])} aria-hidden="true" />;
                  })()}
                  <StatusBadge domain="claimVerdict" value={active.verdict} />
                  <StatusBadge domain="evidenceState" value={active.evidence_state} />
                </div>

                {/* "Verified" names what the check found -- not a claim
                 * of truth. Shown only for the one verdict it could be
                 * misread for, right where a reader is looking at it. */}
                {active.verdict === "supported" && (
                  <p className="text-xs text-muted-foreground">
                    Supported by the evidence available to this run -- not an independent
                    confirmation that the claim is true.
                  </p>
                )}

                {active.reason && (
                  <p className="rounded-md bg-sunken p-2.5 text-xs leading-relaxed text-muted-foreground">
                    {active.reason}
                  </p>
                )}

                {active.verdict === "contradicted" && conflicting.length > 0 ? (
                  <div>
                    <p className="text-label mb-2 uppercase text-muted-foreground">
                      Conflicting evidence ({conflicting.length})
                    </p>
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                      {conflicting.map((entry) => (
                        <EvidenceCard key={entry!.citation.id} citation={entry!.citation} tone="destructive" />
                      ))}
                    </div>
                  </div>
                ) : (
                  (active.source_reference_ids ?? []).length > 0 && (
                    <div>
                      <p className="text-label mb-2 uppercase text-muted-foreground">Cited evidence</p>
                      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                        {(active.source_reference_ids ?? []).map((id) => {
                          const match = citationByIndex.get(id);
                          return match ? (
                            <EvidenceCard key={id} citation={match.citation} tone="neutral" />
                          ) : (
                            <div
                              key={id}
                              className="flex min-w-0 flex-col gap-1 rounded-lg border border-dashed border-muted-foreground/40 p-3"
                            >
                              <EvidenceChip id={id} state="unavailable" />
                              <p className="text-xs text-muted-foreground">
                                Supplied to the model, but not returned by this run's record.
                              </p>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )
                )}

                {(active.unresolved_citation_ids ?? []).length > 0 && (
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-label uppercase text-muted-foreground">Never supplied</span>
                    {(active.unresolved_citation_ids ?? []).map((id) => (
                      <EvidenceChip key={id} id={id} state="not_supplied" />
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </motion.div>
      </CardContent>
      </details>
    </Card>
  );
}
