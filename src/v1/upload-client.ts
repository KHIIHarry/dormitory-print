/**
 * V1 upload/resume client shared by the customer upload and settings screens.
 *
 * Protocol source of truth (verified against app/media.py):
 *   POST   /upload-sessions                         UploadSessionCreate
 *   GET    /upload-sessions/{id}                    UploadSessionView
 *   PUT    /upload-sessions/{id}/chunks?offset=N    raw octet-stream chunk
 *   POST   /upload-sessions/{id}/complete           UploadSessionComplete
 *   DELETE /upload-sessions/{id}                    cancel session
 *   GET    /assets/{id}, POST /assets/{id}/retry, POST /assets/{id}/cancel
 * Raw HEIC display still depends on a normalized preview field from media.py.
 */
import { ApiError, mutation, request } from "../lib/api";
export const HEIC_EXTENSIONS = ["heic", "heif"] as const;

export const DEFAULT_FORMATS = [
  "pdf",
  "doc",
  "docx",
  "ppt",
  "pptx",
  "xls",
  "xlsx",
  "jpg",
  "jpeg",
  "png",
  "webp",
  "heic",
  "heif",
] as const;

export type UploadSessionStatus =
  | "UPLOADING"
  | "COMPLETED"
  | "CANCELLED"
  | "EXPIRED";

export type UploadSessionView = {
  id: string;
  status: UploadSessionStatus;
  purpose: string;
  name: string;
  totalBytes: number;
  receivedBytes: number;
  nextOffset: number;
  chunkSize: number;
  ranges: { start: number; end: number }[];
  sha256: string;
  mime?: string | null;
  expiresAt: string;
  assetId?: string | null;
  cancelRequested?: boolean;
};

export type AssetView = {
  id: string;
  assetId: string;
  status:
    | "UPLOADING"
    | "QUEUED"
    | "PROCESSING"
    | "READY"
    | "FAILED"
    | "DELETED"
    | "PURGED";
  purpose: string;
  name: string;
  mime: string;
  size: number;
  sha256: string;
  pages?: number | null;
  kind?: string | null;
  error?: string | null;
  failureCode?: string | null;
  retryable?: boolean;
  dimensions?: { width?: number; height?: number } | null;
  url?: string | null;
  cancelRequested?: boolean;
  normalizedAssetId?: string | null;
  /**
   * Requested from the media worker: authorized PNG preview of the READY
   * normalized asset. Browsers cannot decode HEIC reliably, so the UI must use
   * this rather than the raw source object URL.
   */
  normalizedUrl?: string | null;
  previewUrl?: string | null;
  createdAt: string;
  expiresAt?: string | null;
};

export type AssetUploadResult = {
  assetId: string;
  status: AssetView["status"];
  kind: string;
  name: string;
  size: number;
  sha256: string;
  pages?: number | null;
  duplicate?: boolean;
};

/** Mirrors pdf_engine.validate_excel_options; unknown keys are rejected. */
export type ExcelOptions = {
  sheetName?: string;
  /** A1-style print area, e.g. "A1:F40". Server validates against the workbook. */
  printArea?: string;
  printTitlesRows?: string;
  printTitlesColumns?: string;
  orientation?: "PORTRAIT" | "LANDSCAPE";
  paperSize?: "A4";
  fitToWidth?: number;
  fitToHeight?: number;
  scalePercent?: number;
  gridlines?: boolean;
  marginsMm?: {
    top?: number;
    right?: number;
    bottom?: number;
    left?: number;
    header?: number;
    footer?: number;
  };
};

export type PreparationFileView = {
  clientFileId: string;
  assetId: string;
  kind: string;
  name: string;
  status: string;
  pages?: number | null;
  excludedPages?: number[];
  bindingType?: string;
  segments?: Record<string, unknown>[];
  copies?: number | null;
  orientation?: string | null;
  fit?: string | null;
  rotationDegrees?: number | null;
  crop?: Record<string, number> | null;
};

export type PreparationArtifactView = {
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

export type PreparationView = {
  id: string;
  version: number;
  status: "QUEUED" | "PROCESSING" | "READY" | "FAILED" | "CANCELLED";
  contentRevision: number;
  expiresAt: string;
  cancelRequested?: boolean;
  quote?: unknown;
  files: PreparationFileView[];
  artifacts: PreparationArtifactView[];
  validation?: { ok: boolean; errors: { code: string; message: string }[]; warnings: unknown[] };
  checkoutAccountId?: string | null;
  canAdopt?: boolean;
  failureCode?: string | null;
  failureMessage?: string | null;
  replacesOrderId?: string | null;
  expectedOrderVersion?: number | null;
  createdAt?: string;
  updatedAt?: string;
};

export const fileExtension = (name: string) =>
  name.split(".").pop()?.toLowerCase() || "";

export const supportsFormat = (name: string, formats?: readonly string[]) =>
  (formats?.length ? formats : DEFAULT_FORMATS).includes(fileExtension(name));

export const isHeicUpload = (file: { name: string; type?: string }) =>
  (HEIC_EXTENSIONS as readonly string[]).includes(fileExtension(file.name)) ||
  file.type === "image/heic" ||
  file.type === "image/heif";

export const isSpreadsheetUpload = (file: { name: string }) =>
  ["xls", "xlsx"].includes(fileExtension(file.name));

export const isV1ImageUpload = (file: {
  name: string;
  type?: string;
}) =>
  isHeicUpload(file) ||
  !!file.type?.startsWith("image/") ||
  ["jpg", "jpeg", "png", "webp"].includes(fileExtension(file.name));

/**
 * A READY asset can be displayed/edited only when the server exposes an
 * authorized normalized preview. Raw HEIC content is never treated as a local
 * image source.
 */
export function normalizedDisplayUrl(asset: AssetView): string | null {
  if (asset.status !== "READY") return null;
  if (asset.normalizedUrl) return asset.normalizedUrl;
  if (asset.previewUrl) return asset.previewUrl;
  return asset.normalizedAssetId
    ? `/api/v1/assets/${encodeURIComponent(asset.normalizedAssetId)}/content`
    : null;
}

const apiUrl = (path: string) => {
  // Media views return same-origin API paths (e.g. /api/v1/assets/...). Never
  // double-prefix those with VITE_API_BASE_URL.
  if (path.startsWith("http") || path.startsWith("/api/")) return path;
  const base = (import.meta.env.VITE_API_BASE_URL || "/api/v1").replace(
    /\/$/,
    "",
  );
  return `${base}${path}`;
};

/**
 * Fetch an authorized normalized PNG and build a local File + object URL. This
 * is the only path from a HEIC/misdecoded image to the existing A4 generator and
 * photo editor. Returns null when the server has no normalized view yet.
 */
export async function loadNormalizedImage(
  asset: AssetView,
  originalName: string,
  signal?: AbortSignal,
): Promise<{ file: File; url: string; width: number; height: number } | null> {
  const source = normalizedDisplayUrl(asset);
  if (!source) return null;
  const response = await fetch(apiUrl(source), {
    credentials: "same-origin",
    cache: "no-store",
    signal,
  });
  if (!response.ok) return null;
  const blob = await response.blob();
  // Only authorized PNG/JPEG output is displayable. Anything else must not be
  // passed off as a successful HEIC decode.
  if (!["image/png", "image/jpeg", "image/webp"].includes(blob.type))
    return null;
  const name = originalName.replace(/\.[^.]+$/, "") + ".png";
  const file = new File([blob], name, { type: blob.type });
  let width = asset.dimensions?.width || 0;
  let height = asset.dimensions?.height || 0;
  if (!width || !height) {
    const bitmap = await createImageBitmap(blob, {
      imageOrientation: "from-image",
    });
    width = bitmap.width;
    height = bitmap.height;
    bitmap.close();
  }
  return { file, url: URL.createObjectURL(blob), width, height };
}

export async function sha256File(file: File): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}

export const uploadSessionCreateBody = (
  file: File,
  sha256: string,
  options: { purpose?: string; excelOptions?: ExcelOptions } = {},
) => ({
  name: file.name,
  purpose: options.purpose || "PRINT_SOURCE",
  totalBytes: file.size,
  sha256,
  ...(file.type ? { mime: file.type } : {}),
  ...(options.excelOptions ? { excelOptions: options.excelOptions } : {}),
});

export const formatUploadProgress = (
  receivedBytes: number,
  totalBytes: number,
) =>
  totalBytes > 0
    ? Math.max(0, Math.min(100, Math.round((receivedBytes / totalBytes) * 100)))
    : 0;

/* ------------------------------------------------------------------------- */
/* Exact media.py routes.                                                    */
/* ------------------------------------------------------------------------- */

export const mediaPaths = {
  createSession: "/upload-sessions",
  session: (id: string) => `/upload-sessions/${encodeURIComponent(id)}`,
  chunks: (id: string, offset: number) =>
    `/upload-sessions/${encodeURIComponent(id)}/chunks?offset=${offset}`,
  complete: (id: string) =>
    `/upload-sessions/${encodeURIComponent(id)}/complete`,
  asset: (id: string) => `/assets/${encodeURIComponent(id)}`,
  retryAsset: (id: string) => `/assets/${encodeURIComponent(id)}/retry`,
  cancelAsset: (id: string) => `/assets/${encodeURIComponent(id)}/cancel`,
};

const apiBase = () =>
  (import.meta.env.VITE_API_BASE_URL || "/api/v1").replace(/\/$/, "");

const csrfToken = () => {
  if (typeof document === "undefined") return "";
  return (
    document.cookie
      .split("; ")
      .find((entry) => entry.startsWith("csrfToken="))
      ?.slice("csrfToken=".length) || ""
  );
};

const chunkKeys = new Map<string, string>();
const chunkKey = (sessionId: string, offset: number, sha: string) => {
  const key = `${sessionId}:${offset}:${sha}`;
  const existing = chunkKeys.get(key);
  if (existing) return existing;
  const created = crypto.randomUUID();
  chunkKeys.set(key, created);
  return created;
};

function putChunkRaw(
  path: string,
  chunk: Blob,
  sha256: string,
  headers: Record<string, string>,
  signal: AbortSignal | undefined,
  onProgress?: (loaded: number, total: number) => void,
): Promise<UploadSessionView> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    let settled = false;
    const finish = (fn: () => void) => {
      if (settled) return;
      settled = true;
      signal?.removeEventListener("abort", abort);
      fn();
    };
    const abort = () => {
      xhr.abort();
      finish(() =>
        reject(new DOMException("上传已取消", "AbortError")),
      );
    };
    xhr.open("PUT", `${apiBase()}${path}`);
    xhr.withCredentials =
      typeof location === "undefined" ||
      new URL(`${apiBase()}${path}`, location.href).origin === location.origin;
    Object.entries(headers).forEach(([key, value]) =>
      xhr.setRequestHeader(key, value),
    );
    xhr.upload.onprogress = (event) => {
      if (!settled && event.lengthComputable && event.total > 0)
        onProgress?.(event.loaded, event.total);
    };
    xhr.onload = () =>
      finish(() => {
        let json: unknown = null;
        try {
          json = JSON.parse(xhr.responseText);
        } catch {
          reject(
            new ApiError(
              "INVALID_RESPONSE",
              "分片响应无法确认，请核实上传状态",
              xhr.status,
            ),
          );
          return;
        }
        const envelope = json as { data?: UploadSessionView; error?: { code: string; message: string } };
        if (xhr.status >= 200 && xhr.status < 300 && envelope.data) {
          resolve(envelope.data);
          return;
        }
        reject(
          new ApiError(
            envelope.error?.code || "UPLOAD_CHUNK_FAILED",
            envelope.error?.message || "分片上传未完成，请继续上传",
            xhr.status,
          ),
        );
      });
    xhr.onerror = () =>
      finish(() =>
        reject(
          new ApiError(
            "NETWORK_ERROR",
            "分片上传连接中断，请继续上传；已接收分片不会重复计费",
            0,
          ),
        ),
      );
    xhr.onabort = () =>
      finish(() => reject(new DOMException("上传已取消", "AbortError")));
    xhr.ontimeout = () =>
      finish(() =>
        reject(
          new ApiError("TIMEOUT", "分片上传超时，请继续上传", 0),
        ),
      );
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) abort();
    else xhr.send(chunk);
  });
}

export async function createUploadSession(
  file: File,
  sha256: string,
  options: { purpose?: string; excelOptions?: ExcelOptions } = {},
): Promise<UploadSessionView> {
  return mutation<UploadSessionView>(
    mediaPaths.createSession,
    uploadSessionCreateBody(file, sha256, options),
  );
}

export async function getUploadSession(
  id: string,
  signal?: AbortSignal,
): Promise<UploadSessionView> {
  return request<UploadSessionView>(mediaPaths.session(id), { signal });
}

export async function resumeUploadSession(
  id: string,
  file: File,
  options: {
    sha256: string;
    signal?: AbortSignal;
    onSession?: (session: UploadSessionView) => void;
    onProgress?: (receivedBytes: number, totalBytes: number) => void;
  },
): Promise<AssetUploadResult> {
  let session = await getUploadSession(id, options.signal);
  options.onSession?.(session);
  options.onProgress?.(session.receivedBytes, file.size);
  if (session.status === "CANCELLED" || session.status === "EXPIRED")
    throw new ApiError(
      "UPLOAD_SESSION_CLOSED",
      "上传会话已结束，请重新开始上传",
      409,
    );
  while (session.status === "UPLOADING" && session.nextOffset < file.size) {
    const start = session.nextOffset;
    const end = Math.min(file.size, start + session.chunkSize);
    const chunk = file.slice(start, end);
    const chunkSha = await sha256File(
      new File([chunk], `${file.name}.part`),
    );
    options.signal?.throwIfAborted();
    const updated = await putChunkRaw(
      mediaPaths.chunks(session.id, start),
      chunk,
      chunkSha,
      {
        Accept: "application/json",
        "Content-Type": "application/octet-stream",
        "Idempotency-Key": chunkKey(session.id, start, chunkSha),
        "X-Chunk-SHA256": chunkSha,
        ...(csrfToken() ? { "X-CSRF-Token": csrfToken() } : {}),
      },
      options.signal,
      (loaded, total) =>
        options.onProgress?.(start + Math.min(loaded, total), file.size),
    );
    session = updated;
    options.onSession?.(session);
    options.onProgress?.(session.receivedBytes, file.size);
  }
  if (session.status === "CANCELLED" || session.status === "EXPIRED")
    throw new ApiError(
      "UPLOAD_SESSION_CLOSED",
      "上传会话已结束，请重新开始上传",
      409,
    );
  return mutation<AssetUploadResult>(mediaPaths.complete(session.id), {
    sha256: options.sha256,
  });
}

/**
 * Upload a file with resumable chunks. When `sessionId` is supplied (same-page
 * retry or a caller-restored server draft), the existing session is resumed from
 * its authoritative nextOffset instead of restarting.
 */
export async function uploadResumable(
  file: File,
  options: {
    purpose?: string;
    excelOptions?: ExcelOptions;
    sessionId?: string;
    signal?: AbortSignal;
    onSession?: (session: UploadSessionView) => void;
    onProgress?: (receivedBytes: number, totalBytes: number) => void;
  } = {},
): Promise<AssetUploadResult> {
  const sha256 = await sha256File(file);
  if (options.sessionId)
    return resumeUploadSession(options.sessionId, file, {
      sha256,
      signal: options.signal,
      onSession: options.onSession,
      onProgress: options.onProgress,
    });
  const session = await createUploadSession(file, sha256, {
    purpose: options.purpose,
    excelOptions: options.excelOptions,
  });
  options.onSession?.(session);
  return resumeUploadSession(session.id, file, {
    sha256,
    signal: options.signal,
    onSession: options.onSession,
    onProgress: options.onProgress,
  });
}

export async function cancelUploadSession(id: string) {
  return request<UploadSessionView>(mediaPaths.session(id), {
    method: "DELETE",
  });
}

export async function getAsset(
  id: string,
  signal?: AbortSignal,
): Promise<AssetView> {
  return request<AssetView>(mediaPaths.asset(id), { signal });
}

export async function retryAsset(id: string): Promise<AssetView> {
  return mutation<AssetView>(mediaPaths.retryAsset(id), {});
}

export async function cancelAsset(id: string): Promise<AssetView> {
  return mutation<AssetView>(mediaPaths.cancelAsset(id), {});
}
