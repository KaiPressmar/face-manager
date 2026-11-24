import React, { useRef, useState } from "react";

import { updateSettings, type AppSettings } from "../../utils/api";
import "./preview-cache-settings.css";

const FALLBACK_DEFAULT_MIB = 768;
const MIN_MIB = 128;
const MAX_MIB = 1_048_576;
const LARGER_PRESETS = [2048, 8192, 32768];
const formatNumber = new Intl.NumberFormat("de-DE");

const PreviewCacheSettings: React.FC<{ settings: AppSettings }> = ({ settings }) => {
  const standard = settings.preview_cache_limit_mib_default ?? FALLBACK_DEFAULT_MIB;
  const presets = [...new Set([standard, ...LARGER_PRESETS])];
  const initialLimit = settings.preview_cache_limit_mib === undefined
    ? standard
    : settings.preview_cache_limit_mib;
  const [savedLimit, setSavedLimit] = useState(initialLimit);
  const [needsRepair, setNeedsRepair] = useState(settings.preview_cache_limit_error === true);
  const [choice, setChoice] = useState(() => {
    if (initialLimit === null) return "unlimited";
    return presets.includes(initialLimit) ? String(initialLimit) : "custom";
  });
  const [customSize, setCustomSize] = useState(initialLimit === null ? String(standard) : String(initialLimit));
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<{ text: string; error?: boolean } | null>(null);
  const saveInFlight = useRef(false);

  const custom = choice === "custom";
  const unlimited = choice === "unlimited";
  const limit = unlimited ? null : Number(custom ? customSize : choice);
  const invalid = limit !== null && (!Number.isInteger(limit) || limit < MIN_MIB || limit > MAX_MIB);
  const canSave = !invalid && (needsRepair || limit !== savedLimit);
  const validationMessage = `Gib eine ganze Zahl zwischen ${formatNumber.format(MIN_MIB)} und ${formatNumber.format(MAX_MIB)} MiB ein.`;

  const save = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canSave || saveInFlight.current) return;
    saveInFlight.current = true;
    setSaving(true);
    setFeedback(null);
    try {
      const next = await updateSettings({ preview_cache_limit_mib: limit });
      if (next.preview_cache_limit_error || next.preview_cache_limit_mib !== limit) {
        throw new Error("Die gespeicherte Cache-Größe konnte nicht bestätigt werden.");
      }
      setSavedLimit(next.preview_cache_limit_mib);
      setNeedsRepair(false);
      setFeedback({ text: "Die Cache-Einstellung wurde gespeichert." });
    } catch (error) {
      setFeedback({
        text: error instanceof Error ? error.message : "Die Cache-Einstellung konnte nicht gespeichert werden.",
        error: true,
      });
    } finally {
      saveInFlight.current = false;
      setSaving(false);
    }
  };

  return (
    <section className="settings-card settings-preview-cache" aria-labelledby="preview-cache-heading">
      <h2 id="preview-cache-heading" className="settings-card__title">Vorschau-Cache</h2>
      <p className="settings-card__copy">
        Face Manager speichert Foto- und Gesichtsvorschauen lokal. Die Einstellung betrifft nur diese
        Vorschauen. Originalbilder und die Bibliothek bleiben erhalten.
      </p>
      {needsRepair && (
        <p className="settings-feedback settings-feedback--error" role="alert">
          Die gespeicherte Cache-Einstellung konnte nicht gelesen werden. Die automatische Bereinigung ist
          pausiert. Speichere deine gewählte Einstellung erneut.
        </p>
      )}
      <form className="settings-preview-cache__form" onSubmit={(event) => void save(event)} noValidate>
        <label className="settings-field">
          <span>Cache-Größe</span>
          <select
            className="app-select settings-select-input"
            value={choice}
            aria-describedby="preview-cache-policy"
            disabled={saving}
            onChange={(event) => {
              setChoice(event.target.value);
              setFeedback(null);
            }}
          >
            {presets.map((preset) => (
              <option key={preset} value={preset}>
                {preset === standard
                  ? `Standard (${formatNumber.format(preset)} MiB)`
                  : `${formatNumber.format(preset / 1024)} GiB`}
              </option>
            ))}
            <option value="custom">Eigene Größe</option>
            <option value="unlimited">Ohne Grenze</option>
          </select>
        </label>
        {custom && (
          <label className="settings-field">
            <span>Eigene Größe in MiB</span>
            <input
              className="settings-number-input"
              type="number"
              inputMode="numeric"
              min={MIN_MIB}
              max={MAX_MIB}
              step={1}
              value={customSize}
              aria-invalid={invalid}
              aria-describedby="preview-cache-range"
              disabled={saving}
              onChange={(event) => {
                setCustomSize(event.target.value);
                setFeedback(null);
              }}
            />
          </label>
        )}
        {custom && (
          <p id="preview-cache-range" className="settings-card__copy" role={invalid ? "alert" : undefined}>
            {validationMessage}
          </p>
        )}
        <p id="preview-cache-policy" className="settings-card__copy">
          {unlimited
            ? "Vorschauen werden weder nach Alter noch nach Größe automatisch entfernt. Der Cache kann auf diesem Gerät weiter wachsen."
            : "Bei einer Begrenzung entfernt Face Manager Vorschauen nach 90 Tagen ohne Nutzung, auch wenn noch Platz ist. Wird die Größe überschritten, räumt die App zuerst die am längsten ungenutzten Vorschauen auf."}
        </p>
        <div className="settings-actions">
          <button type="submit" className="neon-card" disabled={!canSave || saving}>Cache-Einstellung speichern</button>
        </div>
        {saving && <p className="settings-feedback" role="status">Cache-Einstellung wird gespeichert …</p>}
        {feedback && (
          <p className={feedback.error ? "settings-feedback settings-feedback--error" : "settings-feedback"} role={feedback.error ? "alert" : "status"}>
            {feedback.text}
          </p>
        )}
      </form>
    </section>
  );
};

export default PreviewCacheSettings;
