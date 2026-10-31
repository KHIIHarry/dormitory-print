import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  ArrowLeft,
  ArrowRight,
  ChevronDown,
  CloudUpload,
  FileText,
  FolderOpen,
  Image,
  Plus,
  RefreshCw,
  ShieldCheck,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { DEMO, useStore } from "../lib/store";
import { request, isUncertainSubmission } from "../lib/api";
import { getIdentityGeneration } from "../growth/client";
import {
  DEFAULT_FORMATS,
  fileExtension,
  isHeicUpload,
  isSpreadsheetUpload,
  isV1ImageUpload,
  cancelAsset,
  cancelUploadSession,
  loadNormalizedImage,
  supportsFormat,
  uploadResumable,
  type AssetUploadResult,
  type AssetView,
  type ExcelOptions,
} from "../v1/upload-client";
import {
  imageSource,
  canChooseImageMedium,
  imageToA4File,
  isImageUpload,
  selectImagePrintMedium,
  type ImagePrintMedium,
} from "../lib/image-print";
import "./image-medium.css";
import type { PrintFile } from "../lib/types";
import {
  Button,
  Empty,
  Field,
  Notice,
  PageHeading,
  Panel,
  Modal,
  Steps,
} from "../components/ui";
import {
  UploadOperations,
  uploadProgress,
  retryStage,
  interruptUploads,
  a4Attempt,
} from "../lib/upload-chain";
import { UploadFileStatus, UploadFileRow } from "./upload-status";
import { useIdentity } from "../growth/identity";
import type { Artifact } from "../lib/artifacts";
export function UploadPage() {
  const { draft, setDraft, data, toast } = useStore();
  const identity = useIdentity();
  const [sessionReady, setSessionReady] = useState(DEMO);
  const uploadReady = identity.customerReady && sessionReady;
  const nav = useNavigate();
  const [params] = useSearchParams();
  const photo = params.get("type") === "photo";
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);
  const [showUploadStatus, setShowUploadStatus] = useState(false);
  useEffect(() => {
    setShowUploadStatus(false);
    if (!busy) return;
    const timer = window.setTimeout(() => setShowUploadStatus(true), 350);
    return () => window.clearTimeout(timer);
  }, [busy]);
  const operations = useRef(new UploadOperations());
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    const generation = getIdentityGeneration();
    const invalidate = () => {
      setSessionReady(false);
      operations.current.clear();
      setChoices([]);
      uploadLock.current = false;
      setBusy(false);
      setChoiceBusy(false);
      choiceLock.current = false;
    };
    window.addEventListener("dormprint:identity", invalidate);
    return () => {
      mounted.current = false;
      operations.current.clear();
      if (generation === getIdentityGeneration()) setDraft(interruptUploads);
      window.removeEventListener("dormprint:identity", invalidate);
    };
  }, []);
  const input = useRef<HTMLInputElement>(null);
  const [sample, setSample] = useState(false);
  const draftRef = useRef(draft);
  draftRef.current = draft;
  // Same-page resume state. Never files/contact data and never localStorage.
  const uploadSessions = useRef(new Map<string, string>());
  const excelOptions = useRef(new Map<File, ExcelOptions>());
  const [excelQueue, setExcelQueue] = useState<File[]>([]);
  const [excelForm, setExcelForm] = useState<ExcelOptions>({});
  const [excelError, setExcelError] = useState("");
  const pendingBatch = useRef<{
    files: File[];
    retryFile?: PrintFile;
  } | null>(null);
  useEffect(() => {
    if (DEMO) { setSessionReady(true); return; }
    setSessionReady(false);
    if (!identity.customerReady) return;
    let live = true;
    const generation = getIdentityGeneration();
    // Resolve account hydration first, then establish the matching upload
    // session. Otherwise hydration can invalidate an upload already in flight.
    void request("/customer/session", { method: "POST", body: {} })
      .then(() => {
        if (!live || !mounted.current || generation !== getIdentityGeneration()) return;
        setSessionReady(true);
      })
      .catch((error) => {
        if (live && mounted.current && generation === getIdentityGeneration())
          toast(`客户会话暂不可用：${(error as Error).message}。未上传的本地文件不会保存在浏览器，请联网后重新选择。`);
      });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [identity.customerReady, identity.generation]);
  const uploadLock = useRef(false),
    choiceLock = useRef(false);
  const [cancelBusy, setCancelBusy] = useState<string | null>(null);
  const [cancelIssue, setCancelIssue] = useState<Record<string, string>>({});
  const lastCancel = useRef<Map<string, () => Promise<void>>>(new Map());
  const [choices, setChoices] = useState<string[]>(() =>
    draft
      .filter((f) => f.imagePrintMedium === "PENDING" && f.status === "READY")
      .map((f) => f.id),
  );
  const [choiceBusy, setChoiceBusy] = useState(false),
    [choiceError, setChoiceError] = useState("");
  const choiceFile = !busy
    ? draft.find((f) => f.id === choices[0] && canChooseImageMedium(f))
    : undefined;
  const queueChoice = (id: string) =>
    setChoices((ids) => (ids.includes(id) ? ids : [...ids, id]));
  const chooseMedium = async (file: PrintFile, medium: ImagePrintMedium) => {
    if (choiceLock.current) return;
    choiceLock.current = true;
    setChoiceBusy(true);
    setChoiceError("");
    const generation = getIdentityGeneration();
    const operation = operations.current.start(file.id);
    const checkIdentity = () => {
      if (
        !mounted.current ||
        !operation.current() ||
        generation !== getIdentityGeneration()
      )
        throw new Error("身份已变更，请重新选择打印纸张");
    };
    try {
      let next: PrintFile;
      if (medium === "A4") {
        const source = imageSource(file);
        if (!source) throw new Error("原图暂不可用，请重新上传");
        const attempt = a4Attempt(file);
        // Retain the key before asynchronous browser conversion, and retain the exact
        // converted bytes before submission so retry cannot change the payload.
        setDraft((files) =>
          files.map((f) =>
            f.id === file.id ? { ...f, a4UploadAttempt: attempt } : f,
          ),
        );
        const converted = attempt.file || (await imageToA4File(source.file));
        checkIdentity();
        if (converted.size > data.config.maxMb * 1024 * 1024)
          throw new Error(
            `转换后的A4文件超过${data.config.maxMb} MB，请压缩原图后重试，或选择照片纸`,
          );
        setDraft((files) =>
          files.map((f) =>
            f.id === file.id
              ? { ...f, a4UploadAttempt: { ...attempt, file: converted } }
              : f,
          ),
        );
        let asset = {
          assetId: `demo-a4-${crypto.randomUUID()}`,
          status: "READY" as PrintFile["status"],
        };
        if (!DEMO) {
          const body = new FormData();
          body.append("file", converted);
          body.append("purpose", "PRINT_SOURCE");
          const result = await request<{
            assetId: string;
            status: string;
            pages?: number;
          }>("/assets", {
            method: "POST",
            body,
            signal: operation.signal,
            key: attempt.key,
          });
          checkIdentity();
          if (
            !result.assetId ||
            (result.status === "READY" &&
              result.pages !== undefined &&
              result.pages !== 1)
          )
            throw new Error("A4文件处理结果不一致，请重新上传");
          asset = {
            assetId: result.assetId,
            status: result.status === "READY" ? "READY" : "PROCESSING",
          };
        }
        next = selectImagePrintMedium(file, medium, {
          file: converted,
          ...asset,
        });
      } else {
        checkIdentity();
        next = selectImagePrintMedium(file, medium);
      }
      checkIdentity();
      next.a4UploadAttempt = undefined;
      next.uploadPhase = next.status === "READY" ? "READY" : "PROCESSING";
      next.uploadVersion = (file.uploadVersion || 0) + 1;
      setDraft((files) => files.map((f) => (f.id === file.id ? next : f)));
      setChoices((ids) => ids.filter((id) => id !== file.id));
    } catch (error) {
      if (operation.current() && generation === getIdentityGeneration())
        setChoiceError(
          isUncertainSubmission(error)
            ? "A4文件准备结果待核实，请重试核实"
            : (error as Error).message,
        );
    } finally {
      if (operation.current()) operations.current.cancel(file.id);
      if (mounted.current && generation === getIdentityGeneration()) {
        choiceLock.current = false;
        setChoiceBusy(false);
      }
    }
  };
  useEffect(() => {
    const fn = (e: BeforeUnloadEvent) => {
      if (draft.length) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", fn);
    return () => window.removeEventListener("beforeunload", fn);
  }, [draft.length]);
  const update = (id: string, patch: Partial<PrintFile>) =>
    setDraft((s) => s.map((f) => (f.id === id ? { ...f, ...patch } : f)));
  // Build a displayable local source from the server's authorized normalized
  // PNG. Raw HEIC bytes are never treated as a decodable image.
  const attachNormalizedImage = async (
    id: string,
    asset: AssetView,
    originalName: string,
    signal?: AbortSignal,
  ) => {
    if (asset.status !== "READY") return false;
    const display = await loadNormalizedImage(asset, originalName, signal);
    if (!display) return false;
    const current = draftRef.current.find((f) => f.id === id);
    update(id, {
      localFile: display.file,
      url: display.url,
      imageSource: {
        file: display.file,
        url: display.url,
        assetId: asset.assetId,
      },
      photo: {
        ...(current?.photo || {
          fit: "CONTAIN",
          rotationDegrees: 0,
          orientation: "PORTRAIT",
          copies: 1,
          zoom: 1,
          x: 50,
          y: 50,
        }),
        sourceWidth: display.width,
        sourceHeight: display.height,
      },
    });
    return true;
  };
  const beginUpload = (files: File[], retryFile?: PrintFile) => {
    if (!uploadReady) { toast("正在准备上传，请稍候重试"); return; }
    const needsOptions = files.filter(
      (file) =>
        isSpreadsheetUpload(file) && !excelOptions.current.has(file),
    );
    if (!DEMO && needsOptions.length) {
      pendingBatch.current = { files, retryFile };
      setExcelQueue(needsOptions);
      setExcelForm({ fitToWidth: 1, fitToHeight: 0, gridlines: false });
      setExcelError("");
      return;
    }
    void upload(files, retryFile);
  };
  const confirmExcelOptions = () => {
    if (!excelQueue.length) return;
    if (
      excelForm.printArea?.trim() &&
      !/^[A-Za-z]{1,3}\d{1,7}(:[A-Za-z]{1,3}\d{1,7})?$/.test(
        excelForm.printArea.trim(),
      )
    ) {
      setExcelError("打印区域格式无效，请使用类似 A1:F40 的区域。");
      return;
    }
    const [current, ...rest] = excelQueue;
    const normalized: ExcelOptions = {
      ...(excelForm.sheetName?.trim()
        ? { sheetName: excelForm.sheetName.trim() }
        : {}),
      ...(excelForm.printArea?.trim()
        ? { printArea: excelForm.printArea.trim().toUpperCase() }
        : {}),
      ...(typeof excelForm.fitToWidth === "number"
        ? { fitToWidth: excelForm.fitToWidth }
        : {}),
      ...(typeof excelForm.fitToHeight === "number"
        ? { fitToHeight: excelForm.fitToHeight }
        : {}),
      ...(excelForm.orientation ? { orientation: excelForm.orientation } : {}),
      gridlines: !!excelForm.gridlines,
    };
    excelOptions.current.set(current, normalized);
    setExcelQueue(rest);
    setExcelError("");
    if (rest.length) {
      setExcelForm({ fitToWidth: 1, fitToHeight: 0, gridlines: false });
      return;
    }
    const batch = pendingBatch.current;
    pendingBatch.current = null;
    if (batch) void upload(batch.files, batch.retryFile);
  };
  const skipExcelOptions = () => {
    if (!excelQueue.length) return;
    const [current, ...rest] = excelQueue;
    excelOptions.current.set(current, { fitToWidth: 1, fitToHeight: 0 });
    setExcelQueue(rest);
    setExcelError("");
    if (rest.length) {
      setExcelForm({ fitToWidth: 1, fitToHeight: 0, gridlines: false });
      return;
    }
    const batch = pendingBatch.current;
    pendingBatch.current = null;
    if (batch) void upload(batch.files, batch.retryFile);
  };
  const upload = async (files: FileList | File[], retryFile?: PrintFile) => {
    if (!navigator.onLine) {
      toast("离线时无法上传，请联网后重试");
      return;
    }
    if (uploadLock.current) return;
    const items = Array.from(files);
    if (
      items.length + draftRef.current.length - (retryFile ? 1 : 0) >
      data.config.maxFiles
    ) {
      toast(`每单最多选择 ${data.config.maxFiles} 个文件`);
      return;
    }
    uploadLock.current = true;
    const generation = getIdentityGeneration();
    const active = () =>
      mounted.current && generation === getIdentityGeneration();
    setBusy(true);
    const queued: PrintFile[] = [];
    const failures: string[] = [];
    const configuredFormats = (
      data.config as unknown as { formats?: string[] }
    ).formats;
    for (const raw of items) {
      if (!supportsFormat(raw.name, configuredFormats || DEFAULT_FORMATS)) {
        failures.push(`${raw.name}：暂不支持此格式`);
        continue;
      }
      if (raw.size > data.config.maxMb * 1024 * 1024) {
        failures.push(`${raw.name} 超过 ${data.config.maxMb} MB`);
        continue;
      }
      const heic = isHeicUpload(raw);
      if (DEMO && heic) {
        failures.push(
          `${raw.name}：演示模式不提供 HEIC 解码；请选择 PDF 或 JPG/PNG，正式模式由服务端规范化后提供预览`,
        );
        continue;
      }
      const kind = isV1ImageUpload(raw) ? "PHOTO" : "DOCUMENT";
      const id = retryFile?.id || crypto.randomUUID();
      const file: PrintFile = {
        id,
        assetId: "",
        name: raw.name,
        size: raw.size,
        pages: 0,
        kind,
        status: "UPLOADING",
        progress: undefined,
        uploadPhase: "QUEUED",
        uploadVersion: (retryFile?.uploadVersion || 0) + 1,
        uploadRequestKey: retryFile?.uploadRequestKey || crypto.randomUUID(),
        localFile: raw,
        url: retryFile?.url || URL.createObjectURL(raw),
        segments: [],
        excludedPages: [],
        bindingType: "NONE",
        photo: {
          fit: "CONTAIN",
          rotationDegrees: 0,
          orientation: "PORTRAIT",
          copies: 1,
          zoom: 1,
          x: 50,
          y: 50,
        },
        printStatus: "PENDING",
        bindingStatus: "NOT_REQUIRED",
      };
      if (kind === "PHOTO") {
        file.imagePrintMedium = "PENDING";
        // HEIC is not reliably decodable in browsers: do not create an image
        // source from raw bytes. It is set from the server's normalized PNG.
        file.imageSource = heic
          ? undefined
          : { file: raw, url: file.url, assetId: "" };
        if (heic) file.url = undefined;
      }
      queued.push(file);
    }
    const queuedOperations = new Map(
      queued.map((file) => [file.id, operations.current.start(file.id)]),
    );
    setDraft((s) =>
      active() ? [...s.filter((f) => f.id !== retryFile?.id), ...queued] : s,
    );
    for (const file of queued) {
      if (!active()) break;
      const raw = file.localFile!;
      const { id, kind } = file;
      const ext = raw.name.split(".").pop()?.toLowerCase();
      const heic = isHeicUpload(raw);
      const operation = queuedOperations.get(id)!;
      if (!operation.current()) continue;
      const checkIdentity = () => {
        if (!active() || !operation.current())
          throw new Error("上传任务已结束");
      };
      const patch = (value: Partial<PrintFile>) => {
        if (active() && operation.current()) update(id, value);
      };
      try {
        patch({ uploadPhase: "READING" });
        checkIdentity();
        if (kind === "PHOTO" && !heic) {
          const bitmap = await createImageBitmap(raw, {
            imageOrientation: "from-image",
          });
          file.photo.sourceWidth = bitmap.width;
          file.photo.sourceHeight = bitmap.height;
          bitmap.close();
          checkIdentity();
          patch({ photo: { ...file.photo } });
        }
        let pages = 1;
        if (DEMO) {
          patch({
            status: "PROCESSING",
            uploadPhase: "PROCESSING",
            progress: undefined,
          });
          if (kind === "DOCUMENT") {
            if (ext !== "pdf")
              throw new Error(
                "演示模式不提供 Office 转换，请选择 PDF；正式模式由服务端转换",
              );
            const pdf = await import("../lib/pdf");
            pages = await pdf.getPageCount(raw);
          }
          patch({
            pages,
            assetId: `demo-${id}`,
            status: "READY",
            uploadPhase: "READY",
            ...(kind === "PHOTO"
              ? { imageSource: { ...file.imageSource!, assetId: `demo-${id}` } }
              : {}),
            segments: [
              {
                id: crypto.randomUUID(),
                range: pages === 1 ? "1" : `1-${pages}`,
                colorMode: "BW",
                sides: "DUPLEX",
                flip: "LONG_EDGE",
                copies: 1,
              },
            ],
          });
          checkIdentity();
          if (kind === "PHOTO") queueChoice(id);
        } else {
          checkIdentity();
          const resumable =
            (
              data.config as unknown as {
                features?: { resumableUploads?: boolean };
              }
            ).features?.resumableUploads !== false;
          const excel =
            isSpreadsheetUpload(raw) && excelOptions.current.has(raw)
              ? excelOptions.current.get(raw)
              : undefined;
          patch({ uploadPhase: "TRANSFERRING" });
          let asset: AssetUploadResult;
          if (resumable) {
            const sessionKey = file.uploadRequestKey || id;
            asset = await uploadResumable(raw, {
              purpose: "PRINT_SOURCE",
              excelOptions: excel,
              sessionId: uploadSessions.current.get(sessionKey),
              signal: operation.signal,
              onSession: (session) =>
                uploadSessions.current.set(sessionKey, session.id),
              onProgress: (received, total) => {
                if (received >= total)
                  patch({ uploadPhase: "AWAITING_RESPONSE", progress: 100 });
                else patch(uploadProgress({ loaded: received, total }));
              },
            });
            checkIdentity();
          } else {
            const body = new FormData();
            body.append("file", raw);
            body.append("purpose", "PRINT_SOURCE");
            if (excel) body.append("excelOptions", JSON.stringify(excel));
            asset = await request<AssetUploadResult>("/assets", {
              method: "POST",
              body,
              signal: operation.signal,
              key: file.uploadRequestKey,
              onUploadProgress: (value: { loaded: number; total?: number }) =>
                patch(uploadProgress(value)),
              onUploadComplete: () =>
                patch({ uploadPhase: "AWAITING_RESPONSE" }),
            });
            checkIdentity();
          }
          if (!asset.assetId) throw new Error("文件处理结果不一致，请重试核实");
          const ready = asset.status === "READY";
          let normalizedReady = !heic;
          let previewIssue: string | undefined;
          if (ready && kind === "PHOTO" && heic) {
            const view = await request<AssetView>(
              `/assets/${encodeURIComponent(asset.assetId)}`,
              { signal: operation.signal },
            );
            checkIdentity();
            normalizedReady = await attachNormalizedImage(
              id,
              view,
              raw.name,
              operation.signal,
            );
            if (!normalizedReady)
              previewIssue =
                "服务端已收到 HEIC，但暂未提供可显示的 PNG 预览（normalizedUrl/previewUrl）；处理结果不会被当作可显示图片，请稍后重试预览。";
          }
          patch({
            assetId: asset.assetId,
            size: asset.size,
            uploadPhase: ready ? "READY" : "PROCESSING",
            error: previewIssue,
            ...(kind === "PHOTO" && !heic
              ? {
                  imageSource: { ...file.imageSource!, assetId: asset.assetId },
                }
              : {}),
            status: ready ? "READY" : "PROCESSING",
            pages: kind === "PHOTO" ? 1 : asset.pages || 0,
            segments:
              ready && asset.pages
                ? [
                    {
                      id: crypto.randomUUID(),
                      range: `1-${asset.pages}`,
                      colorMode: "BW",
                      sides: "DUPLEX",
                      flip: "LONG_EDGE",
                      copies: 1,
                    },
                  ]
                : [],
            progress: 100,
          });
          if (kind === "PHOTO" && ready && normalizedReady) queueChoice(id);
        }
      } catch (e) {
        const code = (e as { code?: string }).code;
        if (
          code === "UPLOAD_SESSION_CLOSED" ||
          code === "UPLOAD_SESSION_EXPIRED" ||
          code === "CHUNK_CONFLICT"
        )
          uploadSessions.current.delete(file.uploadRequestKey || id);
        if (active() && operation.current()) {
          patch({
            status: "FAILED",
            uploadPhase: "FAILED",
            error: isUncertainSubmission(e)
              ? "上传结果待核实，请重试核实"
              : (e as Error).message,
          });
          failures.push(`${file.name}：${(e as Error).message}`);
        }
      } finally {
        if (operation.current()) operations.current.cancel(id);
      }
    }
    if (active()) {
      uploadLock.current = false;
      setBusy(false);
      if (failures.length) toast(failures.join("；"));
    }
  };
  const sampleFile = async () => {
    const generation = getIdentityGeneration();
    const m = await import("../lib/demo");
    if (!mounted.current || generation !== getIdentityGeneration()) return;
    const f = m.makeFile("英语期末复习资料.pdf");
    setDraft((s) => [...s, f]);
    setSample(false);
  };
  const remove = (f: PrintFile) => {
    operations.current.cancel(f.id);
    if (f.url) URL.revokeObjectURL(f.url);
    setDraft((s) => s.filter((x) => x.id !== f.id));
    setChoices((ids) => ids.filter((id) => id !== f.id));
  };
  const retry = async (f: PrintFile) => {
    if (f.a4UploadAttempt) {
      await chooseMedium(f, "A4");
      return;
    }
    if (
      !DEMO &&
      f.status === "READY" &&
      f.kind === "PHOTO" &&
      !f.imageSource &&
      f.assetId
    ) {
      const operation = operations.current.start(f.id);
      const generation = getIdentityGeneration();
      try {
        const asset = await request<AssetView>(
          `/assets/${encodeURIComponent(f.assetId)}`,
          { signal: operation.signal },
        );
        const ready = await attachNormalizedImage(
          f.id,
          asset,
          f.name,
          operation.signal,
        );
        if (!operation.current() || generation !== getIdentityGeneration())
          return;
        if (!ready)
          throw new Error(
            "服务端仍未提供可显示的 PNG 预览，暂时不能选择纸张；请稍后再试或重新选择文件。",
          );
        update(f.id, {
          status: "READY",
          uploadPhase: "READY",
          error: undefined,
        });
        queueChoice(f.id);
      } catch (e) {
        if (operation.current() && generation === getIdentityGeneration())
          update(f.id, { error: (e as Error).message });
      } finally {
        if (operation.current()) operations.current.cancel(f.id);
      }
      return;
    }
    if (
      !DEMO &&
      f.status === "PROCESSING" &&
      f.error &&
      f.assetId
    ) {
      // A transient status-poll failure is not a failed worker task; clear the
      // message and let the existing poll resume against the same asset.
      update(f.id, { error: undefined });
      return;
    }
    if (DEMO || retryStage(f) === "UPLOAD") {
      if (f.localFile) {
        if (uploadLock.current) return;
        beginUpload([f.localFile], f);
      } else {
        // A restored draft has no local bytes (Q27: never persisted in the
        // browser). The only honest action is asking for a new selection.
        toast(
          "本地原文件已不可用。为保护隐私不会从浏览器恢复文件，请重新选择该文件上传。",
        );
      }
      return;
    }
    const operation = operations.current.poll(f.id);
    if (!operation) return;
    const generation = getIdentityGeneration();
    try {
      await request(`/assets/${f.assetId}/retry`, {
        method: "POST",
        body: {},
        signal: operation.signal,
      });
      if (!operation.current() || generation !== getIdentityGeneration())
        return;
      update(f.id, {
        status: "PROCESSING",
        uploadPhase: "PROCESSING",
        uploadVersion: (f.uploadVersion || 0) + 1,
        error: undefined,
      });
      operations.current.cancel(f.id);
    } catch (e) {
      if (operation.current() && generation === getIdentityGeneration())
        toast((e as Error).message);
      operations.current.cancel(f.id);
    }
  };
  const cancelFile = (f: PrintFile) => {
    const key = f.uploadRequestKey || f.id;
    const action = async () => {
      if (!navigator.onLine) {
        setCancelIssue((issue) => ({
          ...issue,
          [f.id]: "当前离线，联网后再取消任务。",
        }));
        return;
      }
      setCancelBusy(f.id);
      setCancelIssue((issue) => ({ ...issue, [f.id]: "" }));
      try {
        if (f.assetId) {
          const view = await cancelAsset(f.assetId);
          if (view.status === "FAILED")
            update(f.id, {
              status: "FAILED",
              uploadPhase: "FAILED",
              error:
                view.failureCode === "CANCELED"
                  ? "任务已取消（服务端最终结果）"
                  : view.error || "任务已结束",
            });
          else if (view.status === "READY")
            update(f.id, {
              status: "READY",
              uploadPhase: "READY",
              error: "处理已完成，服务端未取消该任务。",
            });
          else
            update(f.id, {
              status: "PROCESSING",
              uploadPhase: "PROCESSING",
              error:
                "取消申请已登记，等待当前处理结束；最终是否取消以服务端结果为准。",
            });
        } else {
          operations.current.cancel(f.id);
          const sessionId = uploadSessions.current.get(key);
          if (sessionId) {
            const session = await cancelUploadSession(sessionId);
            if (session.status === "COMPLETED") {
              update(f.id, {
                status: "PROCESSING",
                uploadPhase: "PROCESSING",
                error:
                  "取消时上传已完成，服务端已生成文件；请刷新核实处理状态，不能冒称已取消。",
              });
            } else {
              uploadSessions.current.delete(key);
              update(f.id, {
                status: "FAILED",
                uploadPhase: "FAILED",
                error: "上传已取消，服务端上传会话已结束。",
              });
            }
          } else {
            update(f.id, {
              status: "FAILED",
              uploadPhase: "FAILED",
              error: "已取消本地上传（尚未建立服务端上传会话）。",
            });
          }
        }
      } catch (e) {
        setCancelIssue((issue) => ({
          ...issue,
          [f.id]: isUncertainSubmission(e)
            ? "取消结果待核实：请求响应未确认，请重试取消并刷新实际状态。"
            : `取消未完成：${(e as Error).message}`,
        }));
      } finally {
        setCancelBusy(null);
        lastCancel.current.set(f.id, action);
      }
    };
    lastCancel.current.set(f.id, action);
    void action();
  };
  useEffect(() => {
    if (DEMO || !draft.some((f) => f.status === "PROCESSING")) return;
    const poll = () => {
      if (document.hidden || !navigator.onLine) return;
      draftRef.current
        .filter((f) => f.status === "PROCESSING")
        .forEach(async (f) => {
          const operation = operations.current.poll(f.id);
          if (!operation) return;
          const generation = getIdentityGeneration();
          try {
            const asset = await request<AssetView>(
              `/assets/${encodeURIComponent(f.assetId)}`,
              { signal: operation.signal },
            );
            if (!operation.current() || generation !== getIdentityGeneration())
              return;
            const current = draftRef.current.find(
              (current) => current.id === f.id && current.assetId === f.assetId,
            );
            if (!current) return;
            const heic = isHeicUpload({
              name: current.name,
              type: current.localFile?.type,
            });
            let normalizedReady = !heic || !!current.imageSource;
            if (
              asset.status === "READY" &&
              current.kind === "PHOTO" &&
              heic &&
              normalizedReady === false
            ) {
              normalizedReady = await attachNormalizedImage(
                f.id,
                asset,
                current.name,
                operation.signal,
              );
              if (!normalizedReady) {
                update(f.id, {
                  status: "READY",
                  uploadPhase: "READY",
                  pages: 1,
                  error:
                    "服务端已就绪，但暂未提供可显示的 PNG 预览；请稍后重试预览或重新选择文件",
                });
                return;
              }
            }
            update(f.id, {
              error:
                asset.status === "FAILED" && asset.failureCode === "CANCELED"
                  ? "任务已取消（服务端最终结果）"
                  : asset.error || undefined,
              uploadPhase:
                asset.status === "READY"
                  ? "READY"
                  : asset.status === "FAILED"
                    ? "FAILED"
                    : "PROCESSING",
              status:
                asset.status === "READY"
                  ? "READY"
                  : asset.status === "FAILED"
                    ? "FAILED"
                    : "PROCESSING",
              pages:
                current.imagePrintMedium === "PENDING"
                  ? 1
                  : asset.pages || 0,
              segments:
                asset.status === "READY" &&
                current.segments.length === 0 &&
                current.kind === "DOCUMENT" &&
                asset.pages
                  ? [
                      {
                        id: crypto.randomUUID(),
                        range: `1-${asset.pages}`,
                        colorMode: "BW",
                        sides: "DUPLEX",
                        flip: "LONG_EDGE",
                        copies: 1,
                      },
                    ]
                  : current.segments,
            });
            if (
              asset.status === "READY" &&
              current.imagePrintMedium === "PENDING" &&
              normalizedReady
            )
              queueChoice(f.id);
          } catch (e) {
            if (operation.current() && generation === getIdentityGeneration())
              update(f.id, { error: (e as Error).message });
          } finally {
            if (operation.current()) operations.current.cancel(f.id);
          }
        });
    };
    const t = setInterval(poll, 3000);
    document.addEventListener("visibilitychange", poll);
    window.addEventListener("online", poll);
    return () => {
      clearInterval(t);
      document.removeEventListener("visibilitychange", poll);
      window.removeEventListener("online", poll);
    };
  }, [draft]);
  return (
    <main className="flow-page">
      <Link to="/u/" className="back-link">
        <ArrowLeft size={17} />
        返回首页
      </Link>
      <PageHeading title="新建打印订单" description="从上传第一份资料开始。" />
      <Steps step={0} />
      <div className="flow-grid">
        <Panel className="upload-panel">
          <div
            className={`dropzone ${drag ? "dragging" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setDrag(true);
            }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDrag(false);
              beginUpload(Array.from(e.dataTransfer.files));
            }}
          >
            <span className="upload-folder">
              <FolderOpen size={47} strokeWidth={1.3} />
              <span>
                <Plus size={15} />
              </span>
            </span>
            <h2>把文件放在这里，开始打印</h2>
            <p role="status" aria-live="polite" aria-atomic="true">
              {busy && showUploadStatus
                ? "正在处理文件，请稍候…"
                : "拖拽文件到此处，或点击下方选择"}
            </p>
            <div className="upload-button">
              <Button
                disabled={!data.config.serviceEnabled || !uploadReady}
                aria-disabled={busy || !data.config.serviceEnabled || !uploadReady}
                aria-busy={busy || undefined}
                onClick={() => {
                  if (uploadReady && !busy && !uploadLock.current) input.current?.click();
                }}
              >
                <Upload size={17} />
                选择文件
              </Button>
              {DEMO && (
                <button
                  className="upload-menu"
                  aria-label="使用示例文件"
                  aria-disabled={busy || !data.config.serviceEnabled}
                  onClick={() => {
                    if (
                      !busy &&
                      !uploadLock.current &&
                      data.config.serviceEnabled
                    )
                      setSample(!sample);
                  }}
                >
                  <ChevronDown size={16} />
                </button>
              )}
              {sample && (
                <div className="dropdown">
                  <button onClick={sampleFile}>使用演示 PDF（12 页）</button>
                </div>
              )}
            </div>
            <input
              ref={input}
              disabled={!uploadReady || !data.config.serviceEnabled}
              type="file"
              hidden
              multiple
              accept={
                photo
                  ? "image/jpeg,image/png,image/webp,image/heic,image/heif,.heic,.heif"
                  : ".pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.jpg,.jpeg,.png,.webp,.heic,.heif"
              }
              onChange={(e) => {
                const files = Array.from(e.target.files || []);
                e.target.value = "";
                if (files.length) beginUpload(files);
              }}
            />
            <div className="supported-types">
              <span>
                <FileText size={14} />
                PDF / Word / PPT / Excel
              </span>
              <span>
                <Image size={14} />
                JPG / PNG / WebP / HEIC
              </span>
            </div>
          </div>
          <div className="selected-heading">
            <h2>
              已选择文件 <span>({draft.length})</span>
            </h2>
            {draft.length > 0 && (
              <button
                className="text-button"
                onClick={() => {
                  draft.forEach(remove);
                }}
              >
                清空
              </button>
            )}
          </div>
          {draft.length === 0 ? (
            <div className="upload-empty">
              <FileText size={22} />
              <p>还没有文件，先上传一份吧</p>
            </div>
          ) : (
            <div className="file-list">
              {draft.map((f) => (
                <div key={f.id}>
                  <UploadFileRow
                    file={f}
                    action={
                      <>
                        <UploadFileStatus file={f} />
                        {(f.status === "UPLOADING" ||
                          f.status === "PROCESSING") && (
                          <button
                            type="button"
                            className="text-button"
                            disabled={cancelBusy === f.id}
                            onClick={() => cancelFile(f)}
                          >
                            {cancelBusy === f.id ? "正在取消…" : "取消任务"}
                          </button>
                        )}
                        {canChooseImageMedium(f) && (
                          <button
                            type="button"
                            className="text-button image-medium-change"
                            aria-label={`为 ${f.name} 选择打印纸张`}
                            onClick={() => {
                              setChoiceError("");
                              queueChoice(f.id);
                            }}
                          >
                            {f.imagePrintMedium === "PENDING"
                              ? "选择纸张"
                              : "更换纸张"}
                          </button>
                        )}
                        <button
                          className="icon-btn"
                          aria-label={`移除 ${f.name}`}
                          onClick={() => remove(f)}
                        >
                          <Trash2 size={17} />
                        </button>
                      </>
                    }
                  />
                  {f.error && (
                    <div className="file-error">
                      <span>{f.error}</span>
                      <button onClick={() => retry(f)}>重试</button>
                    </div>
                  )}
                  {cancelIssue[f.id] && (
                    <div className="file-error">
                      <span>{cancelIssue[f.id]}</span>
                      <button
                        onClick={() => {
                          const action = lastCancel.current.get(f.id);
                          if (action) void action();
                        }}
                      >
                        重试取消
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
          <div className="flow-bottom">
            <span className="muted">文件将分别设置，合并为一个订单</span>
            <Button
              disabled={
                busy ||
                !draft.length ||
                draft.some(
                  (f) =>
                    f.status !== "READY" || f.imagePrintMedium === "PENDING",
                )
              }
              onClick={() => nav("/u/new/settings")}
            >
              下一步
              <ArrowRight size={17} />
            </Button>
          </div>
        </Panel>
        <aside className="flow-aside">
          <Panel title="上传前，请留意">
            <ul className="tips-list">
              <li>优先上传 PDF，避免排版变化。</li>
              <li>
                每单最多 {data.config.maxFiles} 个文件，单个不超过{" "}
                {data.config.maxMb} MB。
              </li>
              <li>
                图片上传后可选择 A4 纸或 5 寸照片纸；A4 按实际纸张收费，5
                寸照片纸仅支持图片。
              </li>
              <li>加密文件请先解除密码后重新上传。</li>
            </ul>
            <Notice>上传后还可以调整打印参数，并预览最终效果。</Notice>
          </Panel>
          <div className="privacy-note">
            <ShieldCheck size={23} />
            <div>
              <b>你的资料，妥善对待</b>
              <p>文件仅用于打印，私有资料不进入离线缓存。</p>
            </div>
          </div>
          {DEMO && (
            <div className="demo-help">
              没有文件也可以体验：点击“选择文件”右侧箭头，加载一份示例 PDF。
            </div>
          )}
        </aside>
      </div>
      <Modal
        open={!!choiceFile}
        focusKey={choiceFile?.id}
        title="选择图片打印纸张"
        onClose={() => {
          if (!choiceBusy) {
            setChoices((ids) => ids.slice(1));
            setChoiceError("");
          }
        }}
      >
        {choiceFile && (
          <div className="image-medium-dialog">
            <div className="image-medium-source">
              {imageSource(choiceFile)?.url && (
                <img
                  src={imageSource(choiceFile)!.url}
                  alt="待选择打印纸张的图片缩略图"
                />
              )}
              <div>
                <strong>{choiceFile.name}</strong>
                <p>请选择这张图片使用的纸张。</p>
              </div>
            </div>
            <div className="image-medium-options">
              <button
                type="button"
                className="image-medium-option"
                aria-label="使用 A4 纸打印"
                disabled={choiceBusy || choiceFile.imagePrintMedium === "A4"}
                onClick={() => void chooseMedium(choiceFile, "A4")}
              >
                <span className="image-medium-icon">
                  <FileText size={24} />
                </span>
                <span>
                  <strong>
                    A4 纸打印
                    {choiceFile.imagePrintMedium === "A4" ? " · 当前纸张" : ""}
                  </strong>
                  <small>
                    完整保留图片，等比居中排成一页 A4。可设置黑白 /
                    彩色、单双面和份数。
                  </small>
                  <em>按 A4 打印价目计费</em>
                </span>
                <ArrowRight size={18} />
              </button>
              <button
                type="button"
                className="image-medium-option"
                aria-label="使用 5 寸照片纸打印"
                disabled={choiceBusy || choiceFile.imagePrintMedium === "PHOTO"}
                onClick={() => void chooseMedium(choiceFile, "PHOTO")}
              >
                <span className="image-medium-icon">
                  <Image size={24} />
                </span>
                <span>
                  <strong>
                    5 寸照片纸打印
                    {choiceFile.imagePrintMedium === "PHOTO"
                      ? " · 当前纸张"
                      : ""}
                  </strong>
                  <small>
                    使用照片纸，可调整方向、裁剪和四边留白，适合相片与证件照。
                  </small>
                  <em>按照片张数计费</em>
                </span>
                <ArrowRight size={18} />
              </button>
            </div>
            {choiceBusy && (
              <p role="status" className="form-hint">
                正在准备打印文件，请稍候…
              </p>
            )}
            {choiceError && (
              <Notice type="error">
                <span role="alert">{choiceError}</span>
              </Notice>
            )}
            <p className="form-hint">
              {choices.length > 1
                ? `还有 ${choices.length - 1} 张图片待选择。`
                : ""}
              稍后也可在上传列表更换纸张；切换会重置这张图片的打印参数。
            </p>
          </div>
        )}
      </Modal>
      <Modal
        open={excelQueue.length > 0}
        focusKey={excelQueue[0]?.name}
        title="Excel 打印区域与分页"
        onClose={() => {
          pendingBatch.current = null;
          setExcelQueue([]);
          setExcelError("");
        }}
      >
        {excelQueue[0] && (
          <div className="v1-form">
            <Notice>
              服务器会按工作簿实际内容转换并分页；这里只设置打印区域与缩放。转换完成后仍须在最终 PDF 预览中逐项确认页数与排版。
            </Notice>
            <p className="form-hint">
              {excelQueue.length > 1
                ? `正在设置 ${excelQueue[0].name}（还有 ${excelQueue.length - 1} 个 Excel 文件待设置）`
                : excelQueue[0].name}
            </p>
            <Field
              label="工作表名称（选填）"
              hint="留空使用默认工作表；名称须与工作簿内完全一致。"
            >
              <input
                value={excelForm.sheetName || ""}
                maxLength={64}
                onChange={(e) =>
                  setExcelForm((form) => ({
                    ...form,
                    sheetName: e.target.value,
                  }))
                }
                placeholder="例如 Sheet1"
              />
            </Field>
            <Field
              label="打印区域（选填）"
              hint="A1 样式，例如 A1:F40；留空按工作簿打印区域/已用区域。"
            >
              <input
                value={excelForm.printArea || ""}
                onChange={(e) =>
                  setExcelForm((form) => ({
                    ...form,
                    printArea: e.target.value,
                  }))
                }
                placeholder="例如 A1:F40"
              />
            </Field>
            <div className="form-grid">
              <Field label="纸张方向">
                <select
                  value={excelForm.orientation || "PORTRAIT"}
                  onChange={(e) =>
                    setExcelForm((form) => ({
                      ...form,
                      orientation: e.target.value as "PORTRAIT" | "LANDSCAPE",
                    }))
                  }
                >
                  <option value="PORTRAIT">纵向</option>
                  <option value="LANDSCAPE">横向</option>
                </select>
              </Field>
              <Field
                label="缩放到页宽"
                hint="1 表示按一页宽；0 表示保持实际大小。"
              >
                <input
                  type="number"
                  min={0}
                  max={20}
                  value={excelForm.fitToWidth ?? 1}
                  onChange={(e) =>
                    setExcelForm((form) => ({
                      ...form,
                      fitToWidth: Number(e.target.value),
                    }))
                  }
                />
              </Field>
              <Field label="缩放到页高" hint="0 表示自动。">
                <input
                  type="number"
                  min={0}
                  max={50}
                  value={excelForm.fitToHeight ?? 0}
                  onChange={(e) =>
                    setExcelForm((form) => ({
                      ...form,
                      fitToHeight: Number(e.target.value),
                    }))
                  }
                />
              </Field>
            </div>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={!!excelForm.gridlines}
                onChange={(e) =>
                  setExcelForm((form) => ({
                    ...form,
                    gridlines: e.target.checked,
                  }))
                }
              />
              <span>打印网格线</span>
            </label>
            {excelError && (
              <div role="alert">
                <Notice type="error">{excelError}</Notice>
              </div>
            )}
            <div className="v1-actions">
              <Button onClick={confirmExcelOptions}>确认并上传</Button>
              <Button variant="secondary" onClick={skipExcelOptions}>
                使用默认分页
              </Button>
            </div>
          </div>
        )}
      </Modal>
    </main>
  );
}
