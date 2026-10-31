import * as pdfjs from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;
export async function getPageCount(file: File) {
  const task = pdfjs.getDocument({
    data: new Uint8Array(await file.arrayBuffer()),
  });
  const pdf = await task.promise;
  const pages = pdf.numPages;
  await task.destroy();
  return pages;
}
export async function renderPage(
  file: File | Uint8Array,
  canvas: HTMLCanvasElement,
  page = 1,
  signal?: AbortSignal,
) {
  const check = () => {
    if (signal?.aborted)
      throw new DOMException("Preview cancelled", "AbortError");
  };
  check();
  const data =
    file instanceof File ? new Uint8Array(await file.arrayBuffer()) : file;
  check();
  const task = pdfjs.getDocument({ data });
  let render: ReturnType<pdfjs.PDFPageProxy["render"]> | undefined;
  let destroyed: Promise<void> | undefined;
  const destroy = () => (destroyed ||= task.destroy());
  const cancel = () => {
    render?.cancel();
    void destroy().catch(() => {});
  };
  signal?.addEventListener("abort", cancel, { once: true });
  try {
    const pdf = await task.promise;
    check();
    const p = await pdf.getPage(Math.max(1, Math.min(page, pdf.numPages)));
    check();
    const viewport = p.getViewport({
      scale: Math.min(1.5, 720 / p.getViewport({ scale: 1 }).width),
    });
    // PDF.js owns only a private canvas until this complete, current render commits.
    const buffer = document.createElement("canvas");
    buffer.width = viewport.width;
    buffer.height = viewport.height;
    render = p.render({ canvas: buffer, viewport });
    await render.promise;
    check();
    const context = canvas.getContext("2d");
    if (!context) throw new Error("预览画布不可用");
    canvas.width = buffer.width;
    canvas.height = buffer.height;
    context.drawImage(buffer, 0, 0);
    return pdf.numPages;
  } catch (error) {
    check();
    throw error;
  } finally {
    signal?.removeEventListener("abort", cancel);
    await destroy();
  }
}
