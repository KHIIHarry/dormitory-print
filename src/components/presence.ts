import { useLayoutEffect, useState } from "react";
export const MOTION = {
  modalIn: 320,
  shadeIn: 300,
  modalOut: 180,
  expand: 220,
  local: 140,
} as const;
export function useReducedMotion() {
  const [reduced, setReduced] = useState(
    () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  useLayoutEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => setReduced(media.matches);
    media.addEventListener("change", sync);
    sync();
    return () => media.removeEventListener("change", sync);
  }, []);
  return reduced;
}
export function usePresence(open: boolean, duration: number = MOTION.modalOut) {
  const reduced = useReducedMotion();
  const [present, setPresent] = useState(open);
  const [phase, setPhase] = useState<"entering" | "open" | "closing">(
    open ? "entering" : "closing",
  );
  useLayoutEffect(() => {
    let first = 0,
      second = 0,
      timer = 0;
    if (open) {
      setPresent(true);
      setPhase(reduced ? "open" : "entering");
      if (!reduced)
        first = requestAnimationFrame(() => {
          second = requestAnimationFrame(() => setPhase("open"));
        });
    } else {
      setPhase("closing");
      if (reduced) setPresent(false);
      else timer = window.setTimeout(() => setPresent(false), duration);
    }
    return () => {
      cancelAnimationFrame(first);
      cancelAnimationFrame(second);
      clearTimeout(timer);
    };
  }, [open, reduced, duration]);
  return { present, phase, reduced };
}
