/**
 * Recover from stale or interrupted lazy-route chunks.
 *
 * When a new build is deployed, an already-open tab still references the old
 * content-hashed chunks. Navigating to a route that was not loaded before the
 * deploy asks the server for a file that no longer exists; Vite's dynamic
 * import rejects and, without a boundary, React unmounts the whole tree.
 * Reloading the document is the only reliable recovery because the new
 * index.html carries the new chunk graph.
 */

export const CHUNK_RELOAD_KEY = "dormprint:chunk-reload-at";
export const CHUNK_RELOAD_COOLDOWN_MS = 20_000;

const CHUNK_ERROR_PATTERNS = [
  /failed to fetch dynamically imported module/i,
  /error loading dynamically imported module/i,
  /dynamically imported module/i,
  /importing a module script failed/i,
  /failed to load module script/i,
  /loading chunk [\w.-]+ failed/i,
  /unable to preload/i,
  /unable to preload css/i,
];

/** True for browser/Vite failures caused by a chunk URL that cannot load. */
export function isChunkLoadError(error: unknown): boolean {
  if (!error) return false;
  if (typeof error === "string")
    return CHUNK_ERROR_PATTERNS.some((pattern) => pattern.test(error));
  const name = (error as { name?: unknown }).name;
  if (name === "ChunkLoadError") return true;
  const message = (error as { message?: unknown }).message;
  return (
    typeof message === "string" &&
    CHUNK_ERROR_PATTERNS.some((pattern) => pattern.test(message))
  );
}

/** Pure guard so repeated failures cannot trap the tab in a reload loop. */
export function canAutoReload(lastReloadAt: number, now: number): boolean {
  return (
    !Number.isFinite(lastReloadAt) ||
    lastReloadAt <= 0 ||
    now - lastReloadAt > CHUNK_RELOAD_COOLDOWN_MS
  );
}

const WINDOW_NAME_PREFIX = "dormprint-chunk-reload:";
let memoryReloadAt = 0;

function lastReloadAt(): number {
  let last = memoryReloadAt;
  try {
    last = Math.max(
      last,
      Number(window.sessionStorage.getItem(CHUNK_RELOAD_KEY)) || 0,
    );
  } catch {
    // Storage can be unavailable in hardened/private browser modes.
  }
  try {
    if (window.name.startsWith(WINDOW_NAME_PREFIX))
      last = Math.max(
        last,
        Number(window.name.slice(WINDOW_NAME_PREFIX.length)) || 0,
      );
  } catch {
    // window.name is a best-effort fallback and may be unwritable.
  }
  return last;
}

function rememberReload(now: number) {
  memoryReloadAt = now;
  try {
    window.sessionStorage.setItem(CHUNK_RELOAD_KEY, String(now));
  } catch {
    // Fall through to window.name so a reload loop is still prevented.
  }
  try {
    window.name = `${WINDOW_NAME_PREFIX}${now}`;
  } catch {
    // Nothing else to do; memory guard still protects this document.
  }
}

/**
 * Reload once per cooldown window. Returns false when a reload already
 * happened recently (or the browser is offline), in which case the caller
 * should keep its fallback UI visible instead of reloading again.
 */
export function reloadOnceForChunkError(): boolean {
  if (typeof window === "undefined") return false;
  if (typeof navigator !== "undefined" && navigator.onLine === false)
    return false;
  const now = Date.now();
  if (!canAutoReload(lastReloadAt(), now)) return false;
  rememberReload(now);
  window.location.reload();
  return true;
}

/**
 * Vite emits `vite:preloadError` when a modulepreload/CSS preload fails.
 * Prevent the rejection only when we are actually reloading; otherwise let
 * the error reach the React boundary so it can show the recovery screen.
 */
let recoveryInstalled = false;

export function installChunkRecovery(): () => void {
  if (typeof window === "undefined" || recoveryInstalled) return () => {};
  recoveryInstalled = true;
  const onPreloadError = (event: Event) => {
    const error = (event as Event & { payload?: unknown }).payload;
    if (isChunkLoadError(error) && reloadOnceForChunkError())
      event.preventDefault();
  };
  window.addEventListener("vite:preloadError", onPreloadError);
  return () => window.removeEventListener("vite:preloadError", onPreloadError);
}
