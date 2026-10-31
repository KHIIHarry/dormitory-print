import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ADMIN_PATH_PATTERN = /^\/[a-zA-Z0-9/_-]+$/;

export function normalizeAdminBase(value = "/manage") {
  const path = `/${String(value || "")
    .trim()
    .replace(/^\/+|\/+$/g, "")}`;
  return path === "/" ? "/manage" : path;
}

export function validateAdminBase(value) {
  const base = normalizeAdminBase(value);
  if (
    !ADMIN_PATH_PATTERN.test(base) ||
    base === "/" ||
    ["/u", "/courier"].includes(base)
  ) {
    throw Error("管理路径必须为独立的绝对路径");
  }
  return base;
}

export function generateManifest() {
  return JSON.stringify(
    {
      id: "/",
      name: "DormPrint 宿舍打印",
      short_name: "DormPrint",
      description: "一份资料，一次安心送达。",
      start_url: "/u/",
      scope: "/",
      display: "standalone",
      background_color: "#fbfaf8",
      theme_color: "#c67c4e",
      lang: "zh-CN",
      icons: [192, 512].flatMap((size) => [
        {
          src: `/assets/icon-${size}.png`,
          sizes: `${size}x${size}`,
          type: "image/png",
          purpose: "any",
        },
        {
          src: `/assets/icon-${size}.png`,
          sizes: `${size}x${size}`,
          type: "image/png",
          purpose: "maskable",
        },
      ]),
    },
    null,
    2,
  );
}

export function generateRootServiceWorker() {
  return `/* DormPrint root service worker: static assets only. */
const CACHE = "dormprint-static-v1";
const OFFLINE_URL = "/offline.html";
const PRECACHE = [OFFLINE_URL, "/assets/icon-192.png", "/assets/icon-512.png"];
const STATIC_PATTERN = /\\.(?:css|js|mjs|woff2?|ttf|otf|png|jpe?g|webp|avif|svg|gif|ico)$/i;

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key.startsWith("dormprint-static-") && key !== CACHE)
            .map((key) => caches.delete(key))
        )
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) return;
  if (url.pathname === "/sw.js" || url.pathname.endsWith("/manifest.webmanifest")) return;
  if (url.search) return;
  if (request.headers.has("Authorization")) return;

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request, { cache: "no-store" }).catch(() => caches.match(OFFLINE_URL))
    );
    return;
  }

  if (!url.pathname.startsWith("/assets/")) return;
  if (!STATIC_PATTERN.test(url.pathname)) return;
  event.respondWith(
    caches.match(url.pathname).then(
      (cached) =>
        cached ||
        fetch(request).then((response) => {
          if (response.ok && response.type === "basic") {
            const copy = response.clone();
            caches.open(CACHE).then((cache) => cache.put(url.pathname, copy));
          }
          return response;
        })
    )
  );
});
`;
}

export function generateOfflinePage() {
  return `<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DormPrint 宿舍打印 · 离线</title>
<style>
body{font-family:system-ui;background:#f9f2ed;color:#73543b;text-align:center;margin:25vh 24px}
img{width:64px;margin:auto}
h1{font-size:24px}
p{font-size:14px;line-height:1.9}
a{background:#c67c4e;color:white;display:inline-block;padding:13px 30px;border-radius:12px;text-decoration:none}
</style>
<img src="/assets/icon-192.png" alt="">
<h1>暂时离线，稍后再来</h1>
<p>联网后再查看订单与执行业务操作。<br>私有资料不会保存在离线缓存中。</p>
<a href="/u/">重新连接</a>
</html>`;
}

/**
 * One installable app: a root identity plus manifest aliases for the legacy
 * deep-link paths. Aliases are byte-identical, so they cannot become separate
 * installs. Only the root worker and root offline page are emitted.
 */
export function buildPwaAssets(adminBase = "/manage") {
  const base = validateAdminBase(adminBase);
  const manifest = generateManifest();
  const legacyBases = Array.from(new Set(["/u", base, "/courier"]));
  const files = {
    "manifest.webmanifest": manifest,
    "sw.js": generateRootServiceWorker(),
    "offline.html": generateOfflinePage(),
  };
  for (const legacyBase of legacyBases) {
    files[`${legacyBase.slice(1)}/manifest.webmanifest`] = manifest;
  }
  return {
    adminBase: base,
    legacyBases,
    manifest: JSON.parse(manifest),
    files,
  };
}

export function writePwaAssets(assets, root = "public") {
  for (const [relativePath, contents] of Object.entries(assets.files)) {
    const target = join(root, relativePath);
    mkdirSync(dirname(target), { recursive: true });
    writeFileSync(target, contents);
  }
  // Old scoped workers and offline pages must not keep serving private apps.
  for (const legacyBase of assets.legacyBases) {
    for (const stale of ["sw.js", "offline.html"]) {
      rmSync(join(root, legacyBase.slice(1), stale), { force: true });
    }
  }
}

function readConfiguredAdminBase() {
  let env = "";
  try {
    env = readFileSync(".env.local", "utf8");
  } catch {
    // .env.local is optional.
  }
  return validateAdminBase(
    process.env.VITE_ADMIN_BASE_PATH ||
      env.match(/^VITE_ADMIN_BASE_PATH=(.+)$/m)?.[1] ||
      "/manage",
  );
}

export function main() {
  const assets = buildPwaAssets(readConfiguredAdminBase());
  writePwaAssets(assets);
  console.log("DormPrint 统一 manifest、根静态 SW 与离线页已生成。");
}

const directRun =
  process.argv[1] &&
  resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url));
if (directRun) main();
