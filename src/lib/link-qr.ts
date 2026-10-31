import QRCode from "qrcode";

/** Version 40 with error-correction level M holds 2331 bytes in byte mode. */
export const QR_MAX_BYTES = 2331;

export type LinkQrKind = "order" | "invite" | "custom";

export interface QrLinkValidationOptions {
  kind: LinkQrKind;
  /** Browser origin used only to permit local HTTP order/invite previews. */
  currentOrigin?: string;
  /** Development-only escape hatch for localhost order/invite links. */
  allowLocalHttp?: boolean;
}

export interface QrImageOptions {
  width?: number;
  margin?: number;
  errorCorrectionLevel?: "L" | "M" | "Q" | "H";
}

export class QrLinkError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "QrLinkError";
  }
}

export function utf8ByteLength(value: string) {
  return new TextEncoder().encode(value).length;
}

function isLocalHostname(hostname: string) {
  const host = hostname.toLowerCase();
  return host === "localhost" || host === "127.0.0.1" || host === "[::1]";
}

function localHttpAllowed(url: URL, options: QrLinkValidationOptions) {
  if (!options.allowLocalHttp) return false;
  if (options.kind === "custom") return false;
  if (!isLocalHostname(url.hostname)) return false;
  if (!options.currentOrigin) return false;
  let current: URL;
  try {
    current = new URL(options.currentOrigin);
  } catch {
    return false;
  }
  if (current.protocol !== "http:" || !isLocalHostname(current.hostname)) {
    return false;
  }
  return current.origin === url.origin;
}

/**
 * Validates a URL before it is encoded, then returns the exact trimmed input.
 * The URL parser is never used to rebuild the value: fragments and query
 * escaping must remain byte-for-byte identical to what the issuer produced.
 */
export function validateQrLink(
  raw: string,
  options: QrLinkValidationOptions,
): string {
  if (typeof raw !== "string")
    throw new QrLinkError("请输入要生成二维码的链接");
  const value = raw.trim();
  if (!value) throw new QrLinkError("请输入要生成二维码的链接");
  if (/[\s\u0000-\u001f\u007f]/.test(value)) {
    throw new QrLinkError("链接中不能包含空格、换行或控制字符");
  }

  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new QrLinkError("链接格式不正确，请粘贴完整的 HTTPS 链接");
  }

  if (url.username || url.password) {
    throw new QrLinkError("链接不能包含账号或密码");
  }

  const protocol = url.protocol.toLowerCase();
  if (protocol === "http:") {
    if (!localHttpAllowed(url, options)) {
      throw new QrLinkError(
        options.kind === "custom"
          ? "后台自定义链接必须使用 HTTPS"
          : "本地 HTTP 链接仅限开发环境、当前来源的订单或邀请链接",
      );
    }
  } else if (protocol !== "https:") {
    throw new QrLinkError(
      options.kind === "custom"
        ? "后台自定义链接必须使用 HTTPS"
        : "仅支持 HTTPS 链接",
    );
  }

  const bytes = utf8ByteLength(value);
  if (bytes > QR_MAX_BYTES) {
    throw new QrLinkError(
      `链接过长，超出二维码容量（最多 ${QR_MAX_BYTES} 字节）。请缩短链接，二维码不会截断链接。`,
    );
  }
  return value;
}

/** Generates the PNG entirely in the current browser/Node process. */
export async function createQrDataUrl(
  value: string,
  options: QrImageOptions = {},
): Promise<string> {
  if (!value) throw new QrLinkError("请输入要生成二维码的链接");
  const bytes = utf8ByteLength(value);
  if (bytes > QR_MAX_BYTES) {
    throw new QrLinkError(
      `链接过长，超出二维码容量（最多 ${QR_MAX_BYTES} 字节）。请缩短链接，二维码不会截断链接。`,
    );
  }
  try {
    return await QRCode.toDataURL(value, {
      errorCorrectionLevel: options.errorCorrectionLevel || "M",
      width: options.width || 512,
      margin: options.margin ?? 2,
      type: "image/png",
    });
  } catch {
    throw new QrLinkError("二维码生成失败，请检查链接后重试");
  }
}
