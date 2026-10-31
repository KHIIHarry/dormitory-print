import type { Config, PrintFile, Role } from "./types";
export function parseRanges(value: string, total: number) {
  if (!value.trim()) throw new Error("请输入页码范围");
  const seen = new Set<number>();
  return value.split(/[,，]/).map((raw) => {
    const part = raw.trim();
    if (!/^\d+(?:\s*-\s*\d+)?$/.test(part))
      throw new Error("页码格式错误，请使用 1-4,7,9-12");
    const [from, to = from] = part.split("-").map(Number);
    if (from < 1 || to < from || to > total)
      throw new Error(`页码应在 1–${total} 之间，且起始页不大于结束页`);
    for (let i = from; i <= to; i++) {
      if (seen.has(i)) throw new Error(`第 ${i} 页重复选择`);
      seen.add(i);
    }
    return { from, to };
  });
}
export function expandRanges(value: string, total: number) {
  return parseRanges(value, total).flatMap((r) =>
    Array.from({ length: r.to - r.from + 1 }, (_, i) => r.from + i),
  );
}
export function productionCount(pages: number, copies: number, sides: string) {
  if (
    !Number.isInteger(pages) ||
    pages < 1 ||
    !Number.isInteger(copies) ||
    copies < 1 ||
    copies > 999
  )
    throw new Error("页数和份数必须是有效正整数");
  return sides === "DUPLEX"
    ? {
        sheets: Math.ceil(pages / 2) * copies,
        pdfPages: Math.ceil(pages / 2) * 2 * copies,
        blankBacks: pages % 2 ? copies : 0,
      }
    : { sheets: pages * copies, pdfPages: pages * copies, blankBacks: 0 };
}
export function validateAllocation(
  total: number,
  segments: number[][],
  excluded: number[],
) {
  const errors: string[] = [];
  const seen = new Set<number>();
  for (const p of segments.flat()) {
    if (p < 1 || p > total) errors.push(`第 ${p} 页越界`);
    if (seen.has(p)) errors.push(`第 ${p} 页被重复分配`);
    seen.add(p);
  }
  for (const p of excluded) {
    if (seen.has(p)) errors.push(`第 ${p} 页同时被打印与跳过`);
    if (p < 1 || p > total) errors.push(`跳过页 ${p} 越界`);
  }
  for (let p = 1; p <= total; p++)
    if (!seen.has(p) && !excluded.includes(p))
      errors.push(`第 ${p} 页未分配，请明确跳过或添加到分段`);
  return errors;
}
export function fileErrors(file: PrintFile) {
  if (file.imagePrintMedium === "PENDING") return ["请先为图片选择打印纸张"];
  if (file.status !== "READY") return ["文件尚未准备就绪"];
  if (file.kind === "PHOTO")
    return file.photo.copies < 1 ? ["照片份数必须大于 0"] : [];
  try {
    for (const s of file.segments)
      productionCount(
        expandRanges(s.range, file.pages).length,
        s.copies,
        s.sides,
      );
    return validateAllocation(
      file.pages,
      file.segments.map((s) => expandRanges(s.range, file.pages)),
      file.excludedPages,
    );
  } catch (e) {
    return [(e as Error).message];
  }
}
export function filePrice(file: PrintFile, config: Config) {
  if (file.kind === "PHOTO")
    return (config.prices.photo ?? 0) * file.photo.copies;
  return (
    file.segments.reduce((sum, s) => {
      try {
        const n = expandRanges(s.range, file.pages).length;
        const key =
          s.colorMode === "BW"
            ? s.sides === "SINGLE"
              ? "bwSingle"
              : "bwDuplex"
            : s.sides === "SINGLE"
              ? "colorSingle"
              : "colorDuplex";
        return (
          sum +
          productionCount(n, s.copies, s.sides).sheets *
            (config.prices[key] ?? 0)
        );
      } catch {
        return sum;
      }
    }, 0) +
    (file.bindingType !== "NONE"
      ? (config.prices[file.bindingType === "CORNER" ? "corner" : "side"] ??
          0) * (file.segments[0]?.copies || 1)
      : 0)
  );
}
export function canPack(
  files: { printStatus: string; bindingStatus: string }[],
) {
  return (
    files.length > 0 &&
    files.every(
      (f) =>
        f.printStatus === "COMPLETED" &&
        ["COMPLETED", "NOT_REQUIRED"].includes(f.bindingStatus),
    )
  );
}
export function batchPrimaryAction(status: string): "PUBLISH" | "START" | null {
  if (status === "PLANNED" || status === "POSTPONED") return "PUBLISH";
  if (status === "PUBLISHED") return "START";
  return null;
}

export function canStartBatch(
  orders: { packingStatus: string; addressReview: boolean }[],
) {
  return (
    orders.length > 0 &&
    orders.every((o) => o.packingStatus === "PACKED" && !o.addressReview)
  );
}
export function validateProof(
  role: Role,
  input: {
    photo: boolean;
    skipPhotoReason: string;
    position: boolean;
    skipLocationReason: string;
  },
) {
  const errors: string[] = [];
  if (!input.photo && !input.skipPhotoReason.trim())
    errors.push("请上传交付照片或填写有效的免拍照原因");
  if (!input.position) {
    if (role !== "ADMIN") errors.push("配送员必须获取真实定位");
    else if (!input.skipLocationReason.trim())
      errors.push("管理员跳过定位必须填写原因");
  }
  return errors;
}
export function pricingErrors(file: PrintFile, config: Config) {
  if (file.imagePrintMedium === "PENDING") return ["请先为图片选择打印纸张"];
  const keys: (keyof Config["prices"])[] =
    file.kind === "PHOTO"
      ? ["photo"]
      : file.segments.map((s) =>
          s.colorMode === "BW"
            ? s.sides === "SINGLE"
              ? "bwSingle"
              : "bwDuplex"
            : s.sides === "SINGLE"
              ? "colorSingle"
              : "colorDuplex",
        );
  if (file.bindingType !== "NONE")
    keys.push(file.bindingType === "CORNER" ? "corner" : "side");
  const errors = keys.some(
    (k) => config.prices[k] === null || config.prices[k] === undefined,
  )
    ? ["当前打印单价尚未配置，暂不能提交订单"]
    : [];
  if (file.bindingType !== "NONE" && !config.bindingApproved)
    errors.push("装订规则尚未定版，暂不可接单");
  return errors;
}
