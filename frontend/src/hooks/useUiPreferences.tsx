import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { MapStyle } from "../components/map/WorldMap";
import type { ImageGridSize } from "../components/people/ImageGrid";
import { API_BASE, apiFetch } from "../utils/api";

interface UiPreferences {
  map_style: MapStyle;
  map_country_names: boolean;
  image_grid_size: ImageGridSize;
  help_introduction_seen: boolean;
}

type PreferenceValues = Partial<UiPreferences>;
type PreferenceFailure = "load" | "save";
const PREFERENCE_TIMEOUT_MS = 5000;

const defaults: UiPreferences = {
  map_style: "standard",
  map_country_names: false,
  image_grid_size: "medium",
  help_introduction_seen: false,
};

function readLegacyPreference(key: string): string | null {
  try {
    return window.localStorage?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

function readLegacyPreferences(): PreferenceValues {
  const values: PreferenceValues = {};
  const style = readLegacyPreference("face-manager:map-style");
  if (style === "standard" || style === "landscape" || style === "satellite") values.map_style = style;
  const names = readLegacyPreference("face-manager:map-country-names");
  if (names === "true" || names === "false") values.map_country_names = names === "true";
  const size = readLegacyPreference("face-manager:image-grid-size");
  if (size === "xsmall" || size === "small" || size === "medium" || size === "large") values.image_grid_size = size;
  if (readLegacyPreference("face-manager:help-introduction-v1") === "seen") values.help_introduction_seen = true;
  return values;
}

async function requestPreferences({ values, onlyIfAbsent, signal }: {
  values?: PreferenceValues;
  onlyIfAbsent?: boolean;
  signal?: AbortSignal;
}): Promise<PreferenceValues> {
  const response = await apiFetch(`${API_BASE}/ui-preferences`, {
    signal,
    ...(values && {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(onlyIfAbsent ? { values, only_if_absent: true } : { values }),
    }),
  });
  if (!response.ok) throw new Error(`UI preferences request failed (${response.status})`);
  return await response.json();
}

interface UiPreferencesContextValue {
  values: UiPreferences;
  loading: boolean;
  failure: PreferenceFailure | null;
  setPreference: <Key extends keyof UiPreferences>(key: Key, value: UiPreferences[Key]) => void;
  retry: () => void;
}

const UiPreferencesContext = createContext<UiPreferencesContextValue | null>(null);

export function UiPreferencesProvider({ children }: { children: React.ReactNode }) {
  const [legacy] = useState(readLegacyPreferences);
  const [values, setValues] = useState<UiPreferences>(() => ({ ...defaults, ...legacy }));
  const [loading, setLoading] = useState(true);
  const [loadFailure, setLoadFailure] = useState<PreferenceFailure | null>(null);
  const [saveFailed, setSaveFailed] = useState(false);
  const started = useRef(false);
  const needsLoad = useRef(true);
  const busy = useRef(false);
  const pending = useRef<PreferenceValues>({});

  const synchronize = useCallback(async (load: boolean) => {
    if (busy.current) return;
    busy.current = true;
    const applyStored = (stored: PreferenceValues) => {
      setValues({ ...defaults, ...legacy, ...stored, ...pending.current });
    };

    if (load) {
      setLoading(true);
      setLoadFailure(null);
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), PREFERENCE_TIMEOUT_MS);
      let failure: PreferenceFailure = "load";
      try {
        const stored = await requestPreferences({ signal: controller.signal });
        applyStored(stored);
        const migration = { ...legacy };
        for (const key of Object.keys(migration) as (keyof UiPreferences)[]) {
          if (stored[key] !== undefined || pending.current[key] !== undefined) delete migration[key];
        }
        if (Object.keys(migration).length) {
          failure = "save";
          applyStored(await requestPreferences({ values: migration, onlyIfAbsent: true, signal: controller.signal }));
        }
        needsLoad.current = false;
      } catch {
        setLoadFailure(failure);
      } finally {
        window.clearTimeout(timeout);
        setLoading(false);
      }
    }

    // Keep at most one request in flight and one pending value for each preference.
    while (Object.keys(pending.current).length) {
      const sending = pending.current;
      pending.current = {};
      setSaveFailed(false);
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), PREFERENCE_TIMEOUT_MS);
      try {
        applyStored(await requestPreferences({ values: sending, signal: controller.signal }));
      } catch {
        pending.current = { ...sending, ...pending.current };
        setSaveFailed(true);
        break;
      } finally {
        window.clearTimeout(timeout);
      }
    }
    busy.current = false;
  }, [legacy]);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void synchronize(true);
  }, [synchronize]);

  function setPreference<Key extends keyof UiPreferences>(key: Key, value: UiPreferences[Key]) {
    pending.current[key] = value;
    setValues((current) => ({ ...current, [key]: value }));
    // The startup effect handles any edits made before it runs.
    if (started.current) void synchronize(false);
  }

  return (
    <UiPreferencesContext.Provider value={{ values, loading, failure: saveFailed ? "save" : loadFailure,
      setPreference, retry: () => { void synchronize(needsLoad.current); } }}>
      {children}
    </UiPreferencesContext.Provider>
  );
}

export function useUiPreferences() {
  const context = useContext(UiPreferencesContext);
  if (!context) throw new Error("UiPreferencesProvider is missing");
  return context;
}
