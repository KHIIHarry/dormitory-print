import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import type { StaffSession } from "../src/growth/types";
import { ADMIN } from "../src/lib/api";
import {
  ROOT_MANIFEST_PATH,
  ROOT_SERVICE_WORKER_PATH,
  bootstrapUnifiedPwa,
  isLegacyDormPrintRegistration,
  unregisterLegacyDormPrintServiceWorkers,
} from "../src/lib/pwa";
import {
  courierWorkspaceAllowed,
  customerLoginPath,
  safeStaffReturn,
  workspaceEntries,
} from "../src/components/role-switch";
import { planResources } from "../src/lib/resources";
import { visibleAdminGroups } from "../src/admin/navigation";

const admin = (capabilities = ["*"]): StaffSession =>
  ({
    id: "staff-1",
    role: "ADMIN",
    name: "管理员",
    username: "admin",
    capabilities,
  }) as StaffSession;

const courier = (): StaffSession =>
  ({
    id: "staff-2",
    role: "COURIER",
    name: "李同学",
    username: "courier",
    capabilities: ["*"],
  }) as StaffSession;

const mainSource = readFileSync(
  new URL("../src/main.tsx", import.meta.url),
  "utf8",
);

const registration = (
  scope: string,
  scripts: (string | null)[],
  origin = "https://dormprint.test",
) => ({
  scope: `${origin}${scope}`,
  active: scripts[0]
    ? ({ scriptURL: `${origin}${scripts[0]}` } as ServiceWorker)
    : null,
  installing: scripts[1]
    ? ({ scriptURL: `${origin}${scripts[1]}` } as ServiceWorker)
    : null,
  waiting: scripts[2]
    ? ({ scriptURL: `${origin}${scripts[2]}` } as ServiceWorker)
    : null,
});

describe("single installable DormPrint app", () => {
  it("generates one root manifest identity and only root install assets", async () => {
    const { buildPwaAssets } = await import("../scripts/generate-pwa.mjs");
    const assets = buildPwaAssets("/manage");
    const root = assets.files["manifest.webmanifest"];
    const manifest = JSON.parse(root);

    expect(manifest.id).toBe("/");
    expect(manifest.start_url).toBe("/u/");
    expect(manifest.scope).toBe("/");
    expect(manifest.short_name).toBe("DormPrint");
    expect(manifest.name).toContain("DormPrint");
    expect(manifest.display).toBe("standalone");
    expect(manifest.icons).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ sizes: "192x192" }),
        expect.objectContaining({ sizes: "512x512" }),
      ]),
    );

    expect(assets.legacyBases).toEqual(["/u", "/manage", "/courier"]);
    for (const path of [
      "u/manifest.webmanifest",
      "manage/manifest.webmanifest",
      "courier/manifest.webmanifest",
    ]) {
      expect(assets.files[path]).toBe(root);
    }
    expect(
      Object.keys(assets.files).filter((path) => path.endsWith("sw.js")),
    ).toEqual(["sw.js"]);
    expect(
      Object.keys(assets.files).filter((path) => path.endsWith("offline.html")),
    ).toEqual(["offline.html"]);
    expect(assets.files["u/sw.js"]).toBeUndefined();
    expect(assets.files["manage/sw.js"]).toBeUndefined();
    expect(assets.files["courier/sw.js"]).toBeUndefined();
    expect(assets.files["u/offline.html"]).toBeUndefined();
  });

  it("honours a configurable admin base for aliases without separate installs", async () => {
    const { buildPwaAssets } = await import("../scripts/generate-pwa.mjs");
    const assets = buildPwaAssets("/ops/");
    expect(assets.legacyBases).toEqual(["/u", "/ops", "/courier"]);
    expect(assets.files["ops/manifest.webmanifest"]).toBe(
      assets.files["manifest.webmanifest"],
    );
    expect(assets.files["ops/sw.js"]).toBeUndefined();
    expect(() => buildPwaAssets("/u")).toThrow();
    expect(() => buildPwaAssets("/courier")).toThrow();
    expect(() => buildPwaAssets("https://evil.test")).toThrow();
  });

  it("keeps the root service worker static-only and privacy-safe", async () => {
    const { buildPwaAssets } = await import("../scripts/generate-pwa.mjs");
    const sw = buildPwaAssets("/manage").files["sw.js"];

    expect(sw).toContain('const CACHE = "dormprint-static-v1"');
    expect(sw).toContain('const OFFLINE_URL = "/offline.html"');
    expect(sw).toContain('url.pathname.startsWith("/api/")');
    expect(sw).toContain("url.search");
    expect(sw).toContain('request.headers.has("Authorization")');
    expect(sw).toContain('request.mode === "navigate"');
    expect(sw).toContain('fetch(request, { cache: "no-store" })');
    expect(sw).toContain("caches.match(OFFLINE_URL)");
    expect(sw).toContain('url.pathname.startsWith("/assets/")');
    expect(sw).not.toContain("cache.put(request");
    const precache = sw.match(/const PRECACHE = \[[^\]]*\]/)?.[0] || "";
    expect(precache).not.toContain("/api/");
    expect(sw).not.toContain("/u/");
  });
});

describe("legacy service worker migration", () => {
  const opts = { origin: "https://dormprint.test", adminBase: "/manage" };

  it("recognises only DormPrint legacy scoped registrations", () => {
    expect(
      isLegacyDormPrintRegistration(
        registration("/u/", ["/u/sw.js"]),
        opts,
      ),
    ).toBe(true);
    expect(
      isLegacyDormPrintRegistration(
        registration("/manage/", ["/manage/sw.js"]),
        opts,
      ),
    ).toBe(true);
    expect(
      isLegacyDormPrintRegistration(
        registration("/courier/", [null, "/courier/sw.js"]),
        opts,
      ),
    ).toBe(true);

    expect(
      isLegacyDormPrintRegistration(registration("/", ["/sw.js"]), opts),
    ).toBe(false);
    expect(
      isLegacyDormPrintRegistration(
        registration("/other/", ["/other/sw.js"]),
        opts,
      ),
    ).toBe(false);
    expect(
      isLegacyDormPrintRegistration(
        registration("/u/", ["/other/sw.js"]),
        opts,
      ),
    ).toBe(false);
    expect(
      isLegacyDormPrintRegistration(
        registration("/other/", ["/u/sw.js"]),
        opts,
      ),
    ).toBe(false);
    expect(
      isLegacyDormPrintRegistration(
        registration("/u/", ["/u/sw.js"], "https://other.test"),
        opts,
      ),
    ).toBe(false);
  });

  it("unregisters the three legacy bases and leaves other apps alone", async () => {
    const unregistered: string[] = [];
    const make = (scope: string, script: string) => {
      const reg = registration(scope, [script]) as ReturnType<
        typeof registration
      > & { unregister: () => Promise<boolean> };
      reg.unregister = vi.fn(async () => {
        unregistered.push(scope);
        return true;
      });
      return reg;
    };
    const registrations = [
      make("/u/", "/u/sw.js"),
      make("/manage/", "/manage/sw.js"),
      make("/courier/", "/courier/sw.js"),
      make("/", "/sw.js"),
      make("/other/", "/other/sw.js"),
    ];
    const container = {
      getRegistrations: async () => registrations,
    } as unknown as ServiceWorkerContainer;

    await expect(
      unregisterLegacyDormPrintServiceWorkers(container, opts),
    ).resolves.toBe(3);
    expect(unregistered.sort()).toEqual(["/courier/", "/manage/", "/u/"]);
  });

  it("installs the root manifest and only registers the root worker in production", async () => {
    const link = { rel: "", href: "" };
    const document = {
      querySelector: () => link,
      createElement: () => link,
      head: { appendChild: () => undefined },
    } as unknown as Document;
    const calls: [string, RegistrationOptions | undefined][] = [];
    const unregister = vi.fn(async () => true);
    const navigator = {
      serviceWorker: {
        getRegistrations: async () => [
          { ...registration("/u/", ["/u/sw.js"]), unregister },
          { ...registration("/other/", ["/other/sw.js"]), unregister },
        ],
        register: async (url: string, options?: RegistrationOptions) => {
          calls.push([url, options]);
          return {} as ServiceWorkerRegistration;
        },
      },
    } as unknown as Navigator;

    const result = await bootstrapUnifiedPwa({
      document,
      navigator,
      production: true,
      adminBase: "/manage",
    });

    expect(link).toMatchObject({ href: ROOT_MANIFEST_PATH });
    expect(calls).toEqual([[ROOT_SERVICE_WORKER_PATH, { scope: "/" }]]);
    expect(unregister).toHaveBeenCalledTimes(1);
    expect(result).toEqual({
      manifest: "/manifest.webmanifest",
      unregistered: 1,
      registered: true,
    });
  });

  it("still migrates legacy workers outside production without registering", async () => {
    const calls: string[] = [];
    const unregister = vi.fn(async () => true);
    const navigator = {
      serviceWorker: {
        getRegistrations: async () => [
          { ...registration("/courier/", ["/courier/sw.js"]), unregister },
        ],
        register: async (url: string) => {
          calls.push(url);
          return {} as ServiceWorkerRegistration;
        },
      },
    } as unknown as Navigator;
    const link = { href: "" };
    const document = {
      querySelector: () => link,
      head: { appendChild: () => undefined },
      createElement: () => link,
    } as unknown as Document;

    const result = await bootstrapUnifiedPwa({
      document,
      navigator,
      production: false,
    });
    expect(result.registered).toBe(false);
    expect(calls).toEqual([]);
    expect(unregister).toHaveBeenCalledTimes(1);
  });
});

describe("role switch guards", () => {
  it("offers no workspace before an employee authenticates", () => {
    expect(workspaceEntries(null)).toEqual([]);
  });

  it("never offers the admin workspace to a courier", () => {
    expect(workspaceEntries(courier()).map((entry) => entry.key)).toEqual([
      "courier",
    ]);
    expect(workspaceEntries(courier())[0].to).toBe("/courier/");
    expect(workspaceEntries(courier()).some((entry) => entry.key === "admin")).toBe(
      false,
    );
  });

  it("offers admin and courier to an admin, including a configured base", () => {
    const entries = workspaceEntries(admin(), "/ops/");
    expect(entries.map((entry) => entry.key)).toEqual(["admin", "courier"]);
    expect(entries[0].to).toBe("/ops/");
    expect(entries[1].to).toBe("/courier/");
  });

  it("sends a limited admin to the first permitted operational route", () => {
    const entries = workspaceEntries(admin(["mail.manage"]));
    expect(entries[0].to).toBe(`${ADMIN}/settings/mail`);
    expect(entries[0].to).not.toBe(`${ADMIN}/`);
  });

  it("uses the configured admin base for a limited admin landing", () => {
    expect(workspaceEntries(admin(["mail.manage"]), "/ops/")[0].to).toBe(
      "/ops/settings/mail",
    );
  });

  it("does not offer the courier workspace to a mail-only admin", () => {
    const entries = workspaceEntries(admin(["mail.manage"]));
    expect(entries.map((entry) => entry.key)).toEqual(["admin"]);
    expect(entries[0].to).toBe(`${ADMIN}/settings/mail`);
    expect(courierWorkspaceAllowed(admin(["mail.manage"]))).toBe(false);
  });

  it("offers courier only with the courier role or admin delivery capability", () => {
    expect(courierWorkspaceAllowed(courier())).toBe(true);
    expect(courierWorkspaceAllowed(admin(["*"]))).toBe(true);
    expect(courierWorkspaceAllowed(admin(["delivery.manage"]))).toBe(true);
    expect(courierWorkspaceAllowed(admin(["orders.view"]))).toBe(false);
    expect(courierWorkspaceAllowed(admin([]))).toBe(false);
  });

  it("does not prefetch courier resources for a mail-only admin", () => {
    expect(planResources("/courier/", "", "ADMIN", ["mail.manage"])).toEqual(
      [],
    );
    expect(
      planResources("/courier/", "", "ADMIN", ["delivery.manage"]).length,
    ).toBeGreaterThan(0);
    expect(planResources("/courier/", "", "ADMIN", ["*"]).length).toBeGreaterThan(
      0,
    );
    expect(planResources("/courier/", "", "COURIER", []).length).toBeGreaterThan(
      0,
    );
  });

  it("falls back to the admin account page when no operational route is allowed", () => {
    expect(workspaceEntries(admin([]))[0].to).toBe(`${ADMIN}/account`);
  });

  it("keeps the dashboard when the server grants dashboard visibility", () => {
    expect(workspaceEntries(admin(["orders.view"]))[0].to).toBe(`${ADMIN}/`);
  });

  it("forces the account page when a password change is required", () => {
    expect(
      workspaceEntries({
        ...admin(["mail.manage"]),
        mustChangePassword: true,
      })[0].to,
    ).toBe(`${ADMIN}/account`);
  });

  it("hides the super-admin employee grade policy from an ordinary full admin", () => {
    const labels = (isSuperAdmin: boolean) =>
      visibleAdminGroups(["*"], false, isSuperAdmin)
        .flatMap((group) => group.items)
        .map((item) => item.label);
    expect(labels(false)).not.toContain("员工折扣");
    expect(labels(true)).toContain("员工折扣");
  });

  it("lets a super admin without dashboard access land on grade policy", () => {
    expect(
      workspaceEntries({ ...admin([]), isSuperAdmin: true })[0].to,
    ).toBe(`${ADMIN}/settings/benefits`);
  });

  it("keeps only same-origin workspace paths as safe returns", () => {
    expect(safeStaffReturn("/manage/orders?tab=1")).toBe(
      "/manage/orders?tab=1",
    );
    expect(safeStaffReturn("/courier/orders/1")).toBe("/courier/orders/1");
    expect(safeStaffReturn("/manage")).toBe("/manage");
    expect(safeStaffReturn("/manage/login")).toBeNull();
    expect(safeStaffReturn("/courier/login")).toBeNull();
    expect(safeStaffReturn("/u/account")).toBeNull();
    expect(safeStaffReturn("https://evil.test/manage")).toBeNull();
    expect(safeStaffReturn("//evil.test/manage")).toBeNull();
    expect(safeStaffReturn("/manage/../u/account")).toBeNull();
    expect(safeStaffReturn("/manage/%2e%2e/u/account")).toBeNull();
    expect(safeStaffReturn("/manage/%5c..%5cu/account")).toBeNull();
    expect(safeStaffReturn("/manage/%23fragment")).toBeNull();
  });

  it("sends unauthenticated staff to the one customer login with a safe return", () => {
    expect(customerLoginPath("/manage/orders?tab=1")).toBe(
      "/u/account/login?returnTo=%2Fmanage%2Forders%3Ftab%3D1",
    );
    expect(customerLoginPath("/courier/")).toBe(
      "/u/account/login?returnTo=%2Fcourier%2F",
    );
    expect(customerLoginPath("/u/account")).toBe("/u/account/login");
    expect(customerLoginPath("/manage/login")).toBe("/u/account/login");
  });

  it("keys workspace access off the authenticated staff role, not claims", () => {
    expect(
      workspaceEntries({ ...admin(), capabilities: [] }).map(
        (entry) => entry.key,
      ),
    ).toEqual(["admin"]);
    expect(
      workspaceEntries({ ...courier(), role: "ADMIN" }).map(
        (entry) => entry.key,
      ),
    ).toEqual(["admin", "courier"]);
  });
});

describe("legacy staff credential routes", () => {
  it("no longer mounts the retired staff password forms", () => {
    expect(mainSource).not.toContain("StaffCredential");
  });

  it("redirects both staff bases to the base customer account flows", () => {
    expect(mainSource).toContain("const legacyCredentialRoutes = {");
    expect(mainSource).toContain('setup: "/u/account/register"');
    expect(mainSource).toContain('forgot: "/u/account/forgot"');
    expect(mainSource).toContain('reset: "/u/account/reset"');
    expect(mainSource).toContain('verify: "/u/account/verify"');
    expect(mainSource).toContain("path={`${ADMIN}/account/${mode}`}");
    expect(mainSource).toContain("path={`/courier/account/${mode}`}");
    expect(mainSource).toMatch(
      /<LegacyCredentialRedirect\s+to=\{legacyCredentialRoutes\[mode\]\}\s*\/>/,
    );
    expect(mainSource).toContain("search: location.search");
    expect(mainSource).toContain("hash: location.hash");
  });

  it("keeps the new root payroll, benefits, and QR routes", () => {
    expect(mainSource).toContain('path="payroll"');
    expect(mainSource).toContain('path="settings/benefits"');
    expect(mainSource).toContain('path="qr-tools"');
  });
});
