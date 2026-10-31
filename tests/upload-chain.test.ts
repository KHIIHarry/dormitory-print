import type { PrintFile } from "../src/lib/types";
import { describe, expect, it } from "vitest";
import {
  uploadProgress,
  retryStage,
  UploadOperations,
  interruptUploads,
  a4Attempt,
} from "../src/lib/upload-chain";
describe("upload lifecycle", () => {
  it("keeps unknown totals indeterminate and sent bytes awaiting response", () => {
    expect(uploadProgress({ loaded: 12 })).toEqual({
      uploadPhase: "TRANSFERRING",
      progress: undefined,
    });
    expect(uploadProgress({ loaded: 12, total: 12 })).toEqual({
      uploadPhase: "AWAITING_RESPONSE",
      progress: 100,
    });
  });
  it("reuploads originals when no server asset exists", () => {
    expect(retryStage({ assetId: "" })).toBe("UPLOAD");
    expect(retryStage({ assetId: "accepted" })).toBe("PROCESSING");
  });
  it("invalidates deleted, retried, identity-cleared and unmounted callbacks", () => {
    const jobs = new UploadOperations();
    const deleted = jobs.start("a");
    jobs.cancel("a");
    expect(deleted.current()).toBe(false);
    expect(deleted.signal.aborted).toBe(true);
    const old = jobs.start("a");
    const fresh = jobs.start("a");
    expect(old.current()).toBe(false);
    expect(fresh.current()).toBe(true);
    jobs.clear();
    expect(fresh.current()).toBe(false);
  });
  it("allows only one outstanding poll per asset", () => {
    const jobs = new UploadOperations();
    expect(jobs.poll("a")).toBeTruthy();
    expect(jobs.poll("a")).toBeUndefined();
    jobs.cancel("a");
    expect(jobs.poll("a")).toBeTruthy();
  });
});

describe("retained upload work", () => {
  it("turns interrupted client uploads into retryable uncertainty while retaining server work", () => {
    const pending = {
      id: "queued",
      status: "UPLOADING",
      assetId: "",
      uploadRequestKey: "stable",
      localFile: { name: "original.pdf" },
    } as unknown as PrintFile;
    const accepted = { ...pending, id: "accepted", assetId: "server" };
    const result = interruptUploads([pending, accepted]);
    expect(result[0]).toMatchObject({
      status: "FAILED",
      uploadRequestKey: "stable",
      localFile: pending.localFile,
    });
    expect(result[0].error).toContain("待核实");
    expect(result[1]).toMatchObject({
      status: "PROCESSING",
      assetId: "server",
    });
  });
  it("retains identical A4 request bytes and key after response loss and remount", () => {
    const original = {
      id: "image",
      status: "READY",
      assetId: "image-server",
    } as PrintFile;
    const first = a4Attempt(original);
    const bytes = { name: "image-a4.pdf" } as File;
    const retained = {
      ...original,
      a4UploadAttempt: { ...first, file: bytes },
    };
    const [interrupted] = interruptUploads([retained]);
    expect(interrupted.status).toBe("READY");
    expect(a4Attempt(interrupted)).toEqual({ key: first.key, file: bytes });
    expect(interrupted.error).toContain("待核实");
  });
});
