import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  DEMO,
  mutation,
  request,
  invalidateRequestContext,
} from "../lib/api";
let identityGeneration = 0;
export const getIdentityGeneration = () => identityGeneration;
export function advanceIdentity() {
  invalidateRequestContext();
  identityGeneration++;
  if (typeof window !== "undefined")
    window.dispatchEvent(new Event("dormprint:identity"));
  return identityGeneration;
}
export async function growthRequest<T>(
  path: string,
  options: { method?: string; body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  const generation = identityGeneration;
  if (typeof navigator !== "undefined" && !navigator.onLine)
    throw new ApiError("OFFLINE", "当前离线，请联网后再试", 0);
  let result: T;
  if (DEMO) {
    const module = await import("./demo");
    result = (await module.demoRequest(path, options)) as T;
  } else
    result =
      options.method && options.method !== "GET"
        ? await mutation<T>(path, options.body || {}, options.method, {
            signal: options.signal,
          })
        : await request<T>(path, { signal: options.signal });
  if (generation !== identityGeneration)
    throw new ApiError(
      "AUTH_CONTEXT_CHANGED",
      "身份已变更，请重新加载当前内容",
      409,
    );
  return result;
}
export function useGrowthResource<T>(path: string | null) {
  const [snapshot, setSnapshot] = useState<{
    path: string | null;
    generation: number;
    data: T | null;
    pending: boolean;
    error: string;
  }>({
    path,
    generation: identityGeneration,
    data: null,
    pending: !!path,
    error: "",
  });
  const [generation, setGeneration] = useState(identityGeneration);
  const sequence = useRef(0),
    active = useRef<AbortController | null>(null);
  const flight = useRef<{ key: string; promise: Promise<void> } | null>(null);
  const current = useRef({ path, generation: identityGeneration });
  current.current = { path, generation: identityGeneration };
  useEffect(() => {
    const invalidate = () => {
      sequence.current++;
      active.current?.abort();
      setSnapshot({
        path: current.current.path,
        generation: identityGeneration,
        data: null,
        pending: !!current.current.path,
        error: "",
      });
      setGeneration(identityGeneration);
    };
    window.addEventListener("dormprint:identity", invalidate);
    return () => window.removeEventListener("dormprint:identity", invalidate);
  }, []);
  const performReload = async () => {
    const seq = ++sequence.current,
      owner = { path, generation: identityGeneration };
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    const owns = () =>
      seq === sequence.current &&
      owner.path === current.current.path &&
      owner.generation === identityGeneration &&
      !controller.signal.aborted;
    if (!path) {
      setSnapshot({
        path,
        generation: owner.generation,
        data: null,
        pending: false,
        error: "",
      });
      return;
    }
    setSnapshot((old) => ({
      path,
      generation: owner.generation,
      data:
        old.path === path && old.generation === owner.generation
          ? old.data
          : null,
      pending: true,
      error: "",
    }));
    try {
      const response = await growthRequest<T>(path, {
        signal: controller.signal,
      });
      if (owns())
        setSnapshot({
          path,
          generation: owner.generation,
          data: response,
          pending: false,
          error: "",
        });
    } catch (error) {
      if (!owns()) return;
      const item = error as { status?: number; message?: string };
      setSnapshot((old) => ({
        ...old,
        data: item.status === 401 || item.status === 403 ? null : old.data,
        pending: false,
        error: item.message || "暂时无法读取当前内容",
      }));
    }
  };
  const reload = () => {
    const key = `${identityGeneration}:${path}`;
    if (flight.current?.key === key && !active.current?.signal.aborted)
      return flight.current.promise;
    const promise = performReload();
    flight.current = { key, promise };
    void promise.finally(() => {
      if (flight.current?.promise === promise) flight.current = null;
    });
    return promise;
  };
  useEffect(() => {
    void reload();
    const verify = () => {
      if (document.visibilityState === "visible" && navigator.onLine)
        void reload();
    };
    window.addEventListener("online", verify);
    document.addEventListener("visibilitychange", verify);
    return () => {
      sequence.current++;
      active.current?.abort();
      window.removeEventListener("online", verify);
      document.removeEventListener("visibilitychange", verify);
    };
  }, [path, generation]);
  const valid =
    snapshot.path === path && snapshot.generation === identityGeneration;
  const data = valid ? snapshot.data : null,
    hasData = data !== null;
  const pending = !!path && (!valid || snapshot.pending);
  return {
    data,
    loading: pending && !hasData,
    refreshing: pending && hasData,
    hasData,
    stale: !valid,
    error: valid ? snapshot.error : "",
    reload,
  };
}
