// Run after: npx vite build --mode demo --outDir output/pdf-preview-qa/dist
// Uses only a disposable Nginx container and synthetic demo PDFs; no private API/data.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import {
  chmodSync,
  cpSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { test } from "node:test";
import { chromium, expect } from "@playwright/test";

const docker = (...args) =>
  execFileSync("docker", args, {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  }).trim();

test(
  "production Nginx serves executable PDF workers and renders final PDFs",
  { timeout: 90000 },
  async (t) => {
    const fixture = mkdtempSync(join(tmpdir(), "dormprint-pdf-worker-"));
    const name = `dormprint-pdf-worker-${process.pid}`;
    let running = false;
    let browser;
    try {
      chmodSync(fixture, 0o755);
      const html = join(fixture, "html"),
        conf = join(fixture, "conf");
      cpSync(resolve("output/pdf-preview-qa/dist"), html, { recursive: true });
      mkdirSync(conf);
      cpSync(
        "node_modules/pdfjs-dist/build/pdf.worker.min.mjs",
        join(html, "assets", "legacy-worker.mjs"),
      );
      writeFileSync(
        join(fixture, "nginx.conf"),
        readFileSync("deploy/nginx/nginx.conf"),
      );
      writeFileSync(
        join(conf, "default.conf"),
        readFileSync("deploy/nginx/templates/default.conf.template", "utf8")
          .replaceAll("${DORMPRINT_PUBLIC_HOST}", "localhost")
          .replaceAll("${DORMPRINT_UPLOAD_LIMIT_MB}", "220"),
      );
      docker(
        "run",
        "-d",
        "--rm",
        "--name",
        name,
        "--add-host",
        "api:127.0.0.1",
        "--entrypoint",
        "nginx",
        "--tmpfs",
        "/tmp",
        "-p",
        "127.0.0.1::8080",
        "-v",
        `${html}:/usr/share/nginx/html:ro`,
        "-v",
        `${conf}:/etc/nginx/conf.d:ro`,
        "-v",
        `${fixture}/nginx.conf:/etc/nginx/nginx.conf:ro`,
        "nginxinc/nginx-unprivileged:1.27-alpine",
        "-g",
        "daemon off;",
      );
      running = true;
      const port = docker("port", name, "8080/tcp").split(":").at(-1);
      const origin = `http://127.0.0.1:${port}`;
      await expect
        .poll(async () => {
          try {
            return (await fetch(origin + "/healthz")).status;
          } catch {
            return 0;
          }
        })
        .toBe(200);

      await t.test(
        "legacy .mjs responses have a JavaScript MIME type",
        async () => {
          const response = await fetch(origin + "/assets/legacy-worker.mjs");
          assert.equal(response.status, 200);
          assert.match(
            response.headers.get("content-type") || "",
            /^(application|text)\/javascript\b/,
          );
        },
      );

      await t.test(
        "build emits a .js PDF worker while existing CSS types stay intact",
        async () => {
          const names = readdirSync(join(html, "assets"));
          const worker = names.find((name) =>
            /^pdf\.worker\..*\.js$/.test(name),
          );
          assert.ok(
            worker,
            "PDF worker must use a new .js URL instead of a cached .mjs URL",
          );
          const workerResponse = await fetch(origin + "/assets/" + worker);
          assert.equal(workerResponse.status, 200);
          assert.match(
            workerResponse.headers.get("content-type") || "",
            /^(application|text)\/javascript\b/,
          );
          const css = names.find((name) => name.endsWith(".css"));
          assert.ok(css);
          assert.match(
            (await fetch(origin + "/assets/" + css)).headers.get(
              "content-type",
            ) || "",
            /^text\/css\b/,
          );
        },
      );

      browser = await chromium.launch({ channel: "chrome", headless: true });
      for (const disableWorkers of [false, true]) {
        await t.test(
          disableWorkers
            ? "renders with the PDF.js main-thread fallback"
            : "renders with a module worker",
          async () => {
            const context = await browser.newContext();
            try {
              if (disableWorkers)
                await context.addInitScript(() => {
                  window.Worker = class {
                    constructor() {
                      throw new Error("Worker unavailable in this browser");
                    }
                  };
                });
              const page = await context.newPage();
              await page.goto(origin + "/preview");
              await page
                .getByRole("button", { name: "文档设置示例", exact: true })
                .click();
              await page
                .getByRole("button", { name: "生成最终预览", exact: true })
                .click();
              await page
                .getByRole("checkbox", {
                  name: "我已检查这份最终 PDF，确认打印设置",
                  exact: true,
                })
                .first()
                .click();
              await expect(
                page.getByRole("button", {
                  name: "我已确认最终打印格式",
                  exact: true,
                }),
              ).toBeEnabled();
              await expect(
                page.getByRole("dialog").locator("canvas"),
              ).toBeVisible();
              await expect(
                page.getByRole("dialog").getByText(/预览失败/),
              ).toHaveCount(0);
            } finally {
              await context.close();
            }
          },
        );
      }
    } finally {
      await browser?.close();
      if (running) docker("rm", "-f", name);
      rmSync(fixture, { recursive: true, force: true });
    }
  },
);
