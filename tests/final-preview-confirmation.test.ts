import { describe, expect, it } from "vitest";
import * as checkout from "../src/customer/checkout";

const artifact = { id: "artifact-current", sha256: "hash-current" };
describe("最终PDF确认边界", () => {
  it("没有选择具体文件时不能确认", () => {
    expect(checkout.canAcknowledgeArtifact(null, [artifact], true)).toBe(false);
  });
  it("未渲染、文件已替换或哈希已变化时不能确认", () => {
    const canConfirm = checkout.canAcknowledgeArtifact;
    expect(canConfirm(artifact, [artifact], false)).toBe(false);
    expect(canConfirm(artifact, [], true)).toBe(false);
    expect(
      canConfirm(artifact, [{ ...artifact, sha256: "new-hash" }], true),
    ).toBe(false);
    expect(canConfirm(artifact, [artifact], true)).toBe(true);
  });
  it("多任务需逐个确认，旧任务或重复ID不能使提交解锁", () => {
    const allConfirmed = checkout.allArtifactsAcknowledged;
    const next = { id: "second", sha256: "hash-2" };
    expect(allConfirmed([], [])).toBe(false);
    expect(allConfirmed([artifact, next], [artifact.id])).toBe(false);
    expect(allConfirmed([artifact, next], [artifact.id, "old-id"])).toBe(false);
    expect(allConfirmed([artifact, next], [artifact.id, artifact.id])).toBe(
      false,
    );
    expect(allConfirmed([artifact, next], [artifact.id, next.id])).toBe(true);
  });
});
