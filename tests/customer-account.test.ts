import { describe, expect, it } from "vitest";
import {
  consumeFragment,
  readFragmentToken,
  singleFlight,
  safeInviteeLabel,
  couponTab,
  safeCustomerReturn,
} from "../src/customer/account/security";

describe("customer security boundaries", () => {
  it("takes a fragment secret and removes it without changing the query", () => {
    const replaced: string[] = [];
    const location = {
      hash: "#token=private-value",
      pathname: "/u/account/verify",
      search: "?mode=emailchange",
    };
    expect(consumeFragment(location, (url) => replaced.push(url))).toBe(
      "private-value",
    );
    expect(replaced).toEqual(["/u/account/verify?mode=emailchange"]);
  });
  it("keeps pure fragment reads stable across StrictMode double initialization", () => {
    const location = {
      hash: "#token=private-value",
      pathname: "/u/account/reset",
      search: "",
    };
    const firstInitialization = readFragmentToken(location);
    const secondInitialization = readFragmentToken(location);
    expect(firstInitialization).toBe("private-value");
    expect(secondInitialization).toBe(firstInitialization);
    expect(location.hash).toBe("#token=private-value");
    const replace = () => {
      location.hash = "";
    };
    consumeFragment(location, replace);
    consumeFragment(location, replace);
    expect(location.hash).toBe("");
    expect(firstInitialization).toBe("private-value");
  });
  it("deduplicates concurrent exchange operations and permits retry after failure", async () => {
    const run = singleFlight<string>();
    let calls = 0;
    let release!: (value: string) => void;
    const first = run("secret", () => {
      calls++;
      return new Promise<string>((resolve) => {
        release = resolve;
      });
    });
    const second = run("secret", async () => {
      calls++;
      return "wrong";
    });
    release("allowed");
    expect(await first).toBe("allowed");
    expect(await second).toBe("allowed");
    expect(calls).toBe(1);
    await expect(
      run("retry", async () => {
        throw new Error("offline");
      }),
    ).rejects.toThrow("offline");
    expect(await run("retry", async () => "retried")).toBe("retried");
  });
  it("never renders unmasked invitee email or phone", () => {
    expect(safeInviteeLabel("alice@example.com")).toBe("已邀请用户");
    expect(safeInviteeLabel("13812345678")).toBe("已邀请用户");
    expect(safeInviteeLabel("a***@e***.com")).toBe("a***@e***.com");
    expect(safeInviteeLabel("alice@example.com ***")).toBe("已邀请用户");
    expect(safeInviteeLabel("13812345678 ***")).toBe("已邀请用户");
    expect(safeInviteeLabel("张三")).toBe("已邀请用户");
  });
  it("keeps reserved and frozen coupon dimensions separate", () => {
    expect(couponTab("RESERVED")).toBe("RESERVED");
    expect(couponTab("REVOKED")).toBe("INACTIVE");
    expect(couponTab("EXPIRED")).toBe("INACTIVE");
  });
  it("returns only to a local customer route after login", () => {
    expect(safeCustomerReturn("/u/review?step=confirm")).toBe(
      "/u/review?step=confirm",
    );
    expect(safeCustomerReturn("https://other.example/u/")).toBe("/u/account");
    expect(safeCustomerReturn("//other.example/u/")).toBe("/u/account");
    expect(safeCustomerReturn("/admin/orders")).toBe("/u/account");
  });
});
