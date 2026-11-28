import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";

import { fetchSettings, updateSettings } from "../utils/api";
import { applyTheme, readCachedTheme, ThemeMode } from "../utils/theme";

interface ThemeContextValue {
  /** The active preference: system (follow OS), light or dark. */
  mode: ThemeMode;
  /** Persist a new preference (applies immediately, saves to the DB). */
  setMode: (mode: ThemeMode) => void;
  saveFailed: boolean;
  retrySave: () => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export const ThemeProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => {
  const [mode, setModeState] = useState<ThemeMode>(() => readCachedTheme());
  const [saveFailed, setSaveFailed] = useState(false);
  const revision = useRef(0);
  const pendingWrites = useRef(Promise.resolve());

  // Load the persisted preference from the backend once and reconcile with the
  // cached value applied at boot. The DB is the source of truth across devices.
  useEffect(() => {
    let cancelled = false;
    const readRevision = revision.current;
    fetchSettings()
      .then((settings) => {
        if (cancelled || readRevision !== revision.current) return;
        setModeState(settings.ui_theme);
        applyTheme(settings.ui_theme);
      })
      .catch(() => {
        // Keep the cached theme if settings cannot be loaded.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const saveMode = useCallback((next: ThemeMode) => {
    const nextRevision = ++revision.current;
    setSaveFailed(false);
    pendingWrites.current = pendingWrites.current.then(async () => {
      if (nextRevision !== revision.current) return;
      try {
        await updateSettings({ ui_theme: next });
      } catch {
        if (nextRevision === revision.current) setSaveFailed(true);
      }
    });
  }, []);

  const setMode = useCallback((next: ThemeMode) => {
    setModeState(next);
    applyTheme(next);
    saveMode(next);
  }, [saveMode]);

  const retrySave = useCallback(() => saveMode(mode), [mode, saveMode]);

  return (
    <ThemeContext.Provider value={{ mode, setMode, saveFailed, retrySave }}>
      {children}
    </ThemeContext.Provider>
  );
};

export function useTheme(): ThemeContextValue {
  const value = useContext(ThemeContext);
  if (!value) {
    throw new Error("useTheme must be used within a ThemeProvider");
  }
  return value;
}
