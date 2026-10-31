import { describe, expect, it } from "vitest";
import { imageMime } from "../src/lib/image-bytes";

const bytes = (...values: number[]) => new Uint8Array(values);

describe("imageMime", () => {
  it("detects PNG signatures", () => {
    expect(
      imageMime(bytes(0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a)),
    ).toBe("image/png");
  });

  it("detects JPEG signatures", () => {
    expect(imageMime(bytes(0xff, 0xd8, 0xff, 0xe0))).toBe("image/jpeg");
  });

  it("detects WebP signatures", () => {
    expect(
      imageMime(
        bytes(
          0x52,
          0x49,
          0x46,
          0x46,
          0x00,
          0x00,
          0x00,
          0x00,
          0x57,
          0x45,
          0x42,
          0x50,
        ),
      ),
    ).toBe("image/webp");
  });

  it("detects GIF signatures", () => {
    expect(imageMime(bytes(0x47, 0x49, 0x46, 0x38, 0x39, 0x61))).toBe(
      "image/gif",
    );
  });

  it("rejects PDFs and empty payloads", () => {
    expect(imageMime(new TextEncoder().encode("%PDF-1.7"))).toBeNull();
    expect(imageMime(new Uint8Array())).toBeNull();
  });

  it("rejects truncated magic bytes", () => {
    expect(imageMime(bytes(0x89, 0x50, 0x4e, 0x47))).toBeNull();
    expect(imageMime(bytes(0xff, 0xd8))).toBeNull();
    expect(
      imageMime(bytes(0x52, 0x49, 0x46, 0x46, 0x57, 0x45, 0x42, 0x50)),
    ).toBeNull();
    expect(imageMime(bytes(0x47, 0x49, 0x46, 0x38, 0x39))).toBeNull();
  });
});
