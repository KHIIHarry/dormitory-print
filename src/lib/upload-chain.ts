import { progressValue } from "./feedback-state";
import type { PrintFile } from "./types";
export function uploadProgress({
  loaded,
  total,
}: {
  loaded: number;
  total?: number;
}): Pick<PrintFile, "uploadPhase" | "progress"> {
  const progress = progressValue(loaded, total);
  return {
    uploadPhase: progress === 100 ? "AWAITING_RESPONSE" : "TRANSFERRING",
    progress,
  };
}
export const retryStage = ({ assetId }: { assetId: string }) =>
  assetId ? "PROCESSING" : "UPLOAD";
export class UploadOperations {
  private jobs = new Map<string, AbortController>();
  start(id: string) {
    this.cancel(id);
    const controller = new AbortController();
    this.jobs.set(id, controller);
    return {
      signal: controller.signal,
      current: () =>
        this.jobs.get(id) === controller && !controller.signal.aborted,
    };
  }
  poll(id: string) {
    return this.jobs.has(id) ? undefined : this.start(id);
  }
  cancel(id: string) {
    this.jobs.get(id)?.abort();
    this.jobs.delete(id);
  }
  clear() {
    for (const id of this.jobs.keys()) this.cancel(id);
  }
}

export const interruptUploads = (files: PrintFile[]): PrintFile[] =>
  files.map((file) => {
    if (file.status === "UPLOADING")
      return {
        ...file,
        status: file.assetId ? "PROCESSING" : "FAILED",
        uploadPhase: file.assetId ? "PROCESSING" : "FAILED",
        error: file.assetId
          ? undefined
          : "页面已离开，上传结果待核实，请重试核实",
      };
    if (file.a4UploadAttempt)
      return { ...file, error: "页面已离开，A4文件准备结果待核实，请重试核实" };
    return file;
  });
export const a4Attempt = (file: PrintFile): { key: string; file?: File } =>
  file.a4UploadAttempt || { key: crypto.randomUUID() };

export function uploadPhaseLabel(file: PrintFile): string {
  if (file.status === "READY")
    return file.imagePrintMedium === "PENDING" ? "待选纸张" : "已就绪";
  if (file.status === "FAILED") return "处理失败";
  if (file.status === "PROCESSING")
    return /\.(docx?|pptx?)$/i.test(file.name) ? "转换中" : "处理中";
  if (file.uploadPhase === "QUEUED") return "等待上传";
  if (file.uploadPhase === "READING") return "读取文件";
  if (file.uploadPhase === "AWAITING_RESPONSE") return "等待服务端确认";
  return "上传中";
}
