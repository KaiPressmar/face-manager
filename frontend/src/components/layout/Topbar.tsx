import React, { useEffect, useRef, useState } from "react";
import { fetchRuntimeInfo, type ImportQueueState, type SharedLibraryState } from "../../utils/api";
import { useTheme } from "../../theme/ThemeContext";
import { ThemeMode } from "../../utils/theme";
import { subscribeToTopic } from "../../utils/events";
import BackgroundTasksStatus from "../import/BackgroundTasksStatus";
import FolderPickerModal from "../shared/FolderPickerModal";
import RuntimeStatus, { type RuntimeStatusInfo } from "./RuntimeStatus";

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

export type TopbarAction = { destination: "import" | "activities"; sequence: number };

const Topbar: React.FC<{
  route: string;
  sharedLibrary?: SharedLibraryState | null;
  sharedLibraryReadError?: string | null;
  onShowHelp: () => void;
  onImportStateChange: (open: boolean) => void;
  action?: TopbarAction;
}> = ({ route, sharedLibrary, sharedLibraryReadError, onShowHelp, onImportStateChange, action }) => {
  const [runtime, setRuntime] = useState<RuntimeStatusInfo | null>(null);
  const [runtimeUnavailable, setRuntimeUnavailable] = useState(false);
  // Adding pictures is the one setup action that stays relevant forever, so it
  // lives here and is reachable from every view instead of behind a page.
  const [showImport, setShowImport] = useState(false);
  const [importNotice, setImportNotice] = useState<string | null>(null);
  const importButton = useRef<HTMLButtonElement | null>(null);
  const importNoticeTimer = useRef<number>();
  const { mode: themeMode, setMode: setThemeMode } = useTheme();

  useEffect(() => () => window.clearTimeout(importNoticeTimer.current), []);
  useEffect(() => {
    if (action?.destination === "import") {
      importButton.current?.focus();
      setShowImport(true);
    }
  }, [action]);

  useEffect(() => {
    let cancelled = false;
    let requestGeneration = 0;
    let timer: number | undefined;
    let runtimePoll: number | undefined;
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
      if (jobs.some((job) => job.status === "running")) {
        runtimePoll ??= window.setInterval(refreshRuntime, 4000);
      } else {
        window.clearInterval(runtimePoll);
        runtimePoll = undefined;
      }
      const presentIds = new Set(jobs.map((job) => job.id));
      for (const id of checkedCheckpoints.keys()) {
        if (!presentIds.has(id)) checkedCheckpoints.delete(id);
      }

      let changed = false;
      for (const job of jobs) {
        // The pipeline advances to index loading only after the face model is ready.
        // The final snapshot also catches provider fallback during inference.
        const checkpoint = job.finished_at ?? (job.stage === "loading_model" ? "model-loading"
          : job.stage === "loading_index" ? "model-ready" : null);
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
      window.clearInterval(runtimePoll);
      unsubscribe();
    };
  }, []);

  const themeMeta = THEME_META[themeMode];

  return (
    <header className="topbar">
      <div className="topbar__actions">
        <div className="topbar__workflow">
          <button
            ref={importButton}
            type="button"
            className="topbar-import-button"
            aria-label="Bilder hinzufügen"
            onClick={() => { setShowImport(true); onImportStateChange(true); }}
            title="Bilder aus einem oder mehreren Ordnern hinzufügen"
          >
            <span className="topbar-import-button__icon" aria-hidden="true">＋</span>
            <span className="topbar-import-button__label" aria-hidden="true">
              <span className="topbar-import-button__full-label">Bilder hinzufügen</span>
              <span className="topbar-import-button__short-label">Bilder</span>
            </span>
          </button>
          <BackgroundTasksStatus sharedLibrary={sharedLibrary} sharedLibraryReadError={sharedLibraryReadError}
            openRequest={action?.destination === "activities" ? action.sequence : undefined} />
          {importNotice && (
            <span className="topbar-import-notice" role="status">
              {importNotice}
            </span>
          )}
        </div>

        <div className="topbar__utilities">
          <RuntimeStatus runtime={runtime} unavailable={runtimeUnavailable} route={route} />
          <button id="app-help-trigger" className="theme-toggle" type="button" onClick={onShowHelp}>Hilfe</button>
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
            onImportStateChange(false);
            if (started) {
              setImportNotice("Import eingereiht");
              window.clearTimeout(importNoticeTimer.current);
              importNoticeTimer.current = window.setTimeout(() => setImportNotice(null), 6000);
            }
          }}
        />
      )}
    </header>
  );
};

export default Topbar;
