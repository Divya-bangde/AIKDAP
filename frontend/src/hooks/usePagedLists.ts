import { useInfiniteQuery } from "@tanstack/react-query";
import { allSettled } from "@/features/assets/asset-state";
import { listAssetsPage } from "@/services/assets";
import { listProjectsPage } from "@/services/projects";
import { listResearchRunsPage } from "@/services/research";
import { useAuthStore } from "@/store/auth-store";

export function usePagedProjects() {
  const sessionId = useAuthStore((state) => state.sessionId);
  const query = useInfiniteQuery({
    queryKey: ["projects", "pages", sessionId], initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => listProjectsPage(pageParam),
    getNextPageParam: (page) => page.next_cursor,
  });
  return { ...query, items: query.data?.pages.flatMap((page) => page.items) ?? [] };
}

export function usePagedAssets(projectId?: string) {
  const sessionId = useAuthStore((state) => state.sessionId);
  const query = useInfiniteQuery({
    queryKey: ["assets", "pages", projectId ?? "all", sessionId], initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => listAssetsPage(projectId, pageParam),
    getNextPageParam: (page) => page.next_cursor,
    refetchInterval: (query) => {
      if (query.state.error) return false;
      const items = query.state.data?.pages.flatMap((page) => page.items);
      return items && !allSettled(items) ? 3000 : false;
    },
  });
  return { ...query, items: query.data?.pages.flatMap((page) => page.items) ?? [] };
}

export function usePagedResearch(projectId?: string, groundingStatus?: string) {
  const sessionId = useAuthStore((state) => state.sessionId);
  const query = useInfiniteQuery({
    queryKey: ["research", "runs", "pages", projectId ?? "all", groundingStatus ?? "all", sessionId],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => listResearchRunsPage(projectId, pageParam, 50, undefined, groundingStatus),
    getNextPageParam: (page) => page.next_cursor,
  });
  return { ...query, items: query.data?.pages.flatMap((page) => page.items) ?? [] };
}
