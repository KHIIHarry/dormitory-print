/** A response owns a resource only until a newer request or identity takes over. */
export class LatestTask {
  private versions = new Map<string, number>();
  private generation = 0;
  begin(key: string) {
    const version = (this.versions.get(key) || 0) + 1;
    const generation = this.generation;
    this.versions.set(key, version);
    return {
      current: () =>
        this.generation === generation && this.versions.get(key) === version,
    };
  }
  invalidate() {
    this.generation++;
    this.versions.clear();
  }
}
/** Acquired synchronously, before React can paint a disabled button. */
export class ActionLock {
  private locks = new Map<string, symbol>();
  acquire(key: string) {
    if (this.locks.has(key)) return;
    const token = Symbol(key);
    this.locks.set(key, token);
    return () => {
      if (this.locks.get(key) === token) this.locks.delete(key);
    };
  }
  has(key: string) {
    return this.locks.has(key);
  }
  clear() {
    this.locks.clear();
  }
}
export function progressValue(loaded: number, total?: number) {
  if (!Number.isFinite(loaded) || !Number.isFinite(total) || total! <= 0)
    return undefined;
  return Math.max(0, Math.min(100, (loaded / total!) * 100));
}
export type ResourceSnapshot = {
  key: string;
  loading: boolean;
  hasData: boolean;
  error: string;
};
export function resourcePhase(
  s: Pick<ResourceSnapshot, "loading" | "hasData" | "error"> & {
    empty?: boolean;
  },
) {
  if (s.loading) return s.hasData ? "refreshing" : "initial";
  if (s.error) return s.hasData ? "refresh-error" : "error";
  return s.empty ? "empty" : "ready";
}
