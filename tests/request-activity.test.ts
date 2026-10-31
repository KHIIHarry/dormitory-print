import { describe, expect, it } from "vitest";
import { requestActivity } from "../src/lib/request-activity";

describe("foreground request activity", () => {
  it("stays active until all concurrent operations finish and ignores duplicate completion", () => {
    const changes: number[] = [];
    const unsubscribe = requestActivity.subscribe(() => changes.push(requestActivity.snapshot()));
    const finishFirst = requestActivity.begin();
    const finishSecond = requestActivity.begin();
    expect(requestActivity.snapshot()).toBe(2);
    finishFirst();
    finishFirst();
    expect(requestActivity.snapshot()).toBe(1);
    finishSecond();
    expect(requestActivity.snapshot()).toBe(0);
    expect(changes).toEqual([1, 2, 1, 0]);
    unsubscribe();
  });
});
