import React, { useEffect, useRef, useState } from "react";
import {
  AppSettings,
  exportDatabase,
  fetchSettings,
  importDatabase,
  updateSettings,
} from "../../utils/api";
import FolderPickerModal from "../shared/FolderPickerModal";

const PERSON_NAME_JOINER_PRESETS = [
  { label: "Komma", value: ", " },
  { label: "Slash", value: " / " },
  { label: "Und", value: " und " },
  { label: "Plus", value: " + " },
];
const FILENAME_TO_NAMES_SEPARATOR_PRESETS = [
  { label: "Leerzeichen", value: " " },
  { label: "Bindestrich", value: " - " },
  { label: "Unterstrich", value: "_" },
  { label: "Klammer auf", value: " (" },
];

const SAMPLE_BASENAME = "img_abc_heute hier morgen da.jpg";
const SAMPLE_PERSON_NAMES = ["Kai", "Regina"];
const LOG_LEVEL_OPTIONS = [
  {
    label: "Nur Fehler",
    value: "ERROR",
    description: "Schreibt nur Fehler und Abstürze in die lokale Logdatei.",
  },
  {
    label: "Warnungen",
    value: "WARNING",
    description: "Erfasst zusätzlich auffällige, aber noch nicht fatale Probleme.",
  },
  {
    label: "Informationen",
    value: "INFO",
    description: "Hilfreich, wenn im Windows-Build Abläufe nachvollziehbar sein sollen.",
  },
  {
    label: "Debug",
    value: "DEBUG",
    description: "Maximale Detailtiefe für die Fehlersuche in problematischen Deployments.",
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

const SettingsPage: React.FC = () => {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [thresholdInput, setThresholdInput] = useState("0.50");
  const [filenameBlockSeparatorInput, setFilenameBlockSeparatorInput] =
    useState(" ");
  const [personJoinerInput, setPersonJoinerInput] = useState(", ");
  const [fileLogLevelInput, setFileLogLevelInput] =
    useState<AppSettings["file_log_level"]>("ERROR");
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [isImporting, setIsImporting] = useState(false);
  const [showFolderPicker, setShowFolderPicker] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
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
        setThresholdInput(data.cluster_distance_threshold.toFixed(2));
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
    };
  }, []);

  const handleSaveThreshold = async () => {
    const value = Number.parseFloat(thresholdInput);
    if (Number.isNaN(value) || value < 0 || value > 1) {
      setError(
        "Der Clustering-Schwellenwert muss zwischen 0,00 und 1,00 liegen.",
      );
      return;
    }

    setIsSaving(true);
    setError(null);
    setMessage(null);
    try {
      const next = await updateSettings({
        cluster_distance_threshold: value,
      });
      setSettings(next);
      setThresholdInput(next.cluster_distance_threshold.toFixed(2));
      setMessage("Clustering threshold saved.");
    } catch (saveError) {
      setError(
        saveError instanceof Error
          ? saveError.message
          : "Die Einstellungen konnten nicht gespeichert werden.",
      );
    } finally {
      setIsSaving(false);
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
      setMessage("Einstellungen fuer Personennamen im Dateinamen gespeichert.");
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
      setMessage("Log-Level fuer die lokale Datei gespeichert.");
    } catch (saveError) {
      setError(
        saveError instanceof Error
          ? saveError.message
          : "Das Log-Level konnte nicht gespeichert werden.",
      );
    } finally {
      setIsSaving(false);
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
      setMessage("Die Datenbank wurde erfolgreich exportiert.");
    } catch (exportError) {
      setError(
        exportError instanceof Error
          ? exportError.message
          : "Die Datenbank konnte nicht exportiert werden.",
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
      setThresholdInput(next.cluster_distance_threshold.toFixed(2));
      setFilenameBlockSeparatorInput(next.filename_person_block_separator);
      setPersonJoinerInput(next.filename_person_joiner);
      setFileLogLevelInput(next.file_log_level);
      setMessage("Die Datenbank wurde erfolgreich importiert.");
    } catch (importError) {
      setError(
        importError instanceof Error
          ? importError.message
          : "Die Datenbank konnte nicht importiert werden.",
      );
    } finally {
      setIsImporting(false);
    }
  };

  return (
    <div className="settings-page">
      <div className="settings-page__header">
        <div>
          <div className="settings-page__eyebrow">Einstellungen</div>
          <h1 className="settings-page__title">Datenbank und Clustering</h1>
          <p className="settings-page__copy">
            Verwalten Sie die Datenbankdatei und passen Sie an, wie aggressiv
            neue Gesichter in bestehende Cluster gruppiert werden.
          </p>
        </div>
      </div>

      {isLoading ? (
        <div className="settings-card">Lade Einstellungen…</div>
      ) : (
        <div className="settings-grid">
          <section className="settings-card">
            <div className="settings-card__kicker">Clustering</div>
            <h2 className="settings-card__title">Abstandsschwelle</h2>
            <p className="settings-card__copy">
              Niedrigere Werte sind strenger und erzeugen mehr separate Cluster.
              Höhere Werte führen dazu, dass Gesichter leichter zusammengeführt
              werden.
            </p>

            <label className="settings-field">
              <span>Schwellenwert</span>
              <div className="settings-threshold-row">
                <input
                  type="range"
                  min="0"
                  max="1"
                  step="0.01"
                  value={thresholdInput}
                  onChange={(event) => setThresholdInput(event.target.value)}
                />
                <input
                  className="settings-number-input"
                  type="number"
                  min="0"
                  max="1"
                  step="0.01"
                  value={thresholdInput}
                  onChange={(event) => setThresholdInput(event.target.value)}
                />
              </div>
            </label>

            <div className="settings-actions">
              <button
                className="neon-card"
                onClick={handleSaveThreshold}
                disabled={isSaving}
              >
                {isSaving ? "Speichern…" : "Schwellenwert speichern"}
              </button>
              <button
                className="neon-card"
                onClick={() =>
                  settings &&
                  setThresholdInput(
                    settings.cluster_distance_threshold_default.toFixed(2),
                  )
                }
              >
                Auf Standard zurücksetzen
              </button>
            </div>
          </section>

          <section className="settings-card">
            <div className="settings-card__kicker">Importe</div>
            <h2 className="settings-card__title">Bildordner einreihen</h2>
            <p className="settings-card__copy">
              Starte einen neuen Importjob, um einen weiteren Bilderordner in
              die Verarbeitungswarteschlange aufzunehmen.
            </p>

            <div className="settings-actions">
              <button
                className="neon-card import-folder-button"
                onClick={() => {
                  setError(null);
                  setMessage(null);
                  setShowFolderPicker(true);
                }}
                type="button"
              >
                Neuen Ordner importieren
              </button>
            </div>
          </section>

          <section className="settings-card">
            <div className="settings-card__kicker">Dateinamen</div>
            <h2 className="settings-card__title">Personen-Anhang</h2>
            <p className="settings-card__copy">
              Legen Sie fest, welche Zeichen zwischen dem eigentlichen
              Dateinamen und dem angehängten Namensblock stehen sollen und wie
              mehrere Personennamen voneinander getrennt werden.
            </p>

            <div className="settings-format-presets" aria-label="Trennung zwischen Dateiname und Namen">
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

            <label className="settings-field">
              <span>Zeichen zwischen Dateiname und Namensblock</span>
              <input
                className="settings-number-input settings-text-input"
                type="text"
                value={filenameBlockSeparatorInput}
                onChange={(event) =>
                  setFilenameBlockSeparatorInput(event.target.value)
                }
                placeholder="z. B.  - "
              />
            </label>

            <div className="settings-format-presets" aria-label="Trennzeichen-Vorlagen">
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

            <label className="settings-field">
              <span>Trennzeichen zwischen Personennamen</span>
              <input
                className="settings-number-input settings-text-input"
                type="text"
                value={personJoinerInput}
                onChange={(event) => setPersonJoinerInput(event.target.value)}
                placeholder="z. B. ,  oder  / "
              />
              <div className="settings-format-help">
                <span>
                  Vorschau für die Namen: <code>{joinerPreviewText}</code>
                </span>
              </div>
            </label>

            <div className="settings-format-preview">
              <span className="settings-format-preview__label">Vorschau</span>
              <code>{suffixPreview}</code>
              <small>
                Beispielpersonen: <code>Kai</code> und <code>Regina</code>
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

          <section className="settings-card">
            <div className="settings-card__kicker">Protokollierung</div>
            <h2 className="settings-card__title">Lokale Logdatei</h2>
            <p className="settings-card__copy">
              Steuern Sie, wie viele Details Face Manager in die lokale
              Logdatei schreibt. Im Windows-Deployment koennen Sie den Wert bei
              Problemen temporaer auf <code>INFO</code> oder <code>DEBUG</code>
              erhöhen und danach wieder reduzieren.
            </p>

            <label className="settings-field">
              <span>Log-Level</span>
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
                    {option.label} ({option.value})
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
              <span>Logdatei</span>
              <code>{settings?.error_log_path}</code>
            </div>

            <div className="settings-actions">
              <button
                className="neon-card"
                onClick={handleSaveLogLevel}
                disabled={isSaving}
              >
                {isSaving ? "Speichern…" : "Log-Level speichern"}
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
            <div className="settings-card__kicker">Datenbank</div>
            <h2 className="settings-card__title">Import und Export</h2>
            <p className="settings-card__copy">
              Exportieren Sie ein Backup der aktuellen SQLite-Datenbank oder
              ersetzen Sie sie durch eine vorhandene Face Manager-Datenbank.
            </p>

            <div className="settings-meta">
              <span>Datenbankpfad</span>
              <code>{settings?.database_path}</code>
            </div>

            <div className="settings-actions">
              <button
                className="neon-card"
                onClick={handleExport}
                disabled={isExporting}
              >
                {isExporting ? "Exportieren…" : "Datenbank exportieren"}
              </button>
              <button
                className="neon-card"
                onClick={handleImportClick}
                disabled={isImporting}
              >
                {isImporting ? "Importieren…" : "Datenbank importieren"}
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

      {showFolderPicker && (
        <FolderPickerModal
          onClose={(started) => {
            setShowFolderPicker(false);
            if (started) {
              setError(null);
              setMessage("Der Ordnerimport wurde zur Warteschlange hinzugefügt.");
            }
          }}
        />
      )}
    </div>
  );
};

export default SettingsPage;
