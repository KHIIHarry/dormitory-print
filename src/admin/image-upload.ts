import { DeviceTask, photoTransfer } from "../courier/task-state";
export type AssetUploadState = { busy: boolean; value?: number; label: string };
export type AssetUploadOptions = {
  signal: AbortSignal;
  onUploadProgress: (event: { loaded: number; total?: number }) => void;
  onUploadComplete: () => void;
};
/** Commits an image only once the server confirms it, within the same identity scope. */
export class AssetUpload {
  private task = new DeviceTask();
  private controller?: AbortController;
  constructor(private scope: () => number) {}
  cancel() {
    this.task.cancel();
    this.controller?.abort();
  }
  async run(
    send: (options: AssetUploadOptions) => Promise<{ url: string }>,
    state: (value: AssetUploadState) => void,
    commit: (url: string) => void,
    error: (error: unknown) => void,
  ) {
    const token = this.task.start();
    if (token === undefined) return;
    const scope = this.scope();
    const current = () => this.task.current(token) && scope === this.scope();
    this.controller = new AbortController();
    state({ busy: true, label: "正在准备图片…" });
    try {
      const result = await send({
        signal: this.controller.signal,
        onUploadProgress: ({ loaded, total }) => {
          if (current()) state({ busy: true, ...photoTransfer(loaded, total) });
        },
        onUploadComplete: () => {
          if (current())
            state({
              busy: true,
              value: 100,
              label: "图片已传输，等待服务端确认…",
            });
        },
      });
      if (current()) commit(result.url);
    } catch (e) {
      if (current()) error(e);
    } finally {
      if (current()) {
        this.task.finish(token);
        state({ busy: false, label: "" });
      }
    }
  }
}

/** A QR has one image; picker and drop handlers share the same validation. */
export function qrUploadFile(files: File[]): File {
  if (files.length !== 1) throw new Error("每次请选择一张二维码图片");
  if (!files[0].type.startsWith("image/")) throw new Error("请选择图片格式");
  return files[0];
}
