import { createContext, useCallback, useContext, useLayoutEffect, useRef } from "react";
import type { AppPage } from "../utils/navigation";

export interface WorkspaceBinding<T = unknown> {
  capture: () => T;
  restore: (snapshot: T | undefined) => void;
}

export interface WorkspaceNavigationContextValue {
  register: (page: AppPage, binding: WorkspaceBinding) => () => void;
  checkpoint: (page: AppPage) => void;
  replaceWithOverview: (page: AppPage) => void;
}

export const WorkspaceNavigationContext = createContext<WorkspaceNavigationContextValue | null>(null);

/** Capture the live page at its history boundary. Keep dialogs outside snapshots. */
export function useWorkspaceNavigation<T>(page: AppPage, binding: WorkspaceBinding<T>) {
  const context = useContext(WorkspaceNavigationContext);
  const current = useRef(binding);
  useLayoutEffect(() => { current.current = binding; });
  useLayoutEffect(() => context?.register(page, {
    capture: () => current.current.capture(),
    restore: (value) => current.current.restore(value as T | undefined),
  }), [context, page]);
  return {
    checkpoint: useCallback(() => context?.checkpoint(page), [context, page]),
    replaceWithOverview: useCallback(() => context?.replaceWithOverview(page), [context, page]),
  };
}
