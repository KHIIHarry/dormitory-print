import { describe, expect, it } from "vitest";
import { verificationCountdown } from "../src/admin/growth/logic";

const now = Date.parse("2026-11-01T00:00:00.000Z");

describe("unverified account countdown", () => {
  it("formats the remaining window to whole minutes", () => {
    expect(verificationCountdown("2026-11-02T00:00:00.000Z", now)).toBe(
      "24 小时 0 分",
    );
    expect(verificationCountdown("2026-11-01T23:59:30.000Z", now)).toBe(
      "23 小时 59 分",
    );
    expect(verificationCountdown("2026-11-01T00:59:30.000Z", now)).toBe(
      "59 分",
    );
  });

  it("never shows a negative window and keeps a clear expiry state", () => {
    expect(verificationCountdown("2026-11-01T00:00:00.000Z", now)).toBe(
      "已到期，等待系统清理",
    );
    expect(verificationCountdown("2026-10-31T23:59:30.000Z", now)).toBe(
      "已到期，等待系统清理",
    );
    expect(verificationCountdown("2026-11-01T00:00:30.000Z", now)).toBe(
      "不足 1 分钟",
    );
  });

  it("has no countdown for verified or malformed records", () => {
    expect(verificationCountdown(null, now)).toBeNull();
    expect(verificationCountdown("", now)).toBeNull();
    expect(verificationCountdown("not-a-date", now)).toBeNull();
  });
});
