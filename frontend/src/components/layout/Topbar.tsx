import React, { useEffect, useState } from "react";
import { fetchRuntimeInfo, type ImportQueueState, type RuntimeInfo } from "../../utils/api";
import { useTheme } from "../../theme/ThemeContext";
import { ThemeMode } from "../../utils/theme";
import { subscribeToTopic } from "../../utils/events";
import BackgroundTasksStatus from "../import/BackgroundTasksStatus";
import FolderPickerModal from "../shared/FolderPickerModal";

const THEME_CYCLE: Record<ThemeMode, ThemeMode> = {
  system: "light",
  light: "dark",
  dark: "system",
};

const THEME_META: Record<ThemeMode, { icon: string; label: string }> = {
  system: { icon: "◐", label: "System" },
  light: { icon: "☀", label: "Hell" },
  dark: { icon: "☾", label: "Dunkel" },
};

const Topbar: React.FC = () => {
  const [runtime, setRuntime] = useState<RuntimeInfo | null>(null);
  const [runtimeUnavailable, setRuntimeUnavailable] = useState(false);
  // Adding pictures is the one setup action that stays relevant forever, so it
  // lives here and is reachable from every view instead of behind a page.
  const [showImport, setShowImport] = useState(false);
  const [importNotice, setImportNotice] = useState<string | null>(null);
  const { mode: themeMode, setMode: setThemeMode } = useTheme();

  useEffect(() => {
    let cancelled = false;
    let requestGeneration = 0;
    let timer: number | undefined;
    const checkedCheckpoints = new Map<string, string>();

    const refreshRuntime = () => {
      const request = ++requestGeneration;
      void fetchRuntimeInfo()
        .then((info) => {
          if (cancelled || request !== requestGeneration) return;
          setRuntime(info);
          setRuntimeUnavailable(false);
        })
        .catch(() => {
          if (!cancelled && request === requestGeneration) setRuntimeUnavailable(true);
        });
    };

    refreshRuntime();
    const unsubscribe = subscribeToTopic<ImportQueueState>("imports", ({ jobs }) => {
      const presentIds = new Set(jobs.map((job) => job.id));
      for (const id of checkedCheckpoints.keys()) {
        if (!presentIds.has(id)) checkedCheckpoints.delete(id);
      }

      let changed = false;
      for (const job of jobs) {
        // The pipeline advances to index loading only after the face model is ready.
        // The final snapshot also catches provider fallback during inference.
        const checkpoint = job.finished_at ?? (job.stage === "loading_index" ? "model-ready" : null);
        if (checkpoint && checkpoint !== checkedCheckpoints.get(job.id)) {
          checkedCheckpoints.set(job.id, checkpoint);
          changed = true;
        }
      }
      if (changed) {
        window.clearTimeout(timer);
        timer = window.setTimeout(refreshRuntime, 120);
      }
    });

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      unsubscribe();
    };
  }, []);

  const mode = runtime?.compute_mode;
  const modeLabel = mode
    ? mode.toUpperCase()
    : runtimeUnavailable
      ? "Nicht verfügbar"
      : "Wird erkannt";
  const modeTitle = runtime
    ? mode === "gpu"
      ? "Gesichtserkennung nutzt die Grafikkarte"
      : "Gesichtserkennung nutzt den Prozessor"
    : runtimeUnavailable
      ? "Die verwendete Beschleunigung konnte nicht ermittelt werden"
      : "Verwendete Beschleunigung wird ermittelt";

  const themeMeta = THEME_META[themeMode];

  return (
    <header className="topbar">
      <div className="topbar__actions">
        <div className="topbar__workflow">
          <button
            type="button"
            className="topbar-import-button"
            aria-label="Bilder hinzufügen"
            onClick={() => setShowImport(true)}
            title="Einen Ordner mit Bildern zur Erkennung hinzufügen"
          >
            <span className="topbar-import-button__icon" aria-hidden="true">＋</span>
            <span className="topbar-import-button__label">Bilder hinzufügen</span>
          </button>
          <BackgroundTasksStatus />
          {importNotice && (
            <span className="topbar-import-notice" role="status">
              {importNotice}
            </span>
          )}
        </div>

        <div className="topbar__utilities">
          <span
            className={`compute-mode-badge compute-mode-badge--${mode ?? "unknown"}`}
            title={modeTitle}
          >
            <span className="compute-mode-badge__indicator" aria-hidden="true" />
            {modeLabel}
          </span>
          <button
            type="button"
            className="theme-toggle"
            onClick={() => setThemeMode(THEME_CYCLE[themeMode])}
            title={`Darstellung: ${themeMeta.label} — zum Wechseln klicken`}
            aria-label={`Darstellung wechseln (aktuell: ${themeMeta.label})`}
          >
            <span aria-hidden="true">{themeMeta.icon}</span>
            <span className="theme-toggle__label">{themeMeta.label}</span>
          </button>
        </div>
      </div>

      {showImport && (
        <FolderPickerModal
          onClose={(started) => {
            setShowImport(false);
            if (started) {
              setImportNotice("Ordner hinzugefügt");
              window.setTimeout(() => setImportNotice(null), 6000);
            }
          }}
        />
      )}
    </header>
  );
};

export default Topbar;
