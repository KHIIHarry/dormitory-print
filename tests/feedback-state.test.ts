import { describe, expect, it } from "vitest";
import {
  ActionLock,
  LatestTask,
  progressValue,
  resourcePhase,
} from "../src/lib/feedback-state";

describe("async feedback ownership", () => {
  it("ignores a completed old request after replacement or identity reset", () => {
    const tasks = new LatestTask();
    const old = tasks.begin("orders");
    const other = tasks.begin("config");
    const current = tasks.begin("orders");
    expect(old.current()).toBe(false);
    expect(other.current()).toBe(true);
    expect(current.current()).toBe(true);
    tasks.invalidate();
    expect(current.current()).toBe(false);
    expect(other.current()).toBe(false);
  });
  it("locks before asynchronous work and releasing an old operation cannot unlock its replacement", () => {
    const locks = new ActionLock();
    const first = locks.acquire("/submit");
    expect(first).toBeTypeOf("function");
    expect(locks.acquire("/submit")).toBeUndefined();
    locks.clear();
    const second = locks.acquire("/submit");
    first?.();
    expect(locks.has("/submit")).toBe(true);
    second?.();
    expect(locks.has("/submit")).toBe(false);
  });
  it("uses unknown progress for missing, zero, negative or invalid totals and clamps real progress", () => {
    expect(progressValue(20, undefined)).toBeUndefined();
    expect(progressValue(20, 0)).toBeUndefined();
    expect(progressValue(NaN, 50)).toBeUndefined();
    expect(progressValue(10, Infinity)).toBeUndefined();
    expect(progressValue(120, 100)).toBe(100);
    expect(progressValue(-20, 100)).toBe(0);
    expect(progressValue(25, 100)).toBe(25);
  });
  it("distinguishes initial error, refresh error and successful empty data", () => {
    expect(resourcePhase({ loading: true, hasData: false, error: "" })).toBe(
      "initial",
    );
    expect(resourcePhase({ loading: true, hasData: true, error: "" })).toBe(
      "refreshing",
    );
    expect(
      resourcePhase({ loading: false, hasData: false, error: "network" }),
    ).toBe("error");
    expect(
      resourcePhase({ loading: false, hasData: true, error: "network" }),
    ).toBe("refresh-error");
    expect(
      resourcePhase({ loading: false, hasData: true, error: "", empty: true }),
    ).toBe("empty");
  });
});
