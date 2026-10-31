import { ADMIN } from "./api";

export const ROOT_MANIFEST_PATH = "/manifest.webmanifest";
export const ROOT_SERVICE_WORKER_PATH = "/sw.js";

type WorkerLike = { scriptURL: string } | null | undefined;
type RegistrationLike = {
  scope: string;
  active?: WorkerLike;
  installing?: WorkerLike;
  waiting?: WorkerLike;
};
type Registrable = RegistrationLike & { unregister: () => Promise<boolean> };
type ServiceWorkerContainerLike = {
  getRegistrations: () => Promise<readonly Registrable[]>;
  register: (
    scriptURL: string,
    options?: RegistrationOptions,
  ) => Promise<ServiceWorkerRegistration>;
};
type ManifestDocument = {
  querySelector: (selectors: string) => HTMLLinkElement | null;
  createElement: (tagName: string) => HTMLLinkElement;
  head: { appendChild: (node: HTMLLinkElement) => void };
};
type PwaNavigator = { serviceWorker?: ServiceWorkerContainerLike };

export type LegacyPwaOptions = {
  origin?: string;
  adminBase?: string;
};

export type BootstrapPwaOptions = LegacyPwaOptions & {
  document?: ManifestDocument;
  navigator?: PwaNavigator;
  production?: boolean;
};

export function normalizeAdminBase(base: string = ADMIN): string {
  const path = `/${String(base || "")
    .trim()
    .replace(/^\/+|\/+$/g, "")}`;
  return path === "/" ? "/manage" : path;
}

/** The three legacy DormPrint entry points which may have their own SW. */
export function legacyAppBases(adminBase: string = ADMIN): string[] {
  return Array.from(
    new Set(["/u", normalizeAdminBase(adminBase), "/courier"]),
  );
}

function registrationOrigin(
  registration: RegistrationLike,
  options: LegacyPwaOptions,
): string | null {
  try {
    return options.origin || new URL(registration.scope).origin;
  } catch {
    return null;
  }
}

/**
 * Matches a legacy DormPrint app registration only: the scope must be exactly
 * one of /u/, the configured admin base, or /courier/, and one of its workers
 * must be that base's own /sw.js. Root or unrelated app registrations never
 * match.
 */
export function isLegacyDormPrintRegistration(
  registration: RegistrationLike,
  options: LegacyPwaOptions = {},
): boolean {
  const origin = registrationOrigin(registration, options);
  if (!origin) return false;
  let scope: URL;
  try {
    scope = new URL(registration.scope);
  } catch {
    return false;
  }
  if (scope.origin !== origin) return false;
  const workers = [
    registration.active,
    registration.installing,
    registration.waiting,
  ]
    .map((worker) => worker?.scriptURL)
    .filter((url): url is string => Boolean(url));

  return legacyAppBases(options.adminBase).some((base) => {
    if (scope.pathname !== `${base}/`) return false;
    return workers.some((worker) => {
      try {
        const url = new URL(worker, origin);
        return url.origin === origin && url.pathname === `${base}/sw.js`;
      } catch {
        return false;
      }
    });
  });
}

/** Remove only the old scoped DormPrint workers, leaving every other app. */
export async function unregisterLegacyDormPrintServiceWorkers(
  container: Pick<ServiceWorkerContainerLike, "getRegistrations">,
  options: LegacyPwaOptions = {},
): Promise<number> {
  if (!container || typeof container.getRegistrations !== "function") return 0;
  let unregistered = 0;
  const registrations = await container.getRegistrations();
  for (const registration of registrations) {
    if (!isLegacyDormPrintRegistration(registration, options)) continue;
    try {
      if (await registration.unregister()) unregistered += 1;
    } catch {
      // A registration can disappear between list and unregister.
    }
  }
  return unregistered;
}

function applyRootManifest(document: ManifestDocument): void {
  let link = document.querySelector('link[rel="manifest"]');
  if (!link) {
    link = document.createElement("link");
    link.rel = "manifest";
    document.head?.appendChild(link);
  }
  link.href = ROOT_MANIFEST_PATH;
}

export type PwaBootstrapResult = {
  manifest: string;
  unregistered: number;
  registered: boolean;
};

/**
 * Point every DormPrint route at the one root manifest, remove legacy scoped
 * workers, and in production install one static-only root worker.
 */
export async function bootstrapUnifiedPwa(
  options: BootstrapPwaOptions = {},
): Promise<PwaBootstrapResult> {
  const doc =
    options.document ||
    (typeof document === "undefined" ? undefined : document);
  const nav =
    options.navigator ||
    (typeof navigator === "undefined" ? undefined : navigator);
  const production =
    options.production ??
    Boolean(
      typeof import.meta !== "undefined" && import.meta.env?.PROD,
    );

  if (doc) applyRootManifest(doc);

  let unregistered = 0;
  let registered = false;
  const serviceWorker = nav?.serviceWorker;
  if (serviceWorker) {
    unregistered = await unregisterLegacyDormPrintServiceWorkers(
      serviceWorker,
      {
        adminBase: options.adminBase,
        origin:
          options.origin ||
          (typeof location === "undefined" ? undefined : location.origin),
      },
    );
    if (production) {
      try {
        await serviceWorker.register(ROOT_SERVICE_WORKER_PATH, { scope: "/" });
        registered = true;
      } catch {
        // Registration can be unavailable in private mode; the app still works.
      }
    }
  }

  return {
    manifest: ROOT_MANIFEST_PATH,
    unregistered,
    registered,
  };
}
