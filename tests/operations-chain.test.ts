import { expect, it } from "vitest";
import {
  DeviceTask,
  photoTransfer,
  exportReady,
} from "../src/courier/task-state";
it("locks immediately and ignores repeated device callbacks", () => {
  const task = new DeviceTask();
  const first = task.start();
  expect(first).toBeDefined();
  expect(task.start()).toBeUndefined();
  expect(task.finish(first!)).toBe(true);
  expect(task.finish(first!)).toBe(false);
});
it("invalidates photo and position callbacks after identity change or removal", () => {
  const task = new DeviceTask();
  const first = task.start()!;
  task.cancel();
  expect(task.current(first)).toBe(false);
  expect(task.finish(first)).toBe(false);
  expect(task.start()).toBeDefined();
});
it("keeps transfer completion waiting for server confirmation and unknown totals indeterminate", () => {
  expect(photoTransfer(4)).toEqual({
    value: undefined,
    label: "正在上传照片…",
  });
  expect(photoTransfer(4, 4)).toEqual({
    value: 100,
    label: "照片已传输，等待服务端确认…",
  });
});
it("does not present accepted exports as ready", () => {
  expect(exportReady("QUEUED", "https://example.test/file")).toBe(false);
  expect(exportReady("PROCESSING", "https://example.test/file")).toBe(false);
  expect(exportReady("READY", "https://example.test/file")).toBe(true);
  expect(exportReady("READY", undefined)).toBe(false);
});
