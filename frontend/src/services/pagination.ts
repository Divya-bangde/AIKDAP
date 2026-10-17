import { assertSession, sessionEpoch } from "@/lib/session-lifecycle";

export interface Page<T> { items: T[]; next_cursor: string | null }

/** Internal selectors/toasts can collect compact pages without a hidden cap. */
export async function collectPages<T>(load: (cursor: string | null) => Promise<Page<T>>): Promise<T[]> {
  const epoch = sessionEpoch();
  const items: T[] = [];
  const seen = new Set<string>();
  let cursor: string | null = null;
  do {
    assertSession(epoch);
    const page = await load(cursor);
    assertSession(epoch);
    items.push(...page.items);
    cursor = page.next_cursor;
    if (cursor && seen.has(cursor)) throw new Error("Pagination cursor did not advance.");
    if (cursor) seen.add(cursor);
  } while (cursor);
  return items;
}

export function pageQuery(cursor?: string | null, limit = 50, filters: Record<string, string | undefined> = {}) {
  const query = new URLSearchParams({ limit: String(limit) });
  if (cursor) query.set("cursor", cursor);
  for (const [key, value] of Object.entries(filters)) if (value) query.set(key, value);
  return query.toString();
}
