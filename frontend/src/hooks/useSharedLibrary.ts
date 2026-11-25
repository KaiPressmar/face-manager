import { useCallback, useEffect, useRef, useState } from "react";
import { fetchSharedLibrary, sharedLibraryFolders, type SharedLibraryState } from "../utils/api";

export function useSharedLibrary() {
  const [resource, setResource] = useState<SharedLibraryState | null>(null);
  const [readError, setReadError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const readInFlight = useRef<AbortController | null>(null);
  const mutationInFlight = useRef(false);

  const refresh = useCallback(async (force = false) => {
    if (mutationInFlight.current || (readInFlight.current && !force)) return null;
    readInFlight.current?.abort();
    const controller = new AbortController();
    readInFlight.current = controller;
    try {
      const next = await fetchSharedLibrary(controller.signal);
      if (controller.signal.aborted) return null;
      setResource(next);
      setReadError(null);
      return next;
    } catch (error) {
      if (!controller.signal.aborted) {
        setReadError(error instanceof Error ? error.message : "Der gemeinsame Stand konnte nicht geladen werden.");
      }
      return null;
    } finally {
      if (readInFlight.current === controller) readInFlight.current = null;
    }
  }, []);

  const mutate = useCallback(async (action: () => Promise<SharedLibraryState>) => {
    if (mutationInFlight.current) return null;
    mutationInFlight.current = true;
    readInFlight.current?.abort();
    readInFlight.current = null;
    setWorking(true);
    try {
      const next = await action();
      setResource(next);
      setReadError(null);
      return next;
    } finally {
      mutationInFlight.current = false;
      setWorking(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    return () => {
      readInFlight.current?.abort();
      readInFlight.current = null;
    };
  }, [refresh]);

  const folders = sharedLibraryFolders(resource);
  const configured = folders.length > 0;
  useEffect(() => {
    if (!configured) return;
    const refreshVisible = () => { if (!document.hidden) void refresh(); };
    const timer = window.setInterval(refreshVisible, 5000);
    document.addEventListener("visibilitychange", refreshVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshVisible);
    };
  }, [configured, refresh]);

  return { resource, readError, working, configured, folders, refresh, mutate };
}

export type SharedLibraryController = ReturnType<typeof useSharedLibrary>;
