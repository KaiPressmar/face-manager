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
  { label: "Schrägstrich", value: " / " },
  { label: "Und", value: " und " },
  { label: "Plus", value: " + " },
];
const FILENAME_TO_NAMES_SEPARATOR_PRESETS = [
  { label: "Leerzeichen", value: " " },
  { label: "Bindestrich", value: " - " },
  { label: "Unterstrich", value: "_" },
  { label: "In Klammern", value: " (" },
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
  const suffix = `${blockSeparator}${joinedNames}`;
  const closing = blockSeparator === " (" ? ")" : "";
  return `${stem}${suffix}${closing}${extension}`;
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

const SettingsPage: React.FC<{
  activeSection?: SettingsSection;
  onNavigateSection: (section: SettingsSection) => void;
}> = ({ activeSection, onNavigateSection }) => {
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
  const [isCheckingUpdates, setIsCheckingUpdates] = useState(false);
  const [pathCleanup, setPathCleanup] = useState<ImagePathCleanupStatus | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const strictnessSaveTimerRef = useRef<number | null>(null);
  const strictnessRevisionRef = useRef(0);
  const pendingStrictnessRef = useRef<{ value: number; revision: number } | null>(null);
  const strictnessSaveInFlightRef = useRef(false);
  const queuedStrictnessRef = useRef<{ value: number; revision: number } | null>(null);
  const suffixPreview = buildSuffixFormatPreview(
    filenameBlockSeparatorInput,
    personJoinerInput,
  );
  const joinerPreviewText = SAMPLE_PERSON_NAMES.join(personJoinerInput);

  useEffect(() => {
    let cancelled = false;

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
        setError(
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
  }, []);

  useEffect(() => {
    let cancelled = false;
    void fetchImagePathCleanup()
      .then((next) => {
        if (!cancelled) setPathCleanup(next);
      })
      .catch(() => {
        // Maintenance state is supplementary; the settings remain usable.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const pathCleanupIsActive =
    pathCleanup?.status === "queued" || pathCleanup?.status === "running";

  useEffect(() => {
    if (!pathCleanupIsActive) return;
    let cancelled = false;
    const timer = window.setInterval(() => {
      void fetchImagePathCleanup()
        .then((next) => {
          if (!cancelled) setPathCleanup(next);
        })
        .catch(() => undefined);
    }, 1000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [pathCleanupIsActive]);

  useEffect(() => {
    if (isLoading) return;
    const animationFrame = window.requestAnimationFrame(() => {
      const targetId = activeSection ? `settings-${activeSection}` : "settings-start";
      document.getElementById(targetId)?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
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
        setError(
          saveError instanceof Error
            ? saveError.message
            : "Die Einstellung für die Gruppierung konnte nicht gespeichert werden.",
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
    setError(null);
    setMessage(null);
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
    setError(null);
    setMessage(null);
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
    setError(null);
    setMessage(null);
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
      setMessage(
        `Die passende Einstellung wurde aus ${result.person_count} bestätigten Personen ermittelt und gespeichert. ` +
          "Mit „Gesichtsgruppen jetzt neu ordnen“ wendest du sie auch auf vorhandene Gesichter an.",
      );
    } catch (tuneError) {
      setError(
        tuneError instanceof Error
          ? tuneError.message
          : "Die Einstellung für die Gruppierung konnte nicht automatisch optimiert werden.",
      );
    } finally {
      setIsAutoTuning(false);
    }
  };

  const handleRecluster = async () => {
    setIsReclustering(true);
    setError(null);
    setMessage(null);
    try {
      const { scheduled, status } = await reclusterAllFaces();
      setMessage(
        !scheduled
          ? "Es sind keine Gesichter vorhanden, die neu geordnet werden könnten."
          : status === "queued"
            ? "Das Neu-Ordnen ist eingeplant und startet automatisch, sobald der laufende Import abgeschlossen ist."
            : "Die Gesichtsgruppen werden jetzt neu geordnet.",
      );
    } catch (reclusterError) {
      setError(
        reclusterError instanceof Error
          ? reclusterError.message
          : "Die Gesichtsgruppen konnten nicht neu geordnet werden.",
      );
    } finally {
      setIsReclustering(false);
    }
  };

  const handleSaveSuffixFormat = async () => {
    setIsSaving(true);
    setError(null);
    setMessage(null);
    try {
      const next = await updateSettings({
        filename_person_block_separator: filenameBlockSeparatorInput,
        filename_person_joiner: personJoinerInput,
      });
      setSettings(next);
      setFilenameBlockSeparatorInput(next.filename_person_block_separator);
      setPersonJoinerInput(next.filename_person_joiner);
      setMessage("Einstellungen für Personennamen im Dateinamen gespeichert.");
    } catch (saveError) {
      setError(
        saveError instanceof Error
          ? saveError.message
          : "Die Dateinamen-Einstellungen konnten nicht gespeichert werden.",
      );
    } finally {
      setIsSaving(false);
    }
  };

  const handleSaveLogLevel = async () => {
    setIsSaving(true);
    setError(null);
    setMessage(null);
    try {
      const next = await updateSettings({
        file_log_level: fileLogLevelInput,
      });
      setSettings(next);
      setFileLogLevelInput(next.file_log_level);
      setMessage("Detailgrad des Fehlerprotokolls gespeichert.");
    } catch (saveError) {
      setError(
        saveError instanceof Error
          ? saveError.message
          : "Der Detailgrad des Fehlerprotokolls konnte nicht gespeichert werden.",
      );
    } finally {
      setIsSaving(false);
    }
  };

  const handleAutomaticUpdateChecks = async (enabled: boolean) => {
    setIsSaving(true);
    setError(null);
    setMessage(null);
    try {
      const next = await updateSettings({ automatic_update_checks: enabled });
      setSettings(next);
      window.dispatchEvent(
        new CustomEvent("face-manager:update-check-setting", { detail: enabled }),
      );
      setMessage(
        enabled
          ? "Die stündliche Update-Prüfung ist aktiviert."
          : "Die automatische Update-Prüfung ist deaktiviert.",
      );
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Update-Einstellung konnte nicht gespeichert werden.");
    } finally {
      setIsSaving(false);
    }
  };

  const handleManualUpdateCheck = async () => {
    setIsCheckingUpdates(true);
    setError(null);
    setMessage(null);
    try {
      const update = await checkForUpdates(true);
      if (update.update_available) {
        window.dispatchEvent(
          new CustomEvent("face-manager:update-available", { detail: update }),
        );
      }
      setMessage(
        update.update_available
          ? `Face Manager ${update.latest_version} ist verfügbar. Der Hinweis erscheint oben in der Anwendung.`
          : `Face Manager ${update.current_version} ist aktuell.`,
      );
    } catch (checkError) {
      setError(checkError instanceof Error ? checkError.message : "Update-Prüfung fehlgeschlagen.");
    } finally {
      setIsCheckingUpdates(false);
    }
  };

  const handleExport = async () => {
    setIsExporting(true);
    setError(null);
    setMessage(null);
    try {
      const blob = await exportDatabase();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "face-manager-database.sqlite";
      link.click();
      URL.revokeObjectURL(url);
      setMessage("Die Sicherung wurde erstellt.");
    } catch (exportError) {
      setError(
        exportError instanceof Error
          ? exportError.message
          : "Die Sicherung konnte nicht erstellt werden.",
      );
    } finally {
      setIsExporting(false);
    }
  };

  const handleImportClick = () => {
    fileInputRef.current?.click();
  };

  const handleImportChange = async (
    event: React.ChangeEvent<HTMLInputElement>,
  ) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;

    setIsImporting(true);
    setError(null);
    setMessage(null);
    try {
      await importDatabase(file);
      const next = await fetchSettings();
      setSettings(next);
      setStrictnessInput(next.clustering_strictness.toFixed(2));
      setFilenameBlockSeparatorInput(next.filename_person_block_separator);
      setPersonJoinerInput(next.filename_person_joiner);
      setFileLogLevelInput(next.file_log_level);
      setMessage("Die Sicherung wurde wiederhergestellt.");
    } catch (importError) {
      setError(
        importError instanceof Error
          ? importError.message
          : "Die Sicherung konnte nicht wiederhergestellt werden.",
      );
    } finally {
      setIsImporting(false);
    }
  };

  const handleImagePathCleanup = async () => {
    setError(null);
    setMessage(null);
    try {
      const next = await startImagePathCleanup();
      setPathCleanup(next);
    } catch (cleanupError) {
      setError(
        cleanupError instanceof Error
          ? cleanupError.message
          : "Die Pfadprüfung konnte nicht gestartet werden.",
      );
    }
  };

  const cleanupSummary = pathCleanup
    ? pathCleanup.status === "queued"
      ? "Die Prüfung wartet, bis keine wichtigeren Hintergrundaufgaben laufen."
      : pathCleanup.status === "running"
        ? `${pathCleanup.scanned_paths.toLocaleString("de-DE")} Speicherorte geprüft…`
        : pathCleanup.status === "completed"
          ? `${pathCleanup.scanned_paths.toLocaleString("de-DE")} Speicherorte geprüft, ${pathCleanup.removed_paths.toLocaleString("de-DE")} ungültige Speicherorte und ${pathCleanup.removed_images.toLocaleString("de-DE")} nicht mehr verfügbare Bilder entfernt.`
          : pathCleanup.status === "failed"
            ? "Die letzte Pfadprüfung konnte nicht abgeschlossen werden."
            : "Noch keine Pfadprüfung in dieser Sitzung ausgeführt."
    : "Der Status der letzten Prüfung wird geladen…";

  return (
    <div className="settings-page">
      <div className="settings-page__header" id="settings-start">
        <div>
          <div className="settings-page__eyebrow">Face Manager</div>
          <h1 className="settings-page__title">Einstellungen</h1>
          <p className="settings-page__copy">
            Passe Erkennung, Dateinamen und Darstellung an oder sichere deine Daten.
          </p>
        </div>
      </div>

      {isLoading ? (
        <div className="settings-card">Lade Einstellungen…</div>
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
                  onNavigateSection(item.section);
                  document.getElementById(`settings-${item.section}`)?.scrollIntoView({
                    behavior: "smooth",
                    block: "start",
                  });
                }}
              >
                {item.label}
              </button>
            ))}
          </nav>

          <div className="settings-sections">
            <section className="settings-group" id="settings-erkennung">
              <header className="settings-group__header">
                <h2>Erkennung und Gruppierung</h2>
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
            </section>
              </div>
            </section>

            <section className="settings-group" id="settings-dateinamen">
              <header className="settings-group__header">
                <h2>Dateinamen</h2>
                <p>Wie erkannte Personennamen an Dateinamen angehängt werden.</p>
              </header>
              <div className="settings-group__cards">
            <section className="settings-card">
              <h2 className="settings-card__title">Benennungsschema</h2>
              <p className="settings-card__copy">
                Der ursprüngliche Dateiname bleibt erhalten. Die folgenden zwei
                Regeln bestimmen nur, wie die erkannten Personen angehängt werden.
              </p>

              <div className="filename-rule-grid">
                <section className="filename-rule-card" aria-labelledby="filename-rule-start">
                  <header className="filename-rule-card__header">
                    <span aria-hidden="true">1</span>
                    <div>
                      <h3 id="filename-rule-start">Beginn des Personen-Anhangs</h3>
                      <p>Dieses Zeichen steht einmal zwischen dem bisherigen Dateinamen und dem ersten Namen.</p>
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
                        onClick={() => setFilenameBlockSeparatorInput(preset.value)}
                      >
                        <strong>{preset.label}</strong>
                        <code>{`Datei${preset.value}Kai${preset.value === " (" ? ")" : ""}.jpg`}</code>
                      </button>
                    ))}
                  </div>
                  <label className="settings-field filename-rule-card__custom">
                    <span>Eigenes Zeichen oder eigener Text</span>
                    <input
                      className="settings-number-input settings-text-input"
                      type="text"
                      value={filenameBlockSeparatorInput}
                      onChange={(event) => setFilenameBlockSeparatorInput(event.target.value)}
                      placeholder="z. B.  - "
                    />
                  </label>
                </section>

                <section className="filename-rule-card" aria-labelledby="filename-rule-people">
                  <header className="filename-rule-card__header">
                    <span aria-hidden="true">2</span>
                    <div>
                      <h3 id="filename-rule-people">Mehrere Personen verbinden</h3>
                      <p>Dieses Zeichen wird nur zwischen zwei oder mehr erkannten Personennamen verwendet.</p>
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
                        onClick={() => setPersonJoinerInput(preset.value)}
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
                      onChange={(event) => setPersonJoinerInput(event.target.value)}
                      placeholder="z. B. ,  oder  / "
                    />
                    <small>Nur die Namen: {joinerPreviewText}</small>
                  </label>
                </section>
              </div>

              <div className="settings-format-preview filename-format-result">
                <span className="settings-format-preview__label">Ergebnis mit beiden Regeln</span>
                <code>{suffixPreview}</code>
                <small>
                  Vorschau für den ursprünglichen Namen <code>{SAMPLE_BASENAME}</code>
                </small>
              </div>

              <div className="settings-actions">
                <button
                  className="neon-card"
                  onClick={handleSaveSuffixFormat}
                  disabled={isSaving}
                >
                  {isSaving ? "Speichern…" : "Dateinamen-Regeln speichern"}
                </button>
                <button
                  className="neon-card"
                  onClick={() => {
                    if (!settings) return;
                    setFilenameBlockSeparatorInput(
                      settings.filename_person_block_separator_default,
                    );
                    setPersonJoinerInput(settings.filename_person_joiner_default);
                  }}
                >
                  Auf Standard zurücksetzen
                </button>
              </div>
            </section>
              </div>
            </section>

            <section className="settings-group" id="settings-darstellung">
              <header className="settings-group__header">
                <h2>Darstellung</h2>
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
                <h2>Updates</h2>
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
                </section>
              </div>
            </section>

            <section className="settings-group" id="settings-daten">
              <header className="settings-group__header">
                <h2>Daten und Wartung</h2>
                <p>Datensicherung und hilfreiche Informationen für die Fehlersuche.</p>
              </header>
              <div className="settings-group__cards">
            <section className="settings-card">
              <h2 className="settings-card__title">Daten sichern und wiederherstellen</h2>
              <p className="settings-card__copy">
                Erstelle eine Sicherung deiner Face-Manager-Daten oder stelle eine
                zuvor gespeicherte Sicherung wieder her.
              </p>

              <div className="settings-meta">
                <span>Speicherort der Face-Manager-Daten</span>
                <code>{settings?.database_path}</code>
              </div>

              <div className="settings-actions">
                <button
                  className="neon-card"
                  onClick={handleExport}
                  disabled={isExporting}
                >
                  {isExporting ? "Sicherung wird erstellt…" : "Sicherung erstellen"}
                </button>
                <button
                  className="neon-card"
                  onClick={handleImportClick}
                  disabled={isImporting}
                >
                  {isImporting ? "Wird wiederhergestellt…" : "Sicherung wiederherstellen"}
                </button>
              </div>

              <input
                ref={fileInputRef}
                type="file"
                accept=".sqlite,.db,application/octet-stream"
                hidden
                onChange={handleImportChange}
              />
            </section>

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

              <div className="settings-meta">
                <span>Speicherort des Fehlerprotokolls</span>
                <code>{settings?.error_log_path}</code>
              </div>

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
            </section>
            <section className="settings-card">
              <h2 className="settings-card__title">Bildspeicherorte prüfen</h2>
              <p className="settings-card__copy">
                Prüft alle gespeicherten Bildpfade und entfernt Einträge für Dateien,
                die nicht mehr vorhanden sind. Die automatische Prüfung läuft mit
                niedriger Priorität, wenn Face Manager gerade nichts Wichtigeres tut.
              </p>
              <div className="settings-maintenance-status" aria-live="polite">
                {cleanupSummary}
              </div>
              <div className="settings-actions">
                <button
                  className="neon-card"
                  type="button"
                  onClick={() => void handleImagePathCleanup()}
                  disabled={pathCleanupIsActive}
                >
                  {pathCleanupIsActive ? "Pfadprüfung läuft…" : "Jetzt Pfade prüfen"}
                </button>
              </div>
            </section>
              </div>
            </section>
          </div>
        </div>
      )}

      {(message || error) && (
        <div
          className={
            error
              ? "settings-feedback settings-feedback--error"
              : "settings-feedback"
          }
        >
          {error || message}
        </div>
      )}

    </div>
  );
};

export default SettingsPage;
