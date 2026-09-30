import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";

import { fetchSettings, updateSettings } from "../utils/api";
import { applyTheme, readCachedTheme, ThemeMode } from "../utils/theme";

interface ThemeContextValue {
  /** The active preference: system (follow OS), light or dark. */
  mode: ThemeMode;
  /** Persist a new preference (applies immediately, saves to the DB). */
  setMode: (mode: ThemeMode) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export const ThemeProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => {
  const [mode, setModeState] = useState<ThemeMode>(() => readCachedTheme());

  // Load the persisted preference from the backend once and reconcile with the
  // cached value applied at boot. The DB is the source of truth across devices.
  useEffect(() => {
    let cancelled = false;
    fetchSettings()
      .then((settings) => {
        if (cancelled) return;
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

  const setMode = useCallback((next: ThemeMode) => {
    setModeState(next);
    applyTheme(next);
    void updateSettings({ ui_theme: next }).catch(() => {
      // The theme is already applied locally; a failed save is non-fatal.
    });
  }, []);

  return (
    <ThemeContext.Provider value={{ mode, setMode }}>
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
