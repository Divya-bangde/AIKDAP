import { describe, expect, it } from "vitest";

import { isSettled } from "@/features/assets/asset-state";
import { aiProfile, makeAsset } from "@/test/fixtures";

describe("isSettled", () => {
  it("settles a generated (report) asset once processing_status is completed, regardless of ai_profile", () => {
    const asset = makeAsset({
      source: "generated",
      processing_status: "completed",
      ai_profile: aiProfile({ status: "pending", embedding_status: "pending" }),
    });

    expect(isSettled(asset)).toBe(true);
  });

  it("does not settle a generated asset still running", () => {
    const asset = makeAsset({ source: "generated", processing_status: "running" });

    expect(isSettled(asset)).toBe(false);
  });

  it("still waits on ai_profile/embedding for an uploaded asset", () => {
    const asset = makeAsset({
      source: "upload",
      processing_status: "completed",
      ai_profile: aiProfile({ status: "pending", embedding_status: "pending" }),
    });

    expect(isSettled(asset)).toBe(false);
  });
});
