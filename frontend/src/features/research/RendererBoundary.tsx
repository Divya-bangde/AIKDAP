import { Component, lazy, Suspense, useMemo, useState, type ComponentType, type ErrorInfo, type ReactNode } from "react";

class LocalBoundary extends Component<{ children: ReactNode; label: string; retry: () => void; retried: boolean }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(_error: Error, _info: ErrorInfo) { /* The local status is the actionable diagnostic. */ }
  render() {
    if (!this.state.failed) return this.props.children;
    return <div role="status" className="rounded border border-border p-3 text-sm"><p>The {this.props.label} renderer could not load or render. The answer, context and data/source alternatives remain available.</p><button type="button" disabled={this.props.retried} onClick={this.props.retry} className="mt-2 rounded border border-border px-3 py-2 text-xs">{this.props.retried ? "Renderer retry used; reload the page to try again" : `Retry ${this.props.label} renderer`}</button></div>;
  }
}

/** Recreate the lazy component on the single permitted retry; a rejected
 * React.lazy cache must not make every retry repeat the first rejection. */
export function LazyRenderer<P extends object>({ load, rendererProps, label }: { load: () => Promise<{ default: ComponentType<P> }>; rendererProps: P; label: string }) {
  const [attempt, setAttempt] = useState(0);
  const Renderer = useMemo(() => lazy(load) as unknown as ComponentType<P>, [load, attempt]);
  return <LocalBoundary key={attempt} label={label} retried={attempt > 0} retry={() => setAttempt(1)}><Suspense fallback={<p className="text-sm text-muted-foreground">Loading {label}…</p>}><Renderer {...rendererProps} /></Suspense></LocalBoundary>;
}

export function RenderGuard({ children, label }: { children: ReactNode; label: string }) {
  const [attempt, setAttempt] = useState(0);
  return <LocalBoundary key={attempt} label={label} retried={attempt > 0} retry={() => setAttempt(1)}>{children}</LocalBoundary>;
}
