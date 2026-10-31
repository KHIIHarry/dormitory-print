import { expect, it } from "vitest";
import { formatDisplayTime, formatFileSize } from "../src/lib/display-format";
it("小文件按实际字节或 KB 显示而不是 0.0 MB", () => {
  expect(formatFileSize(1024)).toBe("1.0 KB");
  expect(formatFileSize(30000)).toBe("29 KB");
  expect(formatFileSize(0)).toBe("0 B");
  expect(formatFileSize(undefined)).toBe("大小未知");
});
it("将 UTC ISO 时间显示为上海时间，并保留已有友好日期", () => {
  expect(formatDisplayTime("2026-10-31T08:50:58.808035+00:00")).toContain("16:50:58");
  expect(formatDisplayTime("2026/10/31 16:50")).toBe("2026/10/31 16:50");
  expect(formatDisplayTime("bad")).toBe("bad");
});
