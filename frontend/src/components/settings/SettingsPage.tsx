import React, { useEffect, useRef, useState } from "react";
import {
  AppSettings,
  exportDatabase,
  fetchSettings,
  importDatabase,
  updateSettings,
} from "../../utils/api";

const SettingsPage: React.FC = () => {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [thresholdInput, setThresholdInput] = useState("0.50");
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [isImporting, setIsImporting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    let cancelled = false;

    fetchSettings()
      .then((data) => {
        if (cancelled) return;
        setSettings(data);
        setThresholdInput(data.cluster_distance_threshold.toFixed(2));
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
      const next = await updateSettings(value);
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
    </div>
  );
};

export default SettingsPage;
