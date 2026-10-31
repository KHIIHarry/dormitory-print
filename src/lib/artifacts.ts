import { PDFDocument, StandardFonts, rgb } from "pdf-lib";
import { drawPhoto, photoPaper } from "./photo-layout";
import type { Config, PrintFile } from "./types";
import { expandRanges, productionCount } from "./domain";
export type Artifact = {
  id: string;
  fileId: string;
  segmentId: string;
  sha256: string;
  url: string;
  pages: number;
  sheets: number;
  blankBacks: number;
  range: string;
  copies: number;
  color: string;
  sides: string;
  flip: string;
};
export async function generateDemoArtifacts(
  files: PrintFile[],
  config: Config,
): Promise<Artifact[]> {
  const artifacts: Artifact[] = [];
  for (const file of files) {
    if (file.imagePrintMedium === "PENDING")
      throw new Error("请先为图片选择打印纸张");
    if (file.kind === "PHOTO") {
      const doc = await PDFDocument.create();
      const paper = photoPaper(file, config);
      const mm = 72 / 25.4;
      const page = doc.addPage([paper.width * mm, paper.height * mm]);
      if (file.localFile) {
        const canvas = document.createElement("canvas");
        await drawPhoto(file, config, canvas);
        const image = await doc.embedPng(canvas.toDataURL("image/png"));
        page.drawImage(image, {
          x: 0,
          y: 0,
          width: page.getWidth(),
          height: page.getHeight(),
        });
        canvas.width = 0;
        canvas.height = 0;
      }
      for (let c = 1; c < file.photo.copies; c++) {
        const [p] = await doc.copyPages(doc, [0]);
        doc.addPage(p);
      }
      const bytes = await doc.save();
      artifacts.push(
        await artifact(
          file,
          "photo",
          bytes,
          file.photo.copies,
          file.photo.copies,
          0,
          "照片",
          file.photo.copies,
          "COLOR",
          "SINGLE",
          "NONE",
        ),
      );
      continue;
    }
    let original: PDFDocument;
    if (file.localFile)
      original = await PDFDocument.load(await file.localFile.arrayBuffer());
    else {
      original = await PDFDocument.create();
      const font = await original.embedFont(StandardFonts.Helvetica);
      for (let p = 1; p <= file.pages; p++) {
        const page = original.addPage([595.28, 841.89]);
        page.drawText("COLLEGE ENGLISH - REVIEW NOTES", {
          x: 54,
          y: 770,
          size: 18,
          font,
          color: rgb(0.19, 0.19, 0.19),
        });
        page.drawText(`Unit ${p} - Learning & Practice`, {
          x: 54,
          y: 715,
          size: 14,
          font,
        });
        for (let n = 0; n < 20; n++)
          page.drawText(
            `${n + 1}. Read carefully, take notes, and review the key concepts.`,
            {
              x: 54,
              y: 675 - n * 23,
              size: 10,
              font,
              color: rgb(0.4, 0.4, 0.4),
            },
          );
        page.drawText(`Demo document / Page ${p}`, {
          x: 54,
          y: 35,
          size: 9,
          font,
        });
      }
    }
    for (const s of file.segments) {
      const doc = await PDFDocument.create();
      const selected = expandRanges(s.range, file.pages);
      const count = productionCount(selected.length, s.copies, s.sides);
      for (let copy = 0; copy < s.copies; copy++) {
        const pages = await doc.copyPages(
          original,
          selected.map((p) => p - 1),
        );
        pages.forEach((p) => doc.addPage(p));
        if (s.sides === "DUPLEX" && selected.length % 2)
          doc.addPage([595.28, 841.89]);
      }
      artifacts.push(
        await artifact(
          file,
          s.id,
          await doc.save(),
          count.pdfPages,
          count.sheets,
          count.blankBacks,
          s.range,
          s.copies,
          s.colorMode,
          s.sides,
          s.flip,
        ),
      );
    }
  }
  return artifacts;
}
async function artifact(
  file: PrintFile,
  segmentId: string,
  bytes: Uint8Array,
  pages: number,
  sheets: number,
  blankBacks: number,
  range: string,
  copies: number,
  color: string,
  sides: string,
  flip: string,
): Promise<Artifact> {
  const buffer = bytes.slice().buffer as ArrayBuffer;
  const hash = await crypto.subtle.digest("SHA-256", buffer);
  return {
    id: crypto.randomUUID(),
    fileId: file.id,
    segmentId,
    sha256: Array.from(new Uint8Array(hash), (b) =>
      b.toString(16).padStart(2, "0"),
    ).join(""),
    url: URL.createObjectURL(new Blob([buffer], { type: "application/pdf" })),
    pages,
    sheets,
    blankBacks,
    range,
    copies,
    color,
    sides,
    flip,
  };
}
