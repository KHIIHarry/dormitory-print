import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { UploadFileStatus, UploadFileRow } from "../src/customer/upload-status";
import type { PrintFile } from "../src/lib/types";
const rendered = (file: Partial<PrintFile>) =>
  renderToStaticMarkup(
    createElement(UploadFileStatus, { file: file as PrintFile }),
  );
it("renders processing PDFs and images as processing rather than conversion", () => {
  for (const name of ["notes.pdf", "photo.jpg", "photo-a4.pdf"]) {
    expect(
      rendered({ name, status: "PROCESSING", uploadPhase: "PROCESSING" }),
    ).toContain("处理中");
    expect(rendered({ name, status: "PROCESSING" })).not.toContain("转换中");
  }
});
it("renders queued and reading phases before upload starts", () => {
  expect(rendered({ status: "UPLOADING", uploadPhase: "QUEUED" })).toContain(
    "等待上传",
  );
  expect(rendered({ status: "UPLOADING", uploadPhase: "READING" })).toContain(
    "读取文件",
  );
});
it("keeps office conversion wording and final readiness accurate", () => {
  expect(rendered({ name: "slides.pptx", status: "PROCESSING" })).toContain(
    "转换中",
  );
  expect(
    rendered({
      name: "photo.jpg",
      status: "READY",
      imagePrintMedium: "PENDING",
    }),
  ).toContain("待选纸张");
  expect(
    rendered({ status: "UPLOADING", uploadPhase: "AWAITING_RESPONSE" }),
  ).toContain("等待服务端确认");
});

it("renders one icon-overlay ring for an image, and one ordinary bar for a document", () => {
  const row = (file: Partial<PrintFile>) =>
    renderToStaticMarkup(
      createElement(UploadFileRow, {
        file: { name: "file.pdf", size: 1, pages: 0, ...file } as PrintFile,
      }),
    );
  for (const file of [
    { kind: "PHOTO", name: "image.jpg" },
    { kind: "DOCUMENT", imageSource: { assetId: "original" } },
  ]) {
    const html = row({
      ...file,
      status: "UPLOADING",
      uploadPhase: "TRANSFERRING",
      progress: 40,
    } as Partial<PrintFile>);
    expect(html.match(/role="progressbar"/g) || []).toHaveLength(1);
    expect(html).toContain("task-progress-ring");
    expect(html).toContain("file-progress-overlay");
    expect(html).toContain('aria-valuenow="40"');
    expect(html).toContain('stroke-dasharray="40 100"');
  }
  const document = row({
    kind: "DOCUMENT",
    status: "UPLOADING",
    uploadPhase: "TRANSFERRING",
    progress: 40,
  });
  expect(document.match(/role="progressbar"/g) || []).toHaveLength(1);
  expect(document).not.toContain("task-progress-ring");
  expect(document).not.toContain("file-progress-overlay");
});
it("renders an indeterminate image processing ring and removes it when ready", () => {
  const row = (status: PrintFile["status"]) =>
    renderToStaticMarkup(
      createElement(UploadFileRow, {
        file: {
          name: "image.png",
          kind: "PHOTO",
          size: 1,
          status,
          uploadPhase: "PROCESSING",
          progress: 100,
        } as PrintFile,
      }),
    );
  expect(row("PROCESSING")).toContain("task-progress-ring");
  expect(row("PROCESSING")).toContain("progress-unknown");
  expect(row("PROCESSING")).not.toContain("aria-valuenow");
  expect(row("READY")).not.toContain('role="progressbar"');
});
