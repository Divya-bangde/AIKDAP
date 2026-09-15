import { afterEach, describe, expect, it, vi } from "vitest";

import * as client from "@/services/client";
import * as reportsService from "@/services/reports";

vi.mock("@/services/client");

describe("reports service", () => {
  afterEach(() => vi.restoreAllMocks());

  it("posts the chosen kind to the project's synopsis endpoint", async () => {
    vi.mocked(client.request).mockResolvedValue({ asset_id: "a1", status: "pending" });

    const result = await reportsService.generateSynopsis("p1", "study_summary");

    expect(client.request).toHaveBeenCalledWith("/api/v1/projects/p1/reports/synopsis", {
      method: "POST",
      body: { kind: "study_summary" },
    });
    expect(result).toEqual({ asset_id: "a1", status: "pending" });
  });

  it("requests the chosen format from the download endpoint", async () => {
    const blob = new Blob(["content"]);
    vi.mocked(client.requestBlob).mockResolvedValue(blob);
    const originalCreateObjectURL = URL.createObjectURL;
    const originalRevokeObjectURL = URL.revokeObjectURL;
    URL.createObjectURL = vi.fn(() => "blob:mock");
    URL.revokeObjectURL = vi.fn();

    await reportsService.downloadReport("a1", "pdf", "Project Synopsis.pdf");

    expect(client.requestBlob).toHaveBeenCalledWith("/api/v1/reports/a1/download?format=pdf");
    expect(URL.createObjectURL).toHaveBeenCalledWith(blob);

    URL.createObjectURL = originalCreateObjectURL;
    URL.revokeObjectURL = originalRevokeObjectURL;
  });

  it("fetches a report with its steps", async () => {
    vi.mocked(client.request).mockResolvedValue({ id: "a1", steps: [] });

    await reportsService.getReport("a1");

    expect(client.request).toHaveBeenCalledWith("/api/v1/reports/a1");
  });
});
