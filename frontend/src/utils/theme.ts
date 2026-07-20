export type ThemeMode = "system" | "light" | "dark";

export const THEME_MODES: ThemeMode[] = ["system", "light", "dark"];

const STORAGE_KEY = "fm-theme";

function isThemeMode(value: unknown): value is ThemeMode {
  return value === "system" || value === "light" || value === "dark";
}

/**
 * Read the last theme the user pinned from localStorage. Used at boot to apply
 * an explicit override before the backend settings have loaded, so an explicit
 * Light/Dark choice does not flash the wrong theme. "system" needs no cache
 * because the CSS `prefers-color-scheme` media query resolves it without JS.
 */
export function readCachedTheme(): ThemeMode {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (isThemeMode(stored)) return stored;
  } catch {
    // localStorage may be unavailable (private mode); fall back to system.
  }
  return "system";
}

/**
 * Apply a theme by stamping `data-theme` on the document root and caching it.
 * The CSS in neon.css keys light/dark values off this attribute (with a
 * `prefers-color-scheme` fallback for "system").
 */
export function applyTheme(mode: ThemeMode): void {
  document.documentElement.dataset.theme = mode;
  try {
    window.localStorage.setItem(STORAGE_KEY, mode);
  } catch {
    // Ignore persistence failures; the DB remains the source of truth.
  }
}
