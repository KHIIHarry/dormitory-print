import { expect, it } from "vitest";
import { AssetUpload } from "../src/admin/image-upload";
it("reports real transfer progress and commits only confirmed server URLs", async () => {
  let finish!: (value: { url: string }) => void;
  let callbacks: any;
  let image = "previous";
  const states: any[] = [];
  const task = new AssetUpload(() => 1);
  const pending = task.run(
    (options) => {
      callbacks = options;
      return new Promise((resolve) => {
        finish = resolve;
      });
    },
    (state) => states.push(state),
    (url) => {
      image = url;
    },
    () => {},
  );
  callbacks.onUploadProgress({ loaded: 5, total: 10 });
  expect(states.at(-1).value).toBe(50);
  expect(image).toBe("previous");
  callbacks.onUploadComplete();
  expect(states.at(-1).value).toBe(100);
  expect(image).toBe("previous");
  finish({ url: "confirmed" });
  await pending;
  expect(image).toBe("confirmed");
  expect(states.at(-1).busy).toBe(false);
});
it("retains valid image on failure and rejects callbacks after cancellation or identity changes", async () => {
  let scope = 1;
  let finish!: (value: { url: string }) => void;
  let callbacks: any;
  let image = "previous";
  const states: any[] = [];
  const task = new AssetUpload(() => scope);
  const pending = task.run(
    (options) => {
      callbacks = options;
      return new Promise((resolve) => (finish = resolve));
    },
    (state) => states.push(state),
    (url) => {
      image = url;
    },
    () => {},
  );
  scope++;
  callbacks.onUploadProgress({ loaded: 10, total: 10 });
  finish({ url: "private-stale" });
  await pending;
  expect(image).toBe("previous");
  expect(states).toHaveLength(1);
  task.cancel();
  await task.run(
    async () => {
      throw Error("failed");
    },
    (state) => states.push(state),
    (url) => {
      image = url;
    },
    () => {},
  );
  expect(image).toBe("previous");
  expect(states.at(-1).busy).toBe(false);
});

it("QR file selection and drops use the same single-image validation", async () => {
  const { qrUploadFile } = await import("../src/admin/image-upload");
  const image = new File([new Uint8Array([1])], "qr.png", {
    type: "image/png",
  });
  expect(qrUploadFile([image])).toBe(image);
  expect(() => qrUploadFile([])).toThrow("每次请选择一张二维码图片");
  expect(() => qrUploadFile([image, image])).toThrow(
    "每次请选择一张二维码图片",
  );
  const pdf = new File(["pdf"], "wrong.pdf", { type: "application/pdf" });
  expect(() => qrUploadFile([pdf])).toThrow("请选择图片格式");
});
