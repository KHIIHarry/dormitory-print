import type { Config, PrintFile } from "./types";
export function paperLayout(
  width: number,
  height: number,
  m: { top: number; right: number; bottom: number; left: number },
) {
  return {
    width,
    height,
    printWidth: width - m.left - m.right,
    printHeight: height - m.top - m.bottom,
    left: m.left,
    top: m.top,
  };
}
export function photoCrop(
  imageWidth: number,
  imageHeight: number,
  printWidth: number,
  printHeight: number,
  zoom: number,
  x: number,
  y: number,
) {
  const scale =
    Math.max(printWidth / imageWidth, printHeight / imageHeight) * zoom;
  const width = Math.min(1, printWidth / (imageWidth * scale)),
    height = Math.min(1, printHeight / (imageHeight * scale));
  return {
    x: ((1 - width) * x) / 100,
    y: ((1 - height) * y) / 100,
    width,
    height,
  };
}
export function photoPaper(file: PrintFile, config: Config) {
  const long = Math.max(config.photoWidthMm, config.photoHeightMm),
    short = Math.min(config.photoWidthMm, config.photoHeightMm);
  return paperLayout(
    file.photo.orientation === "LANDSCAPE" ? long : short,
    file.photo.orientation === "LANDSCAPE" ? short : long,
    config.margins,
  );
}
export async function drawPhoto(
  file: PrintFile,
  config: Config,
  canvas: HTMLCanvasElement,
) {
  if (!file.localFile) return;
  const bitmap = await createImageBitmap(file.localFile, {
    imageOrientation: "from-image",
  });
  const rotated = document.createElement("canvas");
  const turn = file.photo.rotationDegrees % 180 !== 0;
  rotated.width = turn ? bitmap.height : bitmap.width;
  rotated.height = turn ? bitmap.width : bitmap.height;
  const rc = rotated.getContext("2d")!;
  rc.translate(rotated.width / 2, rotated.height / 2);
  rc.rotate((file.photo.rotationDegrees * Math.PI) / 180);
  rc.drawImage(bitmap, -bitmap.width / 2, -bitmap.height / 2);
  bitmap.close();
  const paper = photoPaper(file, config);
  const dpm = 6;
  canvas.width = Math.round(paper.width * dpm);
  canvas.height = Math.round(paper.height * dpm);
  const ctx = canvas.getContext("2d")!;
  ctx.fillStyle = "white";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  const left = paper.left * dpm,
    top = paper.top * dpm,
    w = paper.printWidth * dpm,
    h = paper.printHeight * dpm;
  ctx.save();
  ctx.beginPath();
  ctx.rect(left, top, w, h);
  ctx.clip();
  const scale =
    file.photo.fit === "COVER"
      ? Math.max(w / rotated.width, h / rotated.height) * file.photo.zoom
      : Math.min(w / rotated.width, h / rotated.height);
  const width = rotated.width * scale,
    height = rotated.height * scale;
  ctx.drawImage(
    rotated,
    left + ((w - width) * file.photo.x) / 100,
    top + ((h - height) * file.photo.y) / 100,
    width,
    height,
  );
  ctx.restore();
  rotated.width = 0;
  rotated.height = 0;
}
