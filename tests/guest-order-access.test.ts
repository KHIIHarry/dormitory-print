import { expect, it } from "vitest";
import {
  guestAccessRemaining,
  guestAccessStatus,
  ownsAccountOrder,
} from "../src/customer/guest-order-policy";

it("counts down using elapsed local time from the server sample", () => {
  const sample = {
    status: "ACTIVE" as const,
    expiresAt: "2026-11-18T08:00:00Z",
    serverNow: "2026-11-17T07:30:00Z",
  };
  expect(guestAccessRemaining(sample, 0)).toBe("剩余 1 天 0 小时");
  expect(guestAccessRemaining(sample, 24 * 3600000)).toBe("不足 1 小时");
  expect(guestAccessStatus(sample, 24.5 * 3600000)).toBe("EXPIRED");
  expect(guestAccessStatus({ ...sample, status: "CLOSED" }, 0)).toBe("CLOSED");
});
it("only the matching registered owner bypasses guest access checks", () => {
  expect(
    ownsAccountOrder({ ownerAccountId: "a" }, {
      authKind: "REGISTERED",
      account: { id: "a" },
    } as any),
  ).toBe(true);
  expect(
    ownsAccountOrder({ ownerAccountId: "a" }, {
      authKind: "REGISTERED",
      account: { id: "b" },
    } as any),
  ).toBe(false);
  expect(
    ownsAccountOrder({ accessKind: "GUEST_OWNER" }, {
      authKind: "ANONYMOUS",
    } as any),
  ).toBe(false);
});
