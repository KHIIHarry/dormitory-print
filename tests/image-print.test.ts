import { readFileSync } from "node:fs";
import { describe, it, expect } from "vitest";
import { PDFDocument, PDFName, PDFDict } from "pdf-lib";
import { makeFile, createDemo } from "../src/lib/demo";
import {
  imageA4Placement,
  createA4ImagePdf,
  selectImagePrintMedium,
  isImageUpload,
  canChooseImageMedium,
} from "../src/lib/image-print";
import { demoPriceLines, checkoutSignature } from "../src/customer/checkout";
import { generateDemoArtifacts } from "../src/lib/artifacts";
const png = new Uint8Array(readFileSync("public/assets/icon-192.png"));
function image() {
  const f = makeFile("picture.png", 1);
  f.kind = "PHOTO";
  f.localFile = new File([png], "picture.png", { type: "image/png" });
  f.url = "blob:test-original";
  f.imagePrintMedium = "PENDING";
  f.imageSource = { file: f.localFile, url: f.url, assetId: "image-asset" };
  return f;
}
describe("图片可以选择A4或照片纸", () => {
  it("图片扩展名也可识别，缺少MIME时不误走Office转换", () => {
    expect(isImageUpload({ type: "", name: "PICTURE.PNG" })).toBe(true);
    expect(isImageUpload({ type: "application/pdf", name: "notes.pdf" })).toBe(
      false,
    );
  });
  it.each([
    [1200, 800],
    [800, 1200],
    [4000, 200],
  ])("A4完整容纳%ix%i图片，不裁切或拉伸", (width, height) => {
    const p = imageA4Placement(width, height);
    expect(p.width / p.height).toBeCloseTo(width / height);
    expect(p.x).toBeGreaterThanOrEqual(28);
    expect(p.y).toBeGreaterThanOrEqual(28);
    expect(p.x + p.width).toBeLessThanOrEqual(568);
    expect(p.y + p.height).toBeLessThanOrEqual(814);
  });
  it("A4 PDF真实包含一张嵌入图片，尺寸为210×297mm", async () => {
    const bytes = await createA4ImagePdf(png, "png");
    const pdf = await PDFDocument.load(bytes);
    expect(pdf.getPageCount()).toBe(1);
    const page = pdf.getPage(0);
    expect(page.getWidth()).toBeCloseTo(595.28, 1);
    expect(page.getHeight()).toBeCloseTo(841.89, 1);
    expect(
      page.node.Resources()!.lookup(PDFName.of("XObject"), PDFDict).keys(),
    ).toHaveLength(1);
  });
  it("A4与照片按不同服务计价，切回照片恢复原始图片并清掉旧产物", async () => {
    const original = image(),
      pdf = new File([await createA4ImagePdf(png, "png")], "picture-A4.pdf", {
        type: "application/pdf",
      });
    const a4 = selectImagePrintMedium(original, "A4", {
      file: pdf,
      assetId: "a4-asset",
    });
    expect(a4).toMatchObject({
      kind: "DOCUMENT",
      pages: 1,
      assetId: "a4-asset",
      imagePrintMedium: "A4",
      bindingType: "NONE",
    });
    expect(a4.segments[0]).toMatchObject({
      range: "1",
      colorMode: "COLOR",
      sides: "SINGLE",
      flip: "NONE",
    });
    const photo = selectImagePrintMedium(
      { ...a4, sha256: "old", finalArtifacts: [{} as any] },
      "PHOTO",
    );
    expect(photo.localFile).toBe(original.localFile);
    expect(photo.assetId).toBe("image-asset");
    expect(photo.finalArtifacts).toBeUndefined();
    expect(photo.sha256).toBeUndefined();
    const config = createDemo().config;
    expect(demoPriceLines([a4], config)[0]).toMatchObject({
      service: "A4",
      quantity: 1,
      unitPriceCents: config.prices.colorSingle,
    });
    expect(demoPriceLines([photo], config)[0]).toMatchObject({
      service: "PHOTO",
      unitPriceCents: config.prices.photo,
    });
    expect(checkoutSignature([a4])).not.toBe(checkoutSignature([photo]));
  });
  it("A4转换缺少有效PDF资源时不能伪装为文档就绪", () => {
    expect(() => selectImagePrintMedium(image(), "A4")).toThrow();
  });
  it("实际A4输出沿用文档份数与双面补白规则，不走照片纸尺寸", async () => {
    const a4 = selectImagePrintMedium(image(), "A4", {
      file: new File([await createA4ImagePdf(png, "png")], "a4.pdf", {
        type: "application/pdf",
      }),
      assetId: "a4-asset",
    });
    a4.segments[0].copies = 2;
    a4.segments[0].sides = "DUPLEX";
    a4.segments[0].flip = "LONG_EDGE";
    const [artifact] = await generateDemoArtifacts([a4], createDemo().config);
    const pdf = await PDFDocument.load(
      await (await fetch(artifact.url)).arrayBuffer(),
    );
    expect(pdf.getPageCount()).toBe(4);
    expect(artifact.sheets).toBe(2);
    expect(artifact.blankBacks).toBe(2);
    expect(pdf.getPage(0).getWidth()).toBeCloseTo(595.28, 1);
    URL.revokeObjectURL(artifact.url);
  });
});

it("切换回原纸张也不复用旧预览检查缓存", async () => {
  const original = image();
  const photo = selectImagePrintMedium(original, "PHOTO");
  const a4 = selectImagePrintMedium(photo, "A4", {
    file: new File([await createA4ImagePdf(png, "png")], "a4.pdf", {
      type: "application/pdf",
    }),
    assetId: "a4",
  });
  const restored = selectImagePrintMedium(a4, "PHOTO");
  expect(restored.imagePrintVersion).toBe(3);
  expect(checkoutSignature([restored])).not.toBe(checkoutSignature([photo]));
});

it("A4处理失败后仍可用原图切回照片纸，无需重新上传", async () => {
  const a4 = selectImagePrintMedium(image(), "A4", {
    file: new File([await createA4ImagePdf(png, "png")], "a4.pdf", {
      type: "application/pdf",
    }),
    assetId: "failed-a4",
    status: "FAILED",
  });
  expect(canChooseImageMedium(a4)).toBe(true);
  const photo = selectImagePrintMedium(a4, "PHOTO");
  expect(photo.status).toBe("READY");
  expect(photo.assetId).toBe("image-asset");
  expect(photo.localFile?.type).toBe("image/png");
  const failedSource = image();
  failedSource.status = "FAILED";
  failedSource.imageSource!.assetId = "";
  expect(canChooseImageMedium(failedSource)).toBe(false);
});
