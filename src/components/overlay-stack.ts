/** Shared by dialogs and the existing mobile drawer. Closing layers retain the lock. */
type Layer = {
  element: HTMLElement;
  close: () => void;
  interactive: () => boolean;
  trigger: HTMLElement | null;
};
const layers: Layer[] = [];
let originalOverflow = "",
  originalPadding = "",
  originalGap = "";
let inertElements = new Map<HTMLElement, boolean>();
let observer: MutationObserver | undefined;
function focusable(layer: Layer) {
  return Array.from(
    layer.element.querySelectorAll<HTMLElement>(
      'button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href],[tabindex="0"]',
    ),
  ).filter((el) => !el.closest("[inert]") && el.getClientRects().length > 0);
}
function top() {
  return layers.at(-1);
}
function syncBackground() {
  if (!layers.length) return;
  for (const child of Array.from(document.body.children)) {
    if (!(child instanceof HTMLElement) || child.hasAttribute("data-dp-layer"))
      continue;
    if (!inertElements.has(child)) inertElements.set(child, child.inert);
    child.inert = true;
  }
  layers.forEach((layer) => {
    if (layer !== top()) layer.element.setAttribute("aria-hidden", "true");
    else layer.element.removeAttribute("aria-hidden");
  });
}
function onKey(e: KeyboardEvent) {
  const layer = top();
  if (!layer) return;
  if (e.key === "Escape") {
    e.preventDefault();
    e.stopImmediatePropagation();
    if (layer.interactive()) layer.close();
    return;
  }
  if (e.key !== "Tab") return;
  if (!layer.interactive()) {
    e.preventDefault();
    return;
  }
  const controls = focusable(layer),
    first = controls[0],
    last = controls.at(-1);
  if (!first) {
    e.preventDefault();
    layer.element.focus({ preventScroll: true });
    return;
  }
  if (!layer.element.contains(document.activeElement)) {
    e.preventDefault();
    (e.shiftKey ? last : first)?.focus({ preventScroll: true });
  } else if (e.shiftKey && document.activeElement === first) {
    e.preventDefault();
    last?.focus({ preventScroll: true });
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault();
    first.focus({ preventScroll: true });
  }
}
function onFocus(e: FocusEvent) {
  const layer = top();
  if (layer?.interactive() && !layer.element.contains(e.target as Node))
    focusable(layer)[0]?.focus({ preventScroll: true });
}
export function registerOverlay(
  element: HTMLElement,
  close: () => void,
  interactive: () => boolean,
) {
  const active =
    document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
  const inherited = top()?.trigger;
  const trigger =
    (!active ||
      active === document.body ||
      active.closest(".modal-backdrop[data-state=closing]")) &&
    inherited?.isConnected
      ? inherited
      : active;
  const layer: Layer = { element, close, interactive, trigger };
  element.setAttribute("data-dp-layer", "");
  if (!layers.length) {
    const root = document.documentElement;
    originalOverflow = root.style.overflowY;
    originalPadding = root.style.paddingRight;
    originalGap = root.style.getPropertyValue("--modal-scrollbar-gap");
    const before = document.body.getBoundingClientRect().width,
      padding = parseFloat(getComputedStyle(root).paddingRight) || 0;
    root.style.overflowY = "hidden";
    const gap = Math.max(
      0,
      document.body.getBoundingClientRect().width - before,
    );
    if (gap) {
      root.style.paddingRight = `${padding + gap}px`;
      root.style.setProperty("--modal-scrollbar-gap", `${gap}px`);
    }
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("focusin", onFocus, true);
    observer = new MutationObserver(syncBackground);
    observer.observe(document.body, { childList: true });
  }
  layers.push(layer);
  syncBackground();
  const first = focusable(layer)[0];
  first?.focus({ preventScroll: true });
  let released = false;
  return {
    replace: (replacement: HTMLElement) => {
      layer.element = replacement;
      replacement.setAttribute("data-dp-layer", "");
      syncBackground();
    },
    release: () => {
      if (released) return;
      released = true;
      const wasTop = top() === layer;
      const i = layers.indexOf(layer);
      if (i >= 0) layers.splice(i, 1);
      if (!layers.length) {
        observer?.disconnect();
        observer = undefined;
        document.documentElement.style.overflowY = originalOverflow;
        document.documentElement.style.paddingRight = originalPadding;
        if (originalGap)
          document.documentElement.style.setProperty(
            "--modal-scrollbar-gap",
            originalGap,
          );
        else
          document.documentElement.style.removeProperty(
            "--modal-scrollbar-gap",
          );
        inertElements.forEach((value, el) => {
          if (el.isConnected) el.inert = value;
        });
        inertElements = new Map();
        document.removeEventListener("keydown", onKey, true);
        document.removeEventListener("focusin", onFocus, true);
      } else syncBackground();
      if (wasTop) {
        const remaining = top();
        const trigger = layer.trigger;
        if (
          trigger?.isConnected &&
          !trigger.closest("[inert]") &&
          trigger.getClientRects().length
        )
          trigger.focus({ preventScroll: true });
        else if (remaining)
          focusable(remaining)[0]?.focus({ preventScroll: true });
        else {
          const fallback = document.querySelector<HTMLElement>(
            "main h1,main button,main a[href],.site-header a",
          );
          if (fallback) {
            if (!fallback.matches("a,button,input"))
              fallback.setAttribute("tabindex", "-1");
            fallback.focus({ preventScroll: true });
          }
        }
      }
    },
  };
}
/** Conditional React unmounts still get the same inert, non-interactive exit visual. */
export function exitOverlay(
  element: HTMLElement,
  onFinished: () => void,
  duration = 180,
) {
  element.dataset.state = "closing";
  element.setAttribute("aria-hidden", "true");
  element.style.setProperty("--exit-shade-opacity", "0");
  const dialog = element.querySelector<HTMLElement>(".modal");
  if (dialog) dialog.style.opacity = "0";
  element
    .querySelectorAll<HTMLElement>("section,button,a,input,textarea,select")
    .forEach((el) => (el.inert = true));
  const media = window.matchMedia("(prefers-reduced-motion: reduce)");
  let done = false;
  const finish = () => {
    if (done) return;
    done = true;
    clearTimeout(timer);
    media.removeEventListener("change", change);
    window.removeEventListener("dormprint:identity", finish);
    element.remove();
    onFinished();
  };
  const change = () => {
    if (media.matches) finish();
  };
  const timer = window.setTimeout(finish, media.matches ? 0 : duration);
  media.addEventListener("change", change);
  window.addEventListener("dormprint:identity", finish, { once: true });
}
