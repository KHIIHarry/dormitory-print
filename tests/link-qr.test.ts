import { describe, expect, it } from "vitest";
import QRCode from "qrcode";
import {
  QR_MAX_BYTES,
  QrLinkError,
  createQrDataUrl,
  utf8ByteLength,
  validateQrLink,
} from "../src/lib/link-qr";

const secure = {
  kind: "order" as const,
  currentOrigin: "https://dormprint.example",
  allowLocalHttp: false,
};

const local = {
  kind: "order" as const,
  currentOrigin: "http://localhost:5173",
  allowLocalHttp: true,
};

describe("链接二维码的链接校验", () => {
  it("只去掉首尾空白，原样保留查询串与 fragment", () => {
    const raw =
      "  https://dormprint.example/u/order-link?orderId=ORD-42&back=%2Fu%2Forders#token=abcDEF%2F123%20ghi  ";
    const expected =
      "https://dormprint.example/u/order-link?orderId=ORD-42&back=%2Fu%2Forders#token=abcDEF%2F123%20ghi";
    expect(validateQrLink(raw, secure)).toBe(expected);
  });

  it("不重新编码 fragment，也不改变参数顺序和大小写", () => {
    const raw =
      "https://DormPrint.Example/u/order-link?b=2&a=1#token=AbC%2f%2B%3D&note=%E4%B8%AD%E6%96%87";
    expect(validateQrLink(raw, secure)).toBe(raw);
  });

  it("接受 HTTPS 的订单、邀请和后台自定义链接", () => {
    expect(
      validateQrLink("https://dormprint.example/u/order-link#token=x", secure),
    ).toBe("https://dormprint.example/u/order-link#token=x");
    expect(
      validateQrLink("https://dormprint.example/u/invite?code=ABC", {
        ...secure,
        kind: "invite",
      }),
    ).toBe("https://dormprint.example/u/invite?code=ABC");
    expect(
      validateQrLink("https://example.org/legal-notice", {
        ...secure,
        kind: "custom",
      }),
    ).toBe("https://example.org/legal-notice");
  });

  it("允许去掉首尾空白，拒绝首尾以外的空白和控制字符", () => {
    expect(
      validateQrLink("  https://a.example/ " + String.fromCharCode(10), secure),
    ).toBe("https://a.example/");
    for (const value of [
      "",
      "   ",
      "https://a.example/a b",
      "https://a.example/a" + String.fromCharCode(10) + "b",
      "https://a.example/" + String.fromCharCode(0) + "x",
    ]) {
      expect(() => validateQrLink(value, secure)).toThrow(QrLinkError);
    }
  });

  it("拒绝危险协议和带账号密码的链接", () => {
    for (const value of [
      "javascript:alert(1)",
      "data:text/html,<b>x</b>",
      "blob:https://dormprint.example/123",
      "file:///etc/passwd",
      "ftp://dormprint.example/a",
      "https://user:pass@dormprint.example/u/order-link",
      "https://user@dormprint.example/u/order-link",
    ]) {
      expect(() => validateQrLink(value, secure)).toThrow(QrLinkError);
    }
  });

  it("后台自定义链接只允许 HTTPS，不允许本机 HTTP 例外", () => {
    expect(() =>
      validateQrLink("http://localhost:5173/u/invite", {
        ...secure,
        kind: "custom",
        currentOrigin: "http://localhost:5173",
        allowLocalHttp: true,
      }),
    ).toThrow(QrLinkError);
    expect(() =>
      validateQrLink("http://127.0.0.1:5173/u/order-link", {
        ...secure,
        kind: "custom",
        currentOrigin: "http://127.0.0.1:5173",
        allowLocalHttp: true,
      }),
    ).toThrow(QrLinkError);
  });

  it("仅开发模式允许与当前来源完全一致的本机 HTTP 订单/邀请链接", () => {
    expect(
      validateQrLink("http://localhost:5173/u/order-link#token=x", local),
    ).toBe("http://localhost:5173/u/order-link#token=x");
    expect(
      validateQrLink("http://127.0.0.1:5173/u/invite?code=A", {
        ...local,
        kind: "invite",
        currentOrigin: "http://127.0.0.1:5173",
      }),
    ).toBe("http://127.0.0.1:5173/u/invite?code=A");

    for (const [value, options] of [
      [
        "http://localhost:5173/u/order-link",
        { ...local, allowLocalHttp: false },
      ],
      [
        "http://localhost:5173/u/order-link",
        { ...local, currentOrigin: "http://localhost:5174" },
      ],
      [
        "http://localhost:5173/u/order-link",
        { ...local, currentOrigin: "https://localhost:5173" },
      ],
      ["http://example.com/u/order-link", local],
      [
        "http://localhost:5173/u/order-link",
        { ...local, kind: "custom" as const },
      ],
    ] as const) {
      expect(() => validateQrLink(value, options)).toThrow(QrLinkError);
    }
  });

  it("达到版本 40-M 容量时成功，超出时明确失败且不截断", () => {
    const prefix = "https://dormprint.example/u/order-link?orderId=";
    const atLimit = prefix + "a".repeat(QR_MAX_BYTES - prefix.length);
    expect(utf8ByteLength(atLimit)).toBe(QR_MAX_BYTES);
    expect(validateQrLink(atLimit, secure)).toBe(atLimit);

    const tooLong = atLimit + "a";
    expect(utf8ByteLength(tooLong)).toBe(QR_MAX_BYTES + 1);
    expect(() => validateQrLink(tooLong, secure)).toThrow(/2331/);
    expect(tooLong.length).toBe(QR_MAX_BYTES + 1);
  });
});

function pngSize(dataUrl: string) {
  const base64 = dataUrl.slice(dataUrl.indexOf(",") + 1);
  const bytes = Buffer.from(base64, "base64");
  expect(bytes.subarray(0, 8).toString("hex")).toBe("89504e470d0a1a0a");
  expect(bytes.subarray(12, 16).toString("ascii")).toBe("IHDR");
  return {
    bytes,
    width: bytes.readUInt32BE(16),
    height: bytes.readUInt32BE(20),
  };
}

describe("浏览器本地生成二维码", () => {
  it("返回可下载的 512×512 PNG，且与 qrcode 对同一原始链接的输出一致", async () => {
    const raw =
      "https://dormprint.example/u/order-link?orderId=ORD-42#token=abcDEF%2F123";
    const dataUrl = await createQrDataUrl(raw);
    expect(dataUrl.startsWith("data:image/png;base64,")).toBe(true);
    const { bytes, width, height } = pngSize(dataUrl);
    expect({ width, height }).toEqual({ width: 512, height: 512 });
    expect(bytes.length).toBeGreaterThan(100);

    const expected = await QRCode.toDataURL(raw, {
      errorCorrectionLevel: "M",
      width: 512,
      margin: 2,
      type: "image/png",
    });
    expect(dataUrl).toBe(expected);
  });

  it("超出容量时拒绝生成，不静默截断", async () => {
    const tooLong = "https://dormprint.example/" + "a".repeat(QR_MAX_BYTES);
    await expect(createQrDataUrl(tooLong)).rejects.toBeInstanceOf(QrLinkError);
  });

  it("容量上限仍能生成真实 PNG，不会把超长链接悄悄改写", async () => {
    const prefix = "https://dormprint.example/u/order-link?orderId=";
    const atLimit = prefix + "a".repeat(QR_MAX_BYTES - prefix.length);
    expect(utf8ByteLength(atLimit)).toBe(QR_MAX_BYTES);
    const dataUrl = await createQrDataUrl(atLimit);
    expect(dataUrl.startsWith("data:image/png;base64,")).toBe(true);
    const { width, height } = pngSize(dataUrl);
    expect({ width, height }).toEqual({ width: 512, height: 512 });
  });
});
