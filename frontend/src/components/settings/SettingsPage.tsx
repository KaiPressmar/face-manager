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
        setError(loadError instanceof Error ? loadError.message : "Settings could not be loaded.");
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
      setError("The clustering threshold must be between 0.00 and 1.00.");
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
      setError(saveError instanceof Error ? saveError.message : "The settings could not be saved.");
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
      setMessage("Database export started.");
    } catch (exportError) {
      setError(exportError instanceof Error ? exportError.message : "The database could not be exported.");
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
      setMessage("Database imported successfully.");
    } catch (importError) {
      setError(importError instanceof Error ? importError.message : "The database could not be imported.");
    } finally {
      setIsImporting(false);
    }
  };

  return (
    <div className="settings-page">
      <div className="settings-page__header">
        <div>
          <div className="settings-page__eyebrow">Settings</div>
          <h1 className="settings-page__title">Database and clustering</h1>
          <p className="settings-page__copy">
            Manage the database file and tune how aggressively new faces are grouped into existing clusters.
          </p>
        </div>
      </div>

      {isLoading ? (
        <div className="settings-card">Loading settings…</div>
      ) : (
        <div className="settings-grid">
          <section className="settings-card">
            <div className="settings-card__kicker">Clustering</div>
            <h2 className="settings-card__title">Distance threshold</h2>
            <p className="settings-card__copy">
              Lower values are stricter and create more separate clusters. Higher values merge faces more easily.
            </p>

            <label className="settings-field">
              <span>Threshold</span>
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
              <button className="neon-card" onClick={handleSaveThreshold} disabled={isSaving}>
                {isSaving ? "Saving…" : "Save threshold"}
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
                Reset to default
              </button>
            </div>
          </section>

          <section className="settings-card">
            <div className="settings-card__kicker">Database</div>
            <h2 className="settings-card__title">Import and export</h2>
            <p className="settings-card__copy">
              Export a backup of the current SQLite database or replace it with an existing Face Manager database.
            </p>

            <div className="settings-meta">
              <span>Database path</span>
              <code>{settings?.database_path}</code>
            </div>

            <div className="settings-actions">
              <button className="neon-card" onClick={handleExport} disabled={isExporting}>
                {isExporting ? "Exporting…" : "Export database"}
              </button>
              <button className="neon-card" onClick={handleImportClick} disabled={isImporting}>
                {isImporting ? "Importing…" : "Import database"}
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
          className={error ? "settings-feedback settings-feedback--error" : "settings-feedback"}
        >
          {error || message}
        </div>
      )}
    </div>
  );
};

export default SettingsPage;
