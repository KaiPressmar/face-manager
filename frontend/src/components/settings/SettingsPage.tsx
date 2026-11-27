import React, { useEffect, useRef, useState } from "react";
import {
  AppSettings,
  ImagePathCleanupStatus,
  autoTuneClusterThreshold,
  checkForUpdates,
  exportDatabase,
  fetchSettings,
  fetchImagePathCleanup,
  importDatabase,
  reclusterAllFaces,
  startImagePathCleanup,
  updateSettings,
} from "../../utils/api";
import { useTheme } from "../../theme/ThemeContext";
import { ThemeMode } from "../../utils/theme";
import type { SettingsSection } from "../../utils/navigation";
import PreviewCacheSettings from "./PreviewCacheSettings";
import SharedLibrarySettings from "./SharedLibrarySettings";
import type { SharedLibraryController } from "../../hooks/useSharedLibrary";
import { countWithLabel } from "../../utils/germanFormatting";
import { downloadRestoreBackup, hasRestoreBackup, previewDatabaseRestore, type RestorePreview } from "../../utils/libraryMaintenance";
import RestoreConfirmation from "./RestoreConfirmation";
import ArchiveRepair from "./ArchiveRepair";

const THEME_OPTIONS: { value: ThemeMode; label: string; description: string }[] = [
  {
    value: "system",
    label: "System",
    description: "Übernimmt automatisch die Hell-/Dunkel-Einstellung des Betriebssystems.",
  },
  {
    value: "light",
    label: "Hell",
    description: "Verwendet immer die helle Darstellung.",
  },
  {
    value: "dark",
    label: "Dunkel",
    description: "Verwendet immer die dunkle Darstellung.",
  },
];

const PERSON_NAME_JOINER_PRESETS = [
  { label: "Komma", value: ", " },
  { label: "Punkt", value: " . " },
  { label: "Und", value: " und " },
  { label: "Plus", value: " + " },
];
const FILENAME_TO_NAMES_SEPARATOR_PRESETS = [
  { label: "Leerzeichen", value: " " },
  { label: "Bindestrich", value: " - " },
  { label: "Unterstrich", value: "_" },
  { label: "Doppelter Bindestrich", value: " -- " },
];

const SAMPLE_BASENAME = "Sommerurlaub.jpg";
const SAMPLE_PERSON_NAMES = ["Kai", "Regina"];
const LOG_LEVEL_OPTIONS = [
  {
    label: "Nur Fehler",
    value: "ERROR",
    description: "Speichert nur Fehler und Abstürze. Für die normale Nutzung empfohlen.",
  },
  {
    label: "Warnungen",
    value: "WARNING",
    description: "Speichert zusätzlich Warnungen zu möglichen Problemen.",
  },
  {
    label: "Informationen",
    value: "INFO",
    description: "Speichert zusätzlich wichtige Arbeitsschritte für die Fehlersuche.",
  },
  {
    label: "Sehr ausführlich",
    value: "DEBUG",
    description: "Speichert möglichst viele Details. Nur vorübergehend zur Fehlersuche verwenden.",
  },
] as const;

function saveDatabaseBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

function buildSuffixFormatPreview(
  blockSeparator: string,
  joiner: string,
) {
  const extensionIndex = SAMPLE_BASENAME.lastIndexOf(".");
  const stem =
    extensionIndex >= 0
      ? SAMPLE_BASENAME.slice(0, extensionIndex)
      : SAMPLE_BASENAME;
  const extension = extensionIndex >= 0 ? SAMPLE_BASENAME.slice(extensionIndex) : "";
  const joinedNames = SAMPLE_PERSON_NAMES.join(joiner);
  const nextStem = `${stem}${blockSeparator}${joinedNames}`.trim();
  return `${nextStem}${extension}`;
}

function hasInvalidFilenameCharacters(value: string) {
  return /[<>:"/\\|?*\u0000-\u001f]/.test(value);
}

function visibleSeparator(value: string) {
  if (!value) return "Kein Zeichen";
  return value.replaceAll(" ", "·");
}

const SETTINGS_NAV_ITEMS: { section: SettingsSection; label: string }[] = [
  { section: "erkennung", label: "Erkennung und Gruppierung" },
  { section: "dateinamen", label: "Dateinamen" },
  { section: "darstellung", label: "Darstellung" },
  { section: "updates", label: "Updates" },
  { section: "daten", label: "Daten und Wartung" },
];

type FeedbackArea = "erkennung" | "updates" | "protokoll";

const ImagePathCleanupCard = React.memo(function ImagePathCleanupCard({ active }: { active: boolean }) {
  const [pathCleanup, setPathCleanup] = useState<ImagePathCleanupStatus | null>(null);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    let timer: number | undefined;
    const refresh = async () => {
      try {
        const next = await fetchImagePathCleanup();
        if (cancelled) return;
        setPathCleanup(next);
        if (next.status === "queued" || next.status === "running") {
          timer = window.setTimeout(refresh, 1000);
        }
      } catch {
        if (!cancelled) timer = window.setTimeout(refresh, 5000);
      }
    };
    void refresh();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [active, revision]);

  const running = pathCleanup?.status === "queued" || pathCleanup?.status === "running";
  const start = async () => {
    if (!window.confirm(
      "Fehlende Dateien aus der Bibliothek entfernen? Wenn ein Bild keinen weiteren Speicherort hat, gehen auch seine Gesichtszuordnungen verloren. Schließe vorher alle Fotolaufwerke an.",
    )) return;
    setError(null);
    try {
      setPathCleanup(await startImagePathCleanup());
      setRevision((current) => current + 1);
    } catch (cleanupError) {
      setError(cleanupError instanceof Error ? cleanupError.message : "Die Pfadprüfung konnte nicht gestartet werden.");
    }
  };

  let summary = "Der Status der letzten Prüfung wird geladen…";
  switch (pathCleanup?.status) {
    case "queued":
      summary = "Die Prüfung wartet, bis keine wichtigeren Hintergrundaufgaben laufen.";
      break;
    case "running":
      summary = `${countWithLabel(pathCleanup.scanned_paths, "Speicherort", "Speicherorte")} geprüft…`;
      break;
    case "completed":
      summary = `${countWithLabel(pathCleanup.scanned_paths, "Speicherort", "Speicherorte")} geprüft, ${pathCleanup.unavailable_paths.toLocaleString("de-DE")} nicht erreichbar. ${countWithLabel(pathCleanup.removed_paths, "Speicherort", "Speicherorte")} und ${countWithLabel(pathCleanup.removed_images, "Bild", "Bilder")} entfernt.`;
      break;
    case "failed":
      summary = "Die letzte Pfadprüfung konnte nicht abgeschlossen werden.";
      break;
    case "idle":
      summary = "Noch keine Pfadprüfung in dieser Sitzung ausgeführt.";
      break;
  }

  return (
    <section className="settings-card">
      <h2 className="settings-card__title">Bildspeicherorte prüfen</h2>
      <p className="settings-card__copy">
        Die automatische Prüfung meldet fehlende Bilder. Bei einer manuellen
        Bereinigung lassen sich Einträge für fehlende Dateien an erreichbaren
        Speicherorten entfernen.
      </p>
      <div className="settings-maintenance-status" aria-live="polite">{summary}</div>
      <div className="settings-actions">
        <button className="neon-card" type="button" onClick={() => void start()} disabled={running}>
          {running ? "Pfadprüfung läuft…" : "Fehlende Bildpfade bereinigen"}
        </button>
      </div>
      {error && <p className="settings-feedback settings-feedback--error" role="alert">{error}</p>}
    </section>
  );
});

const SettingsPage: React.FC<{
  active: boolean;
  activeSection?: SettingsSection;
  sharedLibrary: SharedLibraryController;
  onNavigateSection: (section: SettingsSection) => void;
}> = ({ active, activeSection, sharedLibrary, onNavigateSection }) => {
  const { mode: themeMode, setMode: setThemeMode } = useTheme();
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [strictnessInput, setStrictnessInput] = useState("0.50");
  const [filenameBlockSeparatorInput, setFilenameBlockSeparatorInput] =
    useState(" ");
  const [personJoinerInput, setPersonJoinerInput] = useState(", ");
  const [fileLogLevelInput, setFileLogLevelInput] =
    useState<AppSettings["file_log_level"]>("ERROR");
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [isSavingClustering, setIsSavingClustering] = useState(false);
  const [clusteringSaveState, setClusteringSaveState] =
    useState<"saved" | "pending" | "error">("saved");
  const [isAutoTuning, setIsAutoTuning] = useState(false);
  const [isReclustering, setIsReclustering] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [isImporting, setIsImporting] = useState(false);
  const [isInspectingBackup, setIsInspectingBackup] = useState(false);
  const [pendingRestore, setPendingRestore] = useState<{ file: File; preview: RestorePreview } | null>(null);
  const [restoreBackupAvailable, setRestoreBackupAvailable] = useState(false);
  const [backupFeedback, setBackupFeedback] = useState<{ text: string; error?: boolean } | null>(null);
  const backupInFlight = useRef(false);
  const [isCheckingUpdates, setIsCheckingUpdates] = useState(false);
  const [feedback, setFeedback] = useState<Partial<Record<FeedbackArea, { text: string; error: boolean }>>>({});
  const [filenameFeedback, setFilenameFeedback] = useState<{ text: string; error: boolean } | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const focusSection = useRef<SettingsSection>();
  const strictnessSaveTimerRef = useRef<number | null>(null);
  const strictnessRevisionRef = useRef(0);
  const pendingStrictnessRef = useRef<{ value: number; revision: number } | null>(null);
  const strictnessSaveInFlightRef = useRef(false);
  const queuedStrictnessRef = useRef<{ value: number; revision: number } | null>(null);
  const suffixPreview = buildSuffixFormatPreview(
    filenameBlockSeparatorInput,
    personJoinerInput,
  );
  const missingBlockSeparator = filenameBlockSeparatorInput.length === 0;
  const invalidBlockSeparator = missingBlockSeparator || hasInvalidFilenameCharacters(filenameBlockSeparatorInput);
  const invalidPersonJoiner = hasInvalidFilenameCharacters(personJoinerInput);
  const filenameFormatChanged = settings !== null && (
    filenameBlockSeparatorInput !== settings.filename_person_block_separator ||
    personJoinerInput !== settings.filename_person_joiner
  );
  const canSaveFilenameFormat = filenameFormatChanged && !invalidBlockSeparator && !invalidPersonJoiner && !isSaving;
  const [settingsLoadError, setSettingsLoadError] = useState<string | null>(null);
  const [settingsLoadAttempt, setSettingsLoadAttempt] = useState(0);

  const report = (area: FeedbackArea, text?: string, error = false) => {
    setFeedback((current) => ({ ...current, [area]: text ? { text, error } : undefined }));
  };

  const sectionFeedback = (area: FeedbackArea) => {
    const result = feedback[area];
    return result && <p className={result.error ? "settings-feedback settings-feedback--error" : "settings-feedback"}
      role={result.error ? "alert" : "status"}>{result.text}</p>;
  };

  useEffect(() => {
    let active = true;
    void hasRestoreBackup().then((available) => {
      if (active) setRestoreBackupAvailable(available);
    }).catch(() => {});
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setSettingsLoadError(null);

    fetchSettings()
      .then((data) => {
        if (cancelled) return;
        setSettings(data);
        setStrictnessInput(data.clustering_strictness.toFixed(2));
        setFilenameBlockSeparatorInput(data.filename_person_block_separator);
        setPersonJoinerInput(data.filename_person_joiner);
        setFileLogLevelInput(data.file_log_level);
      })
      .catch((loadError) => {
        if (cancelled) return;
        setSettingsLoadError(
          loadError instanceof Error
            ? loadError.message
            : "Die Einstellungen konnten nicht geladen werden.",
        );
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });

    return () => {
      cancelled = true;
      if (strictnessSaveTimerRef.current !== null) {
        window.clearTimeout(strictnessSaveTimerRef.current);
      }
    };
  }, [settingsLoadAttempt]);

  useEffect(() => {
    if (isLoading) return;
    const animationFrame = window.requestAnimationFrame(() => {
      const targetId = activeSection ? `settings-${activeSection}` : "settings-start";
      const target = document.getElementById(targetId);
      if (!target || target.closest("[inert]")) return;
      target.scrollIntoView({ block: "start" });
      if (activeSection && focusSection.current === activeSection) {
        focusSection.current = undefined;
        target.querySelector<HTMLElement>("h2")?.focus({ preventScroll: true });
      }
    });
    return () => window.cancelAnimationFrame(animationFrame);
  }, [activeSection, isLoading]);

  const persistStrictness = async (value: number, revision: number) => {
    if (strictnessSaveInFlightRef.current) {
      queuedStrictnessRef.current = { value, revision };
      return;
    }
    strictnessSaveInFlightRef.current = true;
    setIsSavingClustering(true);
    setClusteringSaveState("pending");
    try {
      const next = await updateSettings({
        clustering_strictness: value,
      });
      if (strictnessRevisionRef.current === revision) {
        setSettings(next);
        setClusteringSaveState("saved");
      }
    } catch (saveError) {
      if (strictnessRevisionRef.current === revision) {
        setClusteringSaveState("error");
        report("erkennung",
          saveError instanceof Error
            ? saveError.message
            : "Die Einstellung für die Gruppierung konnte nicht gespeichert werden.",
          true,
        );
      }
    } finally {
      strictnessSaveInFlightRef.current = false;
      const queued = queuedStrictnessRef.current;
      queuedStrictnessRef.current = null;
      if (queued) {
        void persistStrictness(queued.value, queued.revision);
      } else if (strictnessRevisionRef.current === revision) {
        setIsSavingClustering(false);
      }
    }
  };

  const scheduleStrictnessSave = (value: string) => {
    setStrictnessInput(value);
    report("erkennung");
    setClusteringSaveState("pending");
    strictnessRevisionRef.current += 1;
    const revision = strictnessRevisionRef.current;
    pendingStrictnessRef.current = { value: Number(value), revision };
    if (strictnessSaveTimerRef.current !== null) {
      window.clearTimeout(strictnessSaveTimerRef.current);
    }
    strictnessSaveTimerRef.current = window.setTimeout(() => {
      strictnessSaveTimerRef.current = null;
      pendingStrictnessRef.current = null;
      void persistStrictness(Number(value), revision);
    }, 300);
  };

  const flushStrictnessSave = () => {
    const pending = pendingStrictnessRef.current;
    if (!pending) return;
    if (strictnessSaveTimerRef.current !== null) {
      window.clearTimeout(strictnessSaveTimerRef.current);
      strictnessSaveTimerRef.current = null;
    }
    pendingStrictnessRef.current = null;
    void persistStrictness(pending.value, pending.revision);
  };

  const handleResetStrictness = () => {
    if (!settings) return;
    const value = settings.clustering_strictness_default;
    if (strictnessSaveTimerRef.current !== null) {
      window.clearTimeout(strictnessSaveTimerRef.current);
      strictnessSaveTimerRef.current = null;
    }
    pendingStrictnessRef.current = null;
    strictnessRevisionRef.current += 1;
    const revision = strictnessRevisionRef.current;
    setStrictnessInput(value.toFixed(2));
    report("erkennung");
    void persistStrictness(value, revision);
  };

  const handleAutoTuneThreshold = async () => {
    if (strictnessSaveTimerRef.current !== null) {
      window.clearTimeout(strictnessSaveTimerRef.current);
      strictnessSaveTimerRef.current = null;
    }
    pendingStrictnessRef.current = null;
    strictnessRevisionRef.current += 1;
    setIsAutoTuning(true);
    report("erkennung");
    try {
      const result = await autoTuneClusterThreshold();
      setStrictnessInput(result.strictness.toFixed(2));
      setSettings((current) =>
        current
          ? {
              ...current,
              cluster_distance_threshold: result.threshold,
              clustering_strictness: result.strictness,
              clustering_profile: result.profile,
            }
          : current,
      );
      setClusteringSaveState("saved");
      report("erkennung",
        `Die passende Einstellung wurde aus ${result.person_count} bestätigten Personen ermittelt und gespeichert. ` +
          "Mit „Gesichtsgruppen jetzt neu ordnen“ wendest du sie auch auf vorhandene Gesichter an.",
      );
    } catch (tuneError) {
      report("erkennung",
        tuneError instanceof Error
          ? tuneError.message
          : "Die Einstellung für die Gruppierung konnte nicht automatisch optimiert werden.",
        true,
      );
    } finally {
      setIsAutoTuning(false);
    }
  };

  const handleRecluster = async () => {
    setIsReclustering(true);
    report("erkennung");
    try {
      const { scheduled, status } = await reclusterAllFaces();
      report("erkennung",
        !scheduled
          ? "Es sind keine Gesichter vorhanden, die neu geordnet werden könnten."
          : status === "queued"
            ? "Das Neu-Ordnen ist eingeplant und startet automatisch, sobald der laufende Import abgeschlossen ist."
            : "Die Gesichtsgruppen werden jetzt neu geordnet.",
      );
    } catch (reclusterError) {
      report("erkennung",
        reclusterError instanceof Error
          ? reclusterError.message
          : "Die Gesichtsgruppen konnten nicht neu geordnet werden.",
        true,
      );
    } finally {
      setIsReclustering(false);
    }
  };

  const handleSaveSuffixFormat = async () => {
    if (!canSaveFilenameFormat) return;
    setIsSaving(true);
    setFilenameFeedback(null);
    try {
      const next = await updateSettings({
        filename_person_block_separator: filenameBlockSeparatorInput,
        filename_person_joiner: personJoinerInput,
      });
      setSettings(next);
      setFilenameBlockSeparatorInput(next.filename_person_block_separator);
      setPersonJoinerInput(next.filename_person_joiner);
      setFilenameFeedback({ text: "Einstellungen für Personennamen im Dateinamen gespeichert.", error: false });
    } catch (saveError) {
      setFilenameFeedback({
        text: saveError instanceof Error
          ? saveError.message
          : "Die Dateinamen-Einstellungen konnten nicht gespeichert werden.",
        error: true,
      });
    } finally {
      setIsSaving(false);
    }
  };

  const handleSaveLogLevel = async () => {
    setIsSaving(true);
    report("protokoll");
    try {
      const next = await updateSettings({
        file_log_level: fileLogLevelInput,
      });
      setSettings(next);
      setFileLogLevelInput(next.file_log_level);
      report("protokoll", "Detailgrad des Fehlerprotokolls gespeichert.");
    } catch (saveError) {
      report("protokoll",
        saveError instanceof Error
          ? saveError.message
          : "Der Detailgrad des Fehlerprotokolls konnte nicht gespeichert werden.",
        true,
      );
    } finally {
      setIsSaving(false);
    }
  };

  const handleAutomaticUpdateChecks = async (enabled: boolean) => {
    setIsSaving(true);
    report("updates");
    try {
      const next = await updateSettings({ automatic_update_checks: enabled });
      setSettings(next);
      window.dispatchEvent(
        new CustomEvent("face-manager:update-check-setting", { detail: enabled }),
      );
      report("updates",
        enabled
          ? "Die stündliche Update-Prüfung ist aktiviert."
          : "Die automatische Update-Prüfung ist deaktiviert.",
      );
    } catch (saveError) {
      report("updates", saveError instanceof Error ? saveError.message : "Update-Einstellung konnte nicht gespeichert werden.", true);
    } finally {
      setIsSaving(false);
    }
  };

  const handleManualUpdateCheck = async () => {
    setIsCheckingUpdates(true);
    report("updates");
    try {
      const update = await checkForUpdates(true);
      if (update.update_available) {
        window.dispatchEvent(
          new CustomEvent("face-manager:update-available", { detail: update }),
        );
      }
      report("updates",
        update.update_available
          ? `Face Manager ${update.latest_version} ist verfügbar. Der Hinweis erscheint oben in der Anwendung.`
          : `Face Manager ${update.current_version} ist aktuell.`,
      );
    } catch (checkError) {
      report("updates", checkError instanceof Error ? checkError.message : "Update-Prüfung fehlgeschlagen.", true);
    } finally {
      setIsCheckingUpdates(false);
    }
  };

  const handleExport = async () => {
    if (backupInFlight.current) return;
    backupInFlight.current = true;
    setIsExporting(true);
    setBackupFeedback({ text: "Die Sicherung wird vorbereitet …" });
    try {
      if (window.AndroidExport) {
        window.AndroidExport.requestDatabaseExport();
        setBackupFeedback({ text: "Wähle auf deinem Gerät den Speicherort für die Sicherung." });
        return;
      }
      saveDatabaseBlob(await exportDatabase(), "face-manager-database.sqlite");
      setBackupFeedback({ text: "Die Sicherung wurde erstellt." });
    } catch (exportError) {
      setBackupFeedback({
        error: true,
        text: exportError instanceof Error
          ? exportError.message
          : "Die Sicherung konnte nicht erstellt werden.",
      });
    } finally {
      backupInFlight.current = false;
      setIsExporting(false);
    }
  };

  const handleImportClick = () => {
    if (!backupInFlight.current) fileInputRef.current?.click();
  };

  const handleImportChange = async (
    event: React.ChangeEvent<HTMLInputElement>,
  ) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || backupInFlight.current) return;

    backupInFlight.current = true;
    setIsInspectingBackup(true);
    setBackupFeedback({ text: "Die ausgewählte Sicherung wird geprüft …" });
    try {
      const preview = await previewDatabaseRestore(file);
      setPendingRestore({ file, preview });
      setBackupFeedback(null);
    } catch (previewError) {
      setBackupFeedback({
        error: true,
        text: previewError instanceof Error ? previewError.message : "Die Sicherung konnte nicht geprüft werden.",
      });
    } finally {
      backupInFlight.current = false;
      setIsInspectingBackup(false);
    }
  };

  const confirmRestore = async () => {
    if (!pendingRestore || backupInFlight.current) return;
    const { file, preview } = pendingRestore;
    backupInFlight.current = true;
    setPendingRestore(null);
    setIsImporting(true);
    setBackupFeedback({ text: "Die Bibliothek wird wiederhergestellt …" });
    try {
      await importDatabase(file, preview.checksum);
      setRestoreBackupAvailable(true);
      try {
        const next = await fetchSettings();
        setSettings(next);
        setStrictnessInput(next.clustering_strictness.toFixed(2));
        setFilenameBlockSeparatorInput(next.filename_person_block_separator);
        setPersonJoinerInput(next.filename_person_joiner);
        setFileLogLevelInput(next.file_log_level);
        setBackupFeedback({ text: "Die Sicherung wurde wiederhergestellt." });
      } catch {
        setBackupFeedback({ text: "Die Sicherung wurde wiederhergestellt. Lade die Seite neu, um die Einstellungen anzuzeigen." });
      }
    } catch (importError) {
      setBackupFeedback({
        error: true,
        text: importError instanceof Error
          ? importError.message
          : "Die Sicherung konnte nicht wiederhergestellt werden.",
      });
    } finally {
      backupInFlight.current = false;
      setIsImporting(false);
    }
  };

  const handleDownloadRestoreBackup = async () => {
    if (backupInFlight.current) return;
    backupInFlight.current = true;
    setIsExporting(true);
    setBackupFeedback({ text: "Die vorherige Bibliothek wird vorbereitet …" });
    try {
      saveDatabaseBlob(await downloadRestoreBackup(), "face-manager-vorherige-bibliothek.sqlite");
      setBackupFeedback({ text: "Die Sicherung der vorherigen Bibliothek ist heruntergeladen." });
    } catch (downloadError) {
      setBackupFeedback({
        error: true,
        text: downloadError instanceof Error ? downloadError.message : "Die vorherige Bibliothek konnte nicht heruntergeladen werden.",
      });
    } finally {
      backupInFlight.current = false;
      setIsExporting(false);
    }
  };

  return (
    <div className="settings-page">
      <div className="settings-page__header" id="settings-start">
        <div>
          <h1 className="settings-page__title">Einstellungen</h1>
        </div>
      </div>

      {isLoading ? (
        <div className="settings-card" role="status">Lade Einstellungen…</div>
      ) : settingsLoadError ? (
        <div className="settings-card">
          <p role="alert">{settingsLoadError}</p>
          <button className="neon-card" type="button" onClick={() => setSettingsLoadAttempt((value) => value + 1)}>
            Erneut versuchen
          </button>
        </div>
      ) : (
        <div className="settings-layout">
          <nav className="settings-nav" aria-label="Bereiche">
            {SETTINGS_NAV_ITEMS.map((item) => (
              <button
                key={item.section}
                type="button"
                className="settings-nav__link"
                aria-current={activeSection === item.section ? "location" : undefined}
                onClick={() => {
                  if (activeSection === item.section) {
                    const group = document.getElementById(`settings-${item.section}`);
                    group?.scrollIntoView({ block: "start" });
                    group?.querySelector<HTMLElement>("h2")?.focus({ preventScroll: true });
                    return;
                  }
                  focusSection.current = item.section;
                  onNavigateSection(item.section);
                }}
              >
                {item.label}
              </button>
            ))}
          </nav>

          <div className="settings-sections">
            <section className="settings-group" id="settings-erkennung">
              <header className="settings-group__header">
                <h2 tabIndex={-1}>Erkennung und Gruppierung</h2>
                <p>Wie vorsichtig Gesichter automatisch zu Gruppen zusammengefasst werden.</p>
              </header>
              <div className="settings-group__cards">
            <section className="settings-card settings-card--clustering">
              <h2 className="settings-card__title">Wie vorsichtig soll gruppiert werden?</h2>
              <p className="settings-card__copy">
                Eine höhere Strenge vermeidet falsche Zuordnungen, kann aber mehr
                einzelne Gesichtsgruppen erzeugen. Eine niedrigere Strenge fasst mehr
                Gesichter automatisch. Im Zweifel empfehlen wir die automatische
                Optimierung.
              </p>

              <div className="settings-clustering-control">
                <label className="settings-field">
                  <span className="settings-clustering-control__label">
                    <strong>Strenge</strong>
                    <output>{Math.round(Number(strictnessInput) * 100)} %</output>
                  </span>
                  <input
                    className="settings-clustering-slider"
                    type="range"
                    min="0"
                    max="1"
                    step="0.01"
                    value={strictnessInput}
                    onChange={(event) => scheduleStrictnessSave(event.target.value)}
                    onPointerUp={flushStrictnessSave}
                    onBlur={flushStrictnessSave}
                    aria-label="Strenge der Gruppierung"
                    disabled={isAutoTuning}
                  />
                  <span className="settings-clustering-scale">
                    <span>Mehr zusammenfassen</span>
                    <span>Vorsichtiger trennen</span>
                  </span>
                </label>

                <div className="settings-clustering-actions">
                  <button
                    className="neon-card settings-auto-tune-button"
                    type="button"
                    onClick={handleAutoTuneThreshold}
                    disabled={isAutoTuning || isSavingClustering}
                    title="Passende Einstellung aus deinen bestätigten Personen und stabilen Gesichtsgruppen bestimmen"
                  >
                    {isAutoTuning ? "Optimiere…" : "Automatisch optimieren"}
                  </button>
                  <button
                    className="settings-text-button"
                    type="button"
                    onClick={handleResetStrictness}
                    disabled={isAutoTuning || isSavingClustering}
                  >
                    Standard wiederherstellen
                  </button>
                </div>

                <span
                  className={`settings-save-state settings-save-state--${clusteringSaveState}`}
                  role="status"
                >
                  {clusteringSaveState === "pending"
                    ? "Änderung wird gespeichert…"
                    : clusteringSaveState === "error"
                      ? "Speichern fehlgeschlagen"
                      : "Änderungen werden automatisch gespeichert"}
                </span>
              </div>

              {settings && (
                <details className="settings-clustering-details">
                  <summary>Was wird dabei angepasst?</summary>
                  <p>
                    Face Manager stimmt mehrere Sicherheitsregeln gemeinsam ab:
                    wie ähnlich Gesichter sein müssen, wann eine Gesichtsgruppe zu uneinheitlich
                    ist und wann eine vorhandene Person vorgeschlagen werden darf.
                    Unsichere Gesichter bleiben weiterhin nicht zugewiesen.
                  </p>
                  <div className="settings-clustering-details__values">
                    <span>Ähnlichkeit {settings.clustering_profile.neighbor_threshold.toFixed(2)}</span>
                    <span>Einheitlichkeit der Gruppe {settings.clustering_profile.cohesion_threshold.toFixed(2)}</span>
                    <span>Zuordnung zu Personen {settings.clustering_profile.person_anchor_threshold.toFixed(2)}</span>
                    <span>{Math.round(settings.clustering_profile.cluster_support_ratio * 100)} % erforderliche Übereinstimmung</span>
                  </div>
                </details>
              )}

              <div className="settings-clustering-recluster">
                <div>
                  <h3>Bestehende Gesichtsgruppen neu ordnen</h3>
                  <p>
                    Die Einstellung oben gilt sofort für neue Gesichter. Bereits
                    vorhandene Gruppen ändern sich erst bei einer neuen Sortierung.
                  </p>
                </div>
                <button
                  className="neon-card"
                  type="button"
                  onClick={handleRecluster}
                  disabled={isReclustering || isAutoTuning || isSavingClustering}
                >
                  {isReclustering ? "Wird gestartet…" : "Gesichtsgruppen jetzt neu ordnen"}
                </button>
              </div>
              <p className="settings-clustering-recluster__note">
                Dabei werden nicht zugewiesene Gesichter und die Untergruppen
                bestätigter Personen geprüft. Bestätigte Personen bleiben geschützt.
                Nach manuellen Zuordnungen läuft diese Prüfung ansonsten automatisch,
                sobald Face Manager gerade nichts anderes verarbeitet.
              </p>
              {sectionFeedback("erkennung")}
            </section>
              </div>
            </section>

            <section className="settings-group" id="settings-dateinamen">
              <header className="settings-group__header">
                <h2 tabIndex={-1}>Dateinamen</h2>
                <p>Lege fest, wie zugeordnete Personennamen in den Dateinamenvorschlägen erscheinen.</p>
              </header>
              <div className="settings-group__cards">
            <section className="settings-card">
              <h2 className="settings-card__title">Benennungsschema</h2>
              <p className="settings-card__copy">
                Speichere die Regel für künftige Vorschläge. Welche Fotos du umbenennst,
                entscheidest du anschließend in der Dateinamen-Ansicht.
              </p>

              <div className="filename-rule-grid">
                <section className="filename-rule-card" aria-labelledby="filename-rule-start">
                  <header className="filename-rule-card__header">
                    <div>
                      <h3 id="filename-rule-start">Zwischen Dateiname und Namen</h3>
                      <p>Wähle die Trennung zwischen dem Dateinamen und der ersten Person.</p>
                    </div>
                  </header>
                  <div className="filename-rule-card__formula" aria-label="Position der Einstellung">
                    <span>Originalname</span>
                    <strong title="Verwendete Trennung">
                      {visibleSeparator(filenameBlockSeparatorInput)}
                    </strong>
                    <span>Kai</span>
                  </div>
                  <div className="settings-format-presets" aria-label="Beginn des Personen-Anhangs">
                    {FILENAME_TO_NAMES_SEPARATOR_PRESETS.map((preset) => (
                      <button
                        key={`${preset.label}-${preset.value}`}
                        className={
                          filenameBlockSeparatorInput === preset.value
                            ? "settings-format-preset settings-format-preset--active"
                            : "settings-format-preset"
                        }
                        type="button"
                        disabled={isSaving}
                        aria-pressed={filenameBlockSeparatorInput === preset.value}
                        onClick={() => {
                          setFilenameBlockSeparatorInput(preset.value);
                          setFilenameFeedback(null);
                        }}
                      >
                        <strong>{preset.label}</strong>
                        <code>{`Datei${preset.value}Kai.jpg`}</code>
                      </button>
                    ))}
                  </div>
                  <label className="settings-field filename-rule-card__custom">
                    <span>Eigenes Zeichen oder eigener Text</span>
                    <input
                      className="settings-number-input settings-text-input"
                      type="text"
                      value={filenameBlockSeparatorInput}
                      disabled={isSaving}
                      aria-invalid={invalidBlockSeparator}
                      aria-describedby={invalidBlockSeparator ? "filename-rule-start-error" : undefined}
                      onChange={(event) => {
                        setFilenameBlockSeparatorInput(event.target.value);
                        setFilenameFeedback(null);
                      }}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" && !event.nativeEvent.isComposing && !isSaving) {
                          event.preventDefault();
                          void handleSaveSuffixFormat();
                        }
                      }}
                      placeholder="z. B.  - "
                    />
                    {invalidBlockSeparator && (
                      <small id="filename-rule-start-error" className="settings-save-state--error">
                        {missingBlockSeparator
                          ? "Wähle mindestens ein Trennzeichen, etwa ein Leerzeichen."
                          : "Verwende gültige Zeichen für einen Dateinamen, etwa einen Bindestrich."}
                      </small>
                    )}
                  </label>
                </section>

                <section className="filename-rule-card" aria-labelledby="filename-rule-people">
                  <header className="filename-rule-card__header">
                    <div>
                      <h3 id="filename-rule-people">Mehrere Personen verbinden</h3>
                      <p>Diese Verbindung steht zwischen den Namen, wenn mehrere Personen zugeordnet sind.</p>
                    </div>
                  </header>
                  <div className="filename-rule-card__formula" aria-label="Position der Einstellung">
                    <span>Kai</span>
                    <strong title="Verwendete Verbindung">
                      {visibleSeparator(personJoinerInput)}
                    </strong>
                    <span>Regina</span>
                  </div>
                  <div className="settings-format-presets" aria-label="Verbindung zwischen Personennamen">
                    {PERSON_NAME_JOINER_PRESETS.map((preset) => (
                      <button
                        key={`${preset.label}-${preset.value}`}
                        className={
                          personJoinerInput === preset.value
                            ? "settings-format-preset settings-format-preset--active"
                            : "settings-format-preset"
                        }
                        type="button"
                        disabled={isSaving}
                        aria-pressed={personJoinerInput === preset.value}
                        onClick={() => {
                          setPersonJoinerInput(preset.value);
                          setFilenameFeedback(null);
                        }}
                      >
                        <strong>{preset.label}</strong>
                        <code>{SAMPLE_PERSON_NAMES.join(preset.value)}</code>
                      </button>
                    ))}
                  </div>
                  <label className="settings-field filename-rule-card__custom">
                    <span>Eigenes Zeichen oder eigener Text</span>
                    <input
                      className="settings-number-input settings-text-input"
                      type="text"
                      value={personJoinerInput}
                      disabled={isSaving}
                      aria-invalid={invalidPersonJoiner}
                      aria-describedby={invalidPersonJoiner ? "filename-rule-people-error" : undefined}
                      onChange={(event) => {
                        setPersonJoinerInput(event.target.value);
                        setFilenameFeedback(null);
                      }}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" && !event.nativeEvent.isComposing && !isSaving) {
                          event.preventDefault();
                          void handleSaveSuffixFormat();
                        }
                      }}
                      placeholder="z. B. ,  oder und"
                    />
                    {invalidPersonJoiner && <small id="filename-rule-people-error" className="settings-save-state--error">
                      Wähle ein erlaubtes Zeichen oder Wort, etwa ein Komma oder „und“.
                    </small>}
                  </label>
                </section>
              </div>

              <div className="settings-format-preview filename-format-result">
                <span className="settings-format-preview__label">Vorgeschlagener Dateiname</span>
                <code>{suffixPreview}</code>
                <small>
                  Vorschau für den ursprünglichen Namen <code>{SAMPLE_BASENAME}</code>
                </small>
              </div>

              <div className="settings-actions">
                <button
                  className="neon-card"
                  onClick={handleSaveSuffixFormat}
                  disabled={!canSaveFilenameFormat}
                >
                  {isSaving ? "Speichern…" : "Dateinamen-Regeln speichern"}
                </button>
                <button
                  className="neon-card"
                  disabled={isSaving}
                  onClick={() => {
                    if (!settings) return;
                    setFilenameBlockSeparatorInput(
                      settings.filename_person_block_separator_default,
                    );
                    setPersonJoinerInput(settings.filename_person_joiner_default);
                    setFilenameFeedback(null);
                  }}
                >
                  Standardwerte in der Vorschau
                </button>
              </div>
              {filenameFormatChanged && !filenameFeedback && (
                <small className="settings-save-state settings-save-state--pending" role="status">Ungespeicherte Änderung</small>
              )}
              {filenameFeedback && (
                <p
                  className={filenameFeedback.error ? "settings-feedback settings-feedback--error" : "settings-feedback"}
                  role={filenameFeedback.error ? "alert" : "status"}
                >
                  {filenameFeedback.text}
                </p>
              )}
              <a className="rename-settings-link" href="#/dateinamen">Zu den Dateinamenvorschlägen <span aria-hidden="true">→</span></a>
            </section>
              </div>
            </section>

            <section className="settings-group" id="settings-darstellung">
              <header className="settings-group__header">
                <h2 tabIndex={-1}>Darstellung</h2>
                <p>Erscheinungsbild der Anwendung.</p>
              </header>
              <div className="settings-group__cards">
            <section className="settings-card">
              <h2 className="settings-card__title">Darstellung auswählen</h2>
              <p className="settings-card__copy">
                Wähle das Erscheinungsbild. Im Modus „System“ übernimmt Face
                Manager automatisch die Einstellung des Betriebssystems. Deine
                Auswahl bleibt gespeichert.
              </p>

              <div
                className="settings-theme-options"
                role="radiogroup"
                aria-label="Darstellung"
              >
                {THEME_OPTIONS.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    role="radio"
                    aria-checked={themeMode === option.value}
                    className={
                      themeMode === option.value
                        ? "settings-theme-option settings-theme-option--active"
                        : "settings-theme-option"
                    }
                    onClick={() => setThemeMode(option.value)}
                  >
                    <strong>{option.label}</strong>
                    <span>{option.description}</span>
                  </button>
                ))}
              </div>

              <div className="settings-actions">
                <button
                  className="settings-text-button"
                  type="button"
                  onClick={() => setThemeMode("system")}
                  disabled={themeMode === "system"}
                >
                  Auf System-Standard zurücksetzen
                </button>
              </div>
            </section>
              </div>
            </section>

            <section className="settings-group" id="settings-updates">
              <header className="settings-group__header">
                <h2 tabIndex={-1}>Updates</h2>
                <p>Neue veröffentlichte Versionen von Face Manager.</p>
              </header>
              <div className="settings-group__cards">
                <section className="settings-card">
                  <h2 className="settings-card__title">Automatisch nach Updates suchen</h2>
                  <p className="settings-card__copy">
                    Face Manager prüft höchstens einmal pro Stunde auf GitHub, ob
                    eine neue Version verfügbar ist. Es werden keine Bild- oder
                    Personendaten übertragen.
                  </p>
                  <label className="settings-toggle-row">
                    <input
                      type="checkbox"
                      checked={settings?.automatic_update_checks ?? true}
                      onChange={(event) => void handleAutomaticUpdateChecks(event.target.checked)}
                      disabled={isSaving}
                    />
                    <span>
                      <strong>Stündliche Prüfung aktivieren</strong>
                      <small>Bei einer neuen Version erscheint automatisch ein Hinweis.</small>
                    </span>
                  </label>
                  <div className="settings-actions">
                    <button className="neon-card" type="button" onClick={handleManualUpdateCheck} disabled={isCheckingUpdates}>
                      {isCheckingUpdates ? "Prüfe…" : "Jetzt nach Updates suchen"}
                    </button>
                  </div>
                  {sectionFeedback("updates")}
                </section>
              </div>
            </section>

            <section className="settings-group" id="settings-daten">
              <header className="settings-group__header">
                <h2 tabIndex={-1}>Daten und Wartung</h2>
                <p>Datensicherung und hilfreiche Informationen für die Fehlersuche.</p>
              </header>
              <div className="settings-group__cards">
            <section className="settings-card">
              <h2 className="settings-card__title">Daten sichern und wiederherstellen</h2>
              <p className="settings-card__copy">
                Erstelle eine Sicherung deiner Face-Manager-Daten oder stelle eine
                zuvor gespeicherte Sicherung wieder her.
              </p>

              <details className="settings-meta settings-meta--details">
                <summary>Speicherort der Bibliothek</summary>
                <code>{settings?.database_path}</code>
              </details>

              <div className="settings-actions">
                <button
                  className="neon-card"
                  type="button"
                  onClick={handleExport}
                  disabled={isExporting || isImporting || isInspectingBackup}
                >
                  {isExporting ? "Sicherung wird erstellt…" : "Sicherung erstellen"}
                </button>
                <button
                  className="neon-card"
                  type="button"
                  onClick={handleImportClick}
                  disabled={isExporting || isImporting || isInspectingBackup}
                >
                  {isImporting ? "Wird wiederhergestellt…" : "Sicherung wiederherstellen"}
                </button>
                {restoreBackupAvailable && <button className="neon-card" type="button"
                  onClick={handleDownloadRestoreBackup} disabled={isExporting || isImporting || isInspectingBackup}>
                  Vorherige Bibliothek herunterladen
                </button>}
              </div>
              {backupFeedback && (
                <p
                  className={backupFeedback.error ? "settings-feedback settings-feedback--error" : "settings-feedback"}
                  role={backupFeedback.error ? "alert" : "status"}
                >
                  {backupFeedback.text}
                </p>
              )}

              <input
                ref={fileInputRef}
                type="file"
                accept=".sqlite,.db,application/octet-stream"
                hidden
                onChange={handleImportChange}
              />
              {active && pendingRestore && <RestoreConfirmation file={pendingRestore.file} preview={pendingRestore.preview}
                onClose={() => setPendingRestore(null)} onConfirm={() => void confirmRestore()} />}
            </section>

            <ArchiveRepair active={active} />

            <section className="settings-card">
              <h2 className="settings-card__title">Fehlerprotokoll</h2>
              <p className="settings-card__copy">
                Lege fest, wie ausführlich Face Manager technische Ereignisse
                protokolliert. Für die normale Nutzung reicht „Nur Fehler“. Bei
                Problemen kann ein ausführlicherer Wert die Fehlersuche erleichtern.
              </p>

              <label className="settings-field">
                <span>Detailgrad</span>
                <select
                  className="app-select settings-select-input"
                  value={fileLogLevelInput}
                  onChange={(event) =>
                    setFileLogLevelInput(
                      event.target.value as AppSettings["file_log_level"],
                    )
                  }
                >
                  {LOG_LEVEL_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>

              <div className="settings-format-help">
                <span>
                  {
                    LOG_LEVEL_OPTIONS.find((option) => option.value === fileLogLevelInput)
                      ?.description
                  }
                </span>
              </div>

              <details className="settings-meta settings-meta--details">
                <summary>Speicherort des Fehlerprotokolls</summary>
                <code>{settings?.error_log_path}</code>
              </details>

              <div className="settings-actions">
                <button
                  className="neon-card"
                  onClick={handleSaveLogLevel}
                  disabled={isSaving}
                >
                  {isSaving ? "Speichern…" : "Detailgrad speichern"}
                </button>
                <button
                  className="neon-card"
                  onClick={() => {
                    if (!settings) return;
                    setFileLogLevelInput(settings.file_log_level_default);
                  }}
                >
                  Auf Standard zurücksetzen
                </button>
              </div>
              {sectionFeedback("protokoll")}
            </section>
            {settings && <PreviewCacheSettings settings={settings} />}
            <SharedLibrarySettings controller={sharedLibrary} />
            <ImagePathCleanupCard active={active} />
              </div>
            </section>
          </div>
        </div>
      )}
    </div>
  );
};

export default SettingsPage;
