import { it, expect } from "vitest";
import { makeFile, createDemo } from "../src/lib/demo";
import { fileErrors, pricingErrors } from "../src/lib/domain";
import { generateDemoArtifacts } from "../src/lib/artifacts";
function pending() {
  const file = makeFile("image.png", 1);
  file.kind = "PHOTO";
  return Object.assign(file, { imagePrintMedium: "PENDING" as const });
}
it("未选择纸张的图片不能通过文件校验", () => {
  expect(fileErrors(pending()).join("")).toContain("选择打印纸张");
});
it("未选择纸张的图片不能生成默认照片报价", () => {
  expect(pricingErrors(pending(), createDemo().config).join("")).toContain(
    "选择打印纸张",
  );
});
it("不能绕过纸张选择直接生成照片纸产物", async () => {
  await expect(
    generateDemoArtifacts([pending()], createDemo().config),
  ).rejects.toThrow("选择打印纸张");
});
