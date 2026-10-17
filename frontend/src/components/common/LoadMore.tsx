import { Button } from "@/components/ui/button";

export function LoadMore({ query, label }: {
  query: { hasNextPage: boolean; isFetchingNextPage: boolean; isFetchNextPageError: boolean; fetchNextPage: () => Promise<unknown> };
  label: string;
}) {
  if (!query.hasNextPage && !query.isFetchNextPageError) return null;
  return <div className="flex flex-col items-start gap-2 py-3">
    {query.isFetchNextPageError && <p role="alert" className="text-sm text-destructive">Couldn't load more {label}. Your loaded items are still available.</p>}
    <Button type="button" variant="outline" disabled={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>
      {query.isFetchingNextPage ? "Loading…" : query.isFetchNextPageError ? `Retry loading ${label}` : `Load more ${label}`}
    </Button>
  </div>;
}
