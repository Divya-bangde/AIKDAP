import { useQuery } from "@tanstack/react-query";

import { getProfile } from "@/services/analytics";

export function DatasetProfile({ datasetId }: { datasetId: string }) {
  const profileQuery = useQuery({
    queryKey: ["analytics-profile", datasetId],
    queryFn: () => getProfile(datasetId),
  });

  const profile = profileQuery.data;
  if (!profile) return null;

  return (
    <details open className="rounded-card bg-card p-6 shadow-subtle">
      <summary className="cursor-pointer text-sm font-medium">
        {profile.row_count} rows · {profile.columns.length} columns
        {profile.truncated && <span className="text-muted-foreground"> (truncated)</span>}
      </summary>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead>
            <tr>
              <th className="border-b border-border px-2 py-1 font-medium">Name</th>
              <th className="border-b border-border px-2 py-1 font-medium">Kind</th>
              <th className="border-b border-border px-2 py-1 font-medium">Null %</th>
              <th className="border-b border-border px-2 py-1 font-medium">Samples</th>
            </tr>
          </thead>
          <tbody>
            {profile.columns.map((col) => (
              <tr key={col.name}>
                <td className="border-b border-border px-2 py-1">{col.name}</td>
                <td className="border-b border-border px-2 py-1">{col.kind}</td>
                <td className="border-b border-border px-2 py-1">{col.null_pct}%</td>
                <td className="border-b border-border px-2 py-1">{col.samples.map(String).join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}
