import type { PrintFile } from "./types";
export type ImagePrintMedium = "A4" | "PHOTO";
export function isImageUpload(file: { name: string; type: string }) {
  return (
    file.type.startsWith("image/") || /\.(jpe?g|png|webp)$/i.test(file.name)
  );
}
export function imageSource(file: PrintFile) {
  if (file.imageSource) return file.imageSource;
  if (file.localFile && isImageUpload(file.localFile))
    return { file: file.localFile, url: file.url, assetId: file.assetId };
  return undefined;
}
export function canChooseImageMedium(file: PrintFile) {
  return (
    !!imageSource(file)?.assetId &&
    (file.status === "READY" ||
      (file.status === "FAILED" && file.imagePrintMedium === "A4"))
  );
}
export function selectImagePrintMedium(
  file: PrintFile,
  medium: ImagePrintMedium,
  a4?: { file: File; assetId: string; status?: PrintFile["status"] },
): PrintFile {
  const source = imageSource(file);
  if (!source?.assetId) throw new Error("原图尚未准备就绪，请重新上传");
  if (medium === "A4" && (!a4?.assetId || a4.file.type !== "application/pdf"))
    throw new Error("A4 PDF尚未准备就绪，请重试");
  return {
    ...file,
    imageSource: source,
    imagePrintMedium: medium,
    imagePrintVersion: (file.imagePrintVersion || 0) + 1,
    kind: medium === "A4" ? "DOCUMENT" : "PHOTO",
    status: medium === "A4" ? a4!.status || "READY" : "READY",
    progress: 100,
    error: undefined,
    pages: 1,
    assetId: medium === "A4" ? a4!.assetId : source.assetId,
    localFile: medium === "A4" ? a4!.file : source.file,
    url: source.url,
    segments:
      medium === "A4"
        ? [
            {
              id: crypto.randomUUID(),
              range: "1",
              colorMode: "COLOR",
              sides: "SINGLE",
              flip: "NONE",
              copies: 1,
            },
          ]
        : [],
    photo: { ...file.photo, copies: 1 },
    excludedPages: [],
    bindingType: "NONE",
    bindingStatus: "NOT_REQUIRED",
    printStatus: "PENDING",
    finalArtifacts: undefined,
    artifactId: undefined,
    sha256: undefined,
    pdfUrl: undefined,
    taskResults: undefined,
    attemptIds: undefined,
  };
}
export function imageA4Placement(width: number, height: number) {
  if (
    !Number.isFinite(width) ||
    !Number.isFinite(height) ||
    width <= 0 ||
    height <= 0
  )
    throw new Error("图片尺寸无效");
  const mm = 72 / 25.4,
    pageWidth = 210 * mm,
    pageHeight = 297 * mm,
    margin = 10 * mm;
  const scale = Math.min(
    (pageWidth - 2 * margin) / width,
    (pageHeight - 2 * margin) / height,
  );
  return {
    pageWidth,
    pageHeight,
    x: (pageWidth - width * scale) / 2,
    y: (pageHeight - height * scale) / 2,
    width: width * scale,
    height: height * scale,
  };
}
export async function createA4ImagePdf(
  bytes: Uint8Array,
  format: "png" | "jpeg",
) {
  const { PDFDocument } = await import("pdf-lib");
  const doc = await PDFDocument.create();
  const image =
    format === "png" ? await doc.embedPng(bytes) : await doc.embedJpg(bytes);
  const placement = imageA4Placement(image.width, image.height);
  const page = doc.addPage([placement.pageWidth, placement.pageHeight]);
  page.drawImage(image, {
    x: placement.x,
    y: placement.y,
    width: placement.width,
    height: placement.height,
  });
  return doc.save();
}
/** Normalize EXIF orientation and WebP using the browser's image decoder, only in memory. */
export async function imageToA4File(raw: File) {
  const bitmap = await createImageBitmap(raw, {
    imageOrientation: "from-image",
  });
  const canvas = document.createElement("canvas");
  try {
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    const context = canvas.getContext("2d");
    if (!context)
      throw new Error("当前浏览器无法处理图片，请换用浏览器或先转为PDF");
    context.drawImage(bitmap, 0, 0);
    const png = await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(
        (blob) =>
          blob ? resolve(blob) : reject(new Error("图片转换失败，请重试")),
        "image/png",
      ),
    );
    const pdf = await createA4ImagePdf(
      new Uint8Array(await png.arrayBuffer()),
      "png",
    );
    return new File(
      [pdf.slice().buffer as ArrayBuffer],
      raw.name.replace(/\.[^.]+$/, "") + "-A4.pdf",
      { type: "application/pdf" },
    );
  } finally {
    bitmap.close();
    canvas.width = 0;
    canvas.height = 0;
  }
}
