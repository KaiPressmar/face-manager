import React, { useRef, useState } from "react";
import type { SharedLibraryController } from "../../hooks/useSharedLibrary";
import { navigationHash } from "../../utils/navigation";
import {
  selectImportFolder,
  updateSharedLibrary,
  type SharedLibraryConfig,
  type SharedLibraryState,
} from "../../utils/api";
import "./shared-library.css";

const STATUS_LABEL: Record<SharedLibraryState["status"], string> = {
  off: "Abgleich aus",
  checking: "Freigabe wird geprüft …",
  online: "Freigabe verbunden",
  offline: "Freigabe nicht erreichbar",
  read_only: "Freigabe schreibgeschützt",
  paused: "Abgleich pausiert",
  attention: "Gemeinsamen Ordner prüfen",
};

function configOf(state: SharedLibraryState): SharedLibraryConfig {
  const { source_path, sync_path, paused, share_embeddings, write_enabled } = state;
  return { source_path, sync_path, paused, share_embeddings, write_enabled };
}

export default function SharedLibrarySettings({ controller }: { controller: SharedLibraryController }) {
  const { resource, readError, working, configured, refresh, mutate } = controller;
  const [draft, setDraft] = useState<SharedLibraryConfig | null>(null);
  const draftRef = useRef<SharedLibraryConfig | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [browsing, setBrowsing] = useState<"source_path" | "sync_path" | null>(null);
  const browseInFlight = useRef(false);
  const inputs = draft ?? (resource ? configOf(resource) : null);
  const disabled = working || browsing !== null;
  const canSave = Boolean(inputs?.source_path?.trim());
  const conflictCount = resource?.conflict_count ?? resource?.conflicts?.length ?? 0;

  const setConfigDraft = (next: SharedLibraryConfig | null) => {
    draftRef.current = next;
    setDraft(next);
  };

  const changePath = (field: "source_path" | "sync_path", path: string) => {
    const base = draftRef.current ?? inputs;
    if (base && base[field] !== path) setConfigDraft({
      ...base, [field]: path, write_enabled: false, initialize_share: false, share_embeddings: false,
    });
  };

  const browse = async (field: "source_path" | "sync_path", opener: HTMLButtonElement) => {
    if (browseInFlight.current || working) return;
    browseInFlight.current = true;
    setBrowsing(field);
    setNotice(null);
    setActionError(null);
    try {
      const path = await selectImportFolder();
      if (path) changePath(field, path);
      else setNotice("Kein Ordner ausgewählt.");
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Die Ordnerauswahl konnte nicht geöffnet werden.");
    } finally {
      browseInFlight.current = false;
      setBrowsing(null);
      window.requestAnimationFrame(() => {
        if (opener.isConnected && opener.closest(".page-pane--active") && document.activeElement === document.body) opener.focus();
      });
    }
  };

  const save = async (config: SharedLibraryConfig, success: string) => {
    if (browseInFlight.current) return;
    setNotice(null);
    setActionError(null);
    try {
      const next = await mutate(() => updateSharedLibrary(config));
      if (next) {
        setConfigDraft(null);
        setNotice(success);
      }
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Die Änderung konnte nicht gespeichert werden.");
    }
  };

  if (resource?.available === false || (!resource && !readError)) return null;

  return (
    <section className="settings-card shared-library" aria-labelledby="shared-library-title">
      <h2 id="shared-library-title" className="settings-card__title">Gemeinsamer Ordner</h2>
      <p className="settings-card__copy">{resource?.preview
        ? "Diese Vorschau prüft vorhandene Zuordnungen zu einem freigegebenen Fotoordner. Die Prüfung speichert keine Änderungen an der Freigabe oder an der Fotobibliothek."
        : "Face Manager gleicht bestätigte Zuordnungen für einen freigegebenen Fotoordner ab. Die Originaldateien bleiben an ihrem Speicherort."}</p>
      {readError && <div className="settings-feedback settings-feedback--error">
        <p role="alert">{readError}</p>
        <button type="button" className="secondary-button" disabled={disabled} onClick={() => void refresh(true)}>Erneut prüfen</button>
      </div>}
      {resource && <>
        {configured && <div className="shared-library__status">
          <p role="status"><strong>{readError ? "Letzter bekannter Stand" : STATUS_LABEL[resource.status]}</strong></p>
          {resource.device_name && <p>Dieses Gerät: {resource.device_name}</p>}
          {resource.preview && resource.proposed_count !== undefined &&
            <p>{resource.proposed_count} eindeutig zuordenbar · {conflictCount} zu prüfen</p>}
          {!resource.preview && (resource.imported_decisions !== undefined || resource.conflict_count !== undefined || conflictCount > 0) &&
            <p>{resource.imported_decisions ?? 0} Zuordnungen übernommen · {conflictCount} zu prüfen</p>}
          {conflictCount > 0 && <a href={navigationHash({ page: "review", reviewView: "shared" })}>Abgleich prüfen</a>}
          {resource.status === "offline" && <p>Die lokale Bibliothek bleibt verfügbar. Der Abgleich kann erst bei erreichbarer Freigabe fortgesetzt werden.</p>}
          {resource.status === "read_only" && <p>Dieses Gerät kann den gemeinsamen Stand lesen. Änderungen daran benötigen Schreibzugriff.</p>}
        </div>}
        {resource.error && <p className="settings-feedback settings-feedback--error" role="alert">{resource.error}</p>}
        <details>
          <summary>{configured ? "Einrichtung bearbeiten" : "Gemeinsamen Ordner einrichten"}</summary>
          {inputs && <form className="shared-library__form" onSubmit={(event) => {
            event.preventDefault();
            if (!canSave) return;
            void save({
              ...inputs,
              source_path: inputs.source_path?.trim() || null,
              sync_path: inputs.sync_path?.trim() || null,
              write_enabled: resource.write_available === true ? inputs.write_enabled === true : undefined,
              initialize_share: resource.write_available === true && inputs.write_enabled === true && inputs.initialize_share === true ? true : undefined,
              share_embeddings: resource.write_available === true && resource.analysis_available === true &&
                inputs.write_enabled === true && inputs.share_embeddings === true,
            }, "Die Einrichtung wurde gespeichert.");
          }}>
            <p>Trage auf jedem Gerät den Pfad zum selben Fotoordner und zum selben Abgleichordner ein. Der Fotoordner darf schreibgeschützt sein.</p>
            {([
              ["source_path", "Fotoordner", "Hier liegen die Originale. Lesezugriff genügt."],
              ["sync_path", "Abgleichordner (optional)", "Ohne eigenen Pfad nutzt Face Manager den Fotoordner. Zum Beitragen braucht das Gerät hier Schreibzugriff."],
            ] as const).map(([field, label, help]) => <div key={field} className="shared-library__path">
              <label className="settings-field">
                <span>{label}</span>
                <input className="settings-number-input settings-text-input" value={inputs[field] ?? ""} disabled={disabled} required={field === "source_path"}
                  autoComplete="off" spellCheck={false} aria-describedby={`shared-library-help-${field}`} onChange={(event) => changePath(field, event.target.value)} />
              </label>
              <button type="button" className="secondary-button" disabled={disabled} onClick={(event) => void browse(field, event.currentTarget)}>
                {browsing === field ? "Ordnerauswahl wird geöffnet …" : field === "source_path" ? "Fotoordner auswählen" : "Abgleichordner auswählen"}
              </button>
              <p id={`shared-library-help-${field}`}>{help} Du kannst den Pfad auch eingeben.</p>
            </div>)}
            <p>Wer auf den Abgleichordner zugreifen kann, kann gemeinsame Personennamen und Fotobezüge sehen.</p>
            {resource.write_available === true && <>
              <label className="shared-library__toggle">
                <input
                  type="checkbox"
                  checked={inputs.write_enabled === true}
                  aria-describedby="shared-library-write-description"
                  disabled={disabled}
                  onChange={(event) => setConfigDraft({
                    ...inputs, write_enabled: event.target.checked, initialize_share: false, share_embeddings: false,
                  })}
                />
                Schreibenden Abgleich für diese Ordner erlauben
              </label>
              <p id="shared-library-write-description">Face Manager kann bestätigte Personennamen und Fotobezüge für diese Ordner veröffentlichen und eindeutig passende Zuordnungen des geprüften Geräts übernehmen.</p>
              {inputs.write_enabled === true && <label className="shared-library__toggle">
                <input type="checkbox" checked={inputs.initialize_share === true} disabled={disabled}
                  onChange={(event) => setConfigDraft({ ...inputs, initialize_share: event.target.checked })} />
                Gemeinsamen Stand hier erstmals anlegen
              </label>}
              {inputs.write_enabled === true && resource.analysis_available === true && <>
                <label className="shared-library__toggle">
                  <input type="checkbox" checked={inputs.share_embeddings === true} disabled={disabled}
                    aria-describedby="shared-library-analysis-description"
                    onChange={(event) => setConfigDraft({ ...inputs, share_embeddings: event.target.checked })} />
                  Gesichtsmerkmale mit anderen Geräten teilen
                </label>
                <p id="shared-library-analysis-description">Geräte mit Leserechten für den Abgleichordner können numerische Gesichtsmerkmale wiederverwenden. Dafür brauchen sie ein kompatibles Erkennungsmodell. Die Bilddateien bleiben an ihrem Speicherort.</p>
              </>}
            </>}
            {configured && <label className="shared-library__toggle">
              <input type="checkbox" checked={inputs.paused} disabled={disabled} onChange={(event) => setConfigDraft({ ...inputs, paused: event.target.checked })} />
              Abgleich auf diesem Gerät pausieren
            </label>}
            <div className="settings-actions">
              <button type="submit" className="neon-card" disabled={disabled || !canSave}>Einrichtung speichern</button>
              {configured && <button type="button" className="secondary-button" disabled={disabled} onClick={() => {
                if (!window.confirm("Den Abgleich für dieses Gerät ausschalten?")) return;
                void save({
                  source_path: null,
                  sync_path: null,
                  paused: false,
                  share_embeddings: false,
                  write_enabled: resource.write_available === true ? false : undefined,
                }, "Der Abgleich wurde auf diesem Gerät ausgeschaltet.");
              }}>Abgleich ausschalten</button>}
            </div>
          </form>}
        </details>
      </>}
      {working && <p role="status">Änderung wird gespeichert …</p>}
      {notice && <p className="settings-feedback" role="status">{notice}</p>}
      {actionError && <p className="settings-feedback settings-feedback--error" role="alert">{actionError}</p>}
    </section>
  );
}
