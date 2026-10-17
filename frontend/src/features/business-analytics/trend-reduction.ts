/** Display-only sampling of an already validated, chronologically ordered trace.
 * Every retained value is an original point. No averaging/interpolation occurs. */
export function reduceTrend(values: (number | null)[], target = 1000): { indexes: number[]; method: "min_max_contiguous_buckets" | "full_gap_preservation"; bucketSize: number; reason?: string } {
  const full = () => values.map((_, index) => index);
  if (values.length <= target) return { indexes: full(), method: "full_gap_preservation", bucketSize: 1 };
  const mandatory = new Set<number>([0, values.length - 1]);
  const segments: { start: number; end: number }[] = [];
  let globalMin = -1; let globalMax = -1; let validCount = 0;
  for (let start = 0; start < values.length;) {
    const missing = values[start] === null;
    let end = start;
    while (end + 1 < values.length && (values[end + 1] === null) === missing) end++;
    mandatory.add(start); mandatory.add(end);
    if (!missing) {
      segments.push({ start, end });
      for (let index = start; index <= end; index++) {
        validCount++;
        if (globalMin < 0 || values[index]! < values[globalMin]!) globalMin = index;
        if (globalMax < 0 || values[index]! > values[globalMax]!) globalMax = index;
      }
    }
    start = end + 1;
  }
  if (globalMin >= 0) { mandatory.add(globalMin); mandatory.add(globalMax); }
  // ceil(segment length / width) can add one bucket per segment. Reserve
  // those slots as well as every mandatory gap boundary before choosing width.
  const bucketSlots = Math.floor((target - mandatory.size) / 2) - segments.length;
  if (mandatory.size > target || bucketSlots <= 0) return { indexes: full(), method: "full_gap_preservation", bucketSize: 1, reason: "Gap boundaries and extrema cannot fit the display target; the full accepted trace is retained." };
  const bucketSize = Math.max(1, Math.ceil(validCount / bucketSlots));
  const selected = new Set(mandatory);
  for (const segment of segments) {
    for (let start = segment.start; start <= segment.end; start += bucketSize) {
      const end = Math.min(segment.end, start + bucketSize - 1);
      let min = start; let max = start;
      for (let index = start + 1; index <= end; index++) {
        if (values[index]! < values[min]!) min = index;
        if (values[index]! > values[max]!) max = index;
      }
      selected.add(min); selected.add(max);
    }
  }
  const indexes = [...selected].sort((a, b) => a - b);
  if (indexes.length > target) return { indexes: full(), method: "full_gap_preservation", bucketSize: 1, reason: "The gap-preserving selection exceeds the display target; the full accepted trace is retained." };
  return { indexes, method: "min_max_contiguous_buckets", bucketSize };
}
