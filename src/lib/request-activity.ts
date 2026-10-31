let count = 0;
const listeners = new Set<() => void>();
export const requestActivity = {
  subscribe(listener: () => void) { listeners.add(listener); return () => listeners.delete(listener); },
  snapshot() { return count; },
  begin() {
    count++;
    listeners.forEach((listener) => listener());
    let ended = false;
    return () => {
      if (ended) return;
      ended = true;
      count = Math.max(0, count - 1);
      listeners.forEach((listener) => listener());
    };
  },
};
