import React, { useId, useRef, useState } from "react";
import type { SharedLibraryController } from "../../hooks/useSharedLibrary";
import { navigationHash } from "../../utils/navigation";
import {
  selectImportFolder,
  updateSharedLibrary,
  type SharedLibraryConfig,
  type SharedLibraryFolder,
  type SharedLibraryUpdate,
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

function configOf(state: SharedLibraryFolder): SharedLibraryConfig {
  const { source_path, sync_path, paused, share_embeddings, write_enabled, assist_paused } = state;
  return { source_path, sync_path, paused, share_embeddings, write_enabled, assist_paused };
}

function withUnapprovedPaths(
  config: SharedLibraryConfig,
  paths: Partial<Pick<SharedLibraryConfig, "source_path" | "sync_path">>,
): SharedLibraryConfig {
  return { ...config, ...paths, write_enabled: false, initialize_share: false, share_embeddings: false, assist_paused: false };
}

const EMPTY_CONFIG: SharedLibraryConfig = {
  source_path: null,
  sync_path: null,
  paused: false,
  share_embeddings: false,
};

function SharedFolderEditor({ folder, resource, controller, browsing, setBrowsing, onSaved, onCancel }: {
  folder?: SharedLibraryFolder;
  resource: SharedLibraryState;
  controller: SharedLibraryController;
  browsing: boolean;
  setBrowsing: (value: boolean) => void;
  onSaved: (notice: string) => void;
  onCancel?: () => void;
}) {
  const { readError, working, mutate } = controller;
  const scope = useId();
  const sourceInput = useRef<HTMLInputElement>(null);
  const editor = useRef<HTMLDetailsElement>(null);
  const isAdding = Boolean(onCancel);
  React.useEffect(() => { if (isAdding) sourceInput.current?.focus(); }, [isAdding]);
  const configured = Boolean(folder);
  const writeAvailable = (folder?.write_available ?? resource.write_available) === true;
  const analysisAvailable = (folder?.analysis_available ?? resource.analysis_available) === true;
  const folderId = folder?.folder_id ?? (resource.folders && !folder ? "new" : undefined);
  const [draft, setDraft] = useState<SharedLibraryConfig | null>(null);
  const draftRef = useRef<SharedLibraryConfig | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [browsingField, setBrowsingField] = useState<"source_path" | "sync_path" | null>(null);
  const browseInFlight = useRef(false);
  const inputs = draft ?? (folder ? configOf(folder) : EMPTY_CONFIG);
  const disabled = working || browsing;
  const canSave = Boolean(inputs.source_path?.trim());
  const conflictCount = folder?.conflict_count ?? folder?.conflicts?.length ?? 0;
  const parentPath = readError ? null : folder?.parent_source_path;

  const setConfigDraft = (next: SharedLibraryConfig | null) => {
    draftRef.current = next;
    setDraft(next);
  };

  const changePath = (field: "source_path" | "sync_path", path: string) => {
    const base = draftRef.current ?? inputs;
    if (base[field] !== path) setConfigDraft(withUnapprovedPaths(base, { [field]: path }));
  };

  const useParentFolder = (path: string) => {
    setConfigDraft(withUnapprovedPaths(draftRef.current ?? inputs, { source_path: path, sync_path: null }));
    setActionError(null);
    setNotice("Der Fotoordner steht im Entwurf. Prüfe die Pfade und speichere die Einrichtung.");
    if (editor.current) editor.current.open = true;
    sourceInput.current?.focus();
  };

  const browse = async (field: "source_path" | "sync_path", opener: HTMLButtonElement) => {
    if (browseInFlight.current || disabled) return;
    browseInFlight.current = true;
    setBrowsingField(field);
    setBrowsing(true);
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
      setBrowsingField(null);
      setBrowsing(false);
      window.requestAnimationFrame(() => {
        if (opener.isConnected && opener.closest(".page-pane--active") && document.activeElement === document.body) opener.focus();
      });
    }
  };

  const save = async (config: SharedLibraryUpdate, success: string, options: {
    keepDraft?: boolean;
    focusSection?: boolean;
  } = {}) => {
    if (browseInFlight.current) return;
    setNotice(null);
    setActionError(null);
    try {
      const next = await mutate(() => updateSharedLibrary(config));
      if (next) {
        if (!options.keepDraft) setConfigDraft(null);
        if (!configured || options.focusSection) onSaved(success);
        else setNotice(success);
      }
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Die Änderung konnte nicht gespeichert werden.");
    }
  };

  return (
    <div className="shared-library__folder">
      {folder && <div className="shared-library__status">
        <h3>{folder.source_path}</h3>
        <p><strong>{readError ? "Letzter bekannter Stand" : STATUS_LABEL[folder.status]}</strong></p>
        {!readError && folder.preview && folder.proposed_count !== undefined &&
          <p>{folder.proposed_count} eindeutig zuordenbar · {conflictCount} zu prüfen</p>}
        {!readError && !folder.preview && (folder.imported_decisions !== undefined || conflictCount > 0) &&
          <p>{folder.imported_decisions ?? 0} Zuordnungen übernommen · {conflictCount} zu prüfen</p>}
        {!readError && folder.status === "offline" && <p>Die lokale Bibliothek bleibt verfügbar. Der Abgleich läuft weiter, sobald der Ordner erreichbar ist.</p>}
        {!readError && folder.status === "read_only" && <p>Dieses Gerät kann den gemeinsamen Stand lesen. Änderungen benötigen Schreibzugriff.</p>}
        {!readError && folder.error && <p className="settings-feedback settings-feedback--error" role="alert">{folder.error}</p>}
        {parentPath && parentPath !== inputs.source_path && <>
          <p id={`${scope}-parent`}>{parentPath}</p>
          <button type="button" className="secondary-button" disabled={disabled}
            aria-describedby={`${scope}-parent`} onClick={() => useParentFolder(parentPath)}>
            Übergeordneten Fotoordner auswählen
          </button>
        </>}
      </div>}
      {folder?.linked_paths && folder.linked_paths.length > 0 && <div className="shared-library__status">
        <p>Mit diesem Abgleich verknüpfte Ordner</p>
        <ul className="shared-library__alias-list">
          {folder.linked_paths.map((path) => <li key={path}>
            <span>{path}</span>
            {folderId && <button type="button" className="secondary-button" disabled={disabled} onClick={() => {
              if (!window.confirm(`Die Verknüpfung mit ${path} lösen?`)) return;
              void save({ folder_id: folderId, remove_linked_path: path }, "Die Ordnerverknüpfung wurde gelöst.", { keepDraft: true, focusSection: true });
            }}>Verknüpfung lösen<span className="shared-library__sr-only"> mit {path}</span></button>}
          </li>)}
        </ul>
      </div>}
      <details ref={editor} open={onCancel ? true : undefined}>
        <summary>
          {configured ? "Einrichtung bearbeiten" : "Gemeinsamen Ordner einrichten"}{folder && <span className="shared-library__sr-only"> für {folder.source_path}</span>}
        </summary>
        <form className="shared-library__form" onSubmit={(event) => {
          event.preventDefault();
          if (!canSave) return;
          void save({
            ...inputs,
            folder_id: folderId,
            source_path: inputs.source_path?.trim() || null,
            sync_path: inputs.sync_path?.trim() || null,
            write_enabled: writeAvailable ? inputs.write_enabled === true : undefined,
            initialize_share: writeAvailable && inputs.write_enabled === true && inputs.initialize_share === true ? true : undefined,
            share_embeddings: writeAvailable && analysisAvailable &&
              inputs.write_enabled === true && inputs.share_embeddings === true,
          }, "Die Einrichtung wurde gespeichert.");
        }}>
          {folder?.blocked_paths && folder.blocked_paths.length > 0 && folder.source_path === inputs.source_path &&
            <div className="shared-library__status">
              <p>Diese Unterordner sind von diesem Abgleich ausgenommen. Ihre Inhalte bleiben getrennt.</p>
              <ul className="shared-library__alias-list">
                {folder.blocked_paths.map((path) => <li key={path}>{path}</li>)}
              </ul>
            </div>}
          <p>Trage auf jedem Gerät den Pfad zum selben Fotoordner und zum selben Abgleichordner ein. Der Fotoordner darf schreibgeschützt sein.</p>
          {([
            ["source_path", "Fotoordner", "Hier liegen die Originale. Lesezugriff genügt."],
            ["sync_path", "Abgleichordner (optional)", "Ohne eigenen Pfad nutzt Face Manager den Fotoordner. Zum Beitragen braucht das Gerät hier Schreibzugriff."],
          ] as const).map(([field, label, help]) => <div key={field} className="shared-library__path">
            <label className="settings-field">
              <span>{label}</span>
              <input ref={field === "source_path" ? sourceInput : undefined} className="settings-number-input settings-text-input" value={inputs[field] ?? ""} disabled={disabled} required={field === "source_path"}
                autoComplete="off" spellCheck={false} aria-describedby={`${scope}-${field}`} onChange={(event) => changePath(field, event.target.value)} />
            </label>
            <button type="button" className="secondary-button" disabled={disabled} onClick={(event) => void browse(field, event.currentTarget)}>
              {browsingField === field ? "Ordnerauswahl wird geöffnet …" : field === "source_path" ? "Fotoordner auswählen" : "Abgleichordner auswählen"}
            </button>
            <p id={`${scope}-${field}`}>{help} Du kannst den Pfad auch eingeben.</p>
          </div>)}
          <p>Wer auf den Abgleichordner zugreifen kann, kann gemeinsame Personennamen und Fotobezüge sehen.</p>
          {writeAvailable && <>
            <label className="shared-library__toggle">
              <input
                type="checkbox"
                checked={inputs.write_enabled === true}
                aria-describedby={`${scope}-write`}
                disabled={disabled}
                onChange={(event) => setConfigDraft({
                  ...inputs, write_enabled: event.target.checked, initialize_share: false, share_embeddings: false, assist_paused: false,
                })}
              />
              Schreibenden Abgleich für diese Ordner erlauben
            </label>
            <p id={`${scope}-write`}>Face Manager kann bestätigte Personennamen und Fotobezüge für diese Ordner veröffentlichen und eindeutig passende Zuordnungen des geprüften Geräts übernehmen. Vor dem ersten Schreibabgleich sichert die App deine bestehende Fotobibliothek auf diesem Gerät.</p>
            {inputs.write_enabled === true && <label className="shared-library__toggle">
              <input type="checkbox" checked={inputs.initialize_share === true} disabled={disabled}
                onChange={(event) => setConfigDraft({ ...inputs, initialize_share: event.target.checked })} />
              Gemeinsamen Stand hier erstmals anlegen
            </label>}
            {inputs.write_enabled === true && analysisAvailable && <>
              <label className="shared-library__toggle">
                <input type="checkbox" checked={inputs.share_embeddings === true} disabled={disabled}
                  aria-describedby={`${scope}-analysis`}
                  onChange={(event) => setConfigDraft({ ...inputs, share_embeddings: event.target.checked, assist_paused: false })} />
                Gesichtsmerkmale mit anderen Geräten teilen
              </label>
              <p id={`${scope}-analysis`}>Geräte mit Leserechten für den Abgleichordner können numerische Gesichtsmerkmale wiederverwenden. Dafür brauchen sie ein kompatibles Erkennungsmodell. Die Bilddateien bleiben an ihrem Speicherort.</p>
              {inputs.share_embeddings && <label className="shared-library__toggle">
                <input type="checkbox" checked={inputs.assist_paused === true} disabled={disabled}
                  aria-describedby={`${scope}-assist`}
                  onChange={(event) => setConfigDraft({ ...inputs, assist_paused: event.target.checked })} />
                Eigene Rechenhilfe pausieren
              </label>}
              {inputs.share_embeddings && <p id={`${scope}-assist`}>Dieses Gerät beginnt keine neuen gemeinsamen Analyseaufträge. Es kann weiterhin fertige Ergebnisse und Zuordnungen empfangen.</p>}
            </>}
          </>}
          {configured && <label className="shared-library__toggle">
            <input type="checkbox" checked={inputs.paused} disabled={disabled} onChange={(event) => setConfigDraft({ ...inputs, paused: event.target.checked })} />
            Abgleich auf diesem Gerät pausieren
          </label>}
          <div className="settings-actions">
            <button type="submit" className="neon-card" disabled={disabled || !canSave}>Einrichtung speichern</button>
            {configured && <button type="button" className="secondary-button" disabled={disabled} onClick={() => {
              if (!window.confirm(`Den Abgleich für ${folder?.source_path} auf diesem Gerät ausschalten?`)) return;
              void save({
                folder_id: folderId,
                source_path: null,
                sync_path: null,
                paused: false,
                share_embeddings: false,
                write_enabled: writeAvailable ? false : undefined,
              }, "Der Abgleich für diesen Ordner wurde auf diesem Gerät ausgeschaltet.", { focusSection: true });
            }}>Abgleich ausschalten</button>}
            {onCancel && <button type="button" className="secondary-button" disabled={disabled} onClick={onCancel}>Abbrechen</button>}
          </div>
        </form>
      </details>
      {notice && <p className="settings-feedback" role="status">{notice}</p>}
      {actionError && <p className="settings-feedback settings-feedback--error" role="alert">{actionError}</p>}
    </div>
  );
}

export default function SharedLibrarySettings({ controller }: { controller: SharedLibraryController }) {
  const { resource, folders, readError, working, refresh } = controller;
  const [adding, setAdding] = useState(false);
  const [browsing, setBrowsing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const addButton = useRef<HTMLButtonElement>(null);

  if (resource?.available === false || (!resource && !readError)) return null;

  const conflictCount = resource?.conflict_count ?? resource?.conflicts?.length ?? 0;
  const finish = (message: string) => {
    setNotice(message);
    window.requestAnimationFrame(() => heading.current?.focus());
  };

  return (
    <section className="settings-card shared-library" aria-labelledby="shared-library-title">
      <h2 id="shared-library-title" className="settings-card__title" tabIndex={-1} ref={heading}>Gemeinsame Ordner</h2>
      <p className="settings-card__copy">{resource?.preview
        ? "Die Vorschau prüft vorhandene Zuordnungen zu freigegebenen Fotoordnern. Sie speichert keine Änderungen an der Freigabe oder an der Fotobibliothek."
        : "Face Manager gleicht bestätigte Zuordnungen für freigegebene Fotoordner ab. Die Originaldateien bleiben an ihrem Speicherort."}</p>
      {readError && <div className="settings-feedback settings-feedback--error">
        <p role="alert">{readError}</p>
        <button type="button" className="secondary-button" disabled={working || browsing} onClick={() => void refresh(true)}>Erneut prüfen</button>
      </div>}
      {resource && <>
        {folders.length > 0 && resource.device_name && <p>Dieses Gerät: {resource.device_name}</p>}
        {conflictCount > 0 && <a href={navigationHash({ page: "review", reviewView: "shared" })}>Abgleich prüfen ({conflictCount})</a>}
        {!folders.length && resource.error && <p className="settings-feedback settings-feedback--error" role="alert">{resource.error}</p>}
        <div className="shared-library__folders">
          {folders.map((folder) => <SharedFolderEditor key={folder.folder_id ?? "legacy"} folder={folder} resource={resource}
            controller={controller} browsing={browsing} setBrowsing={setBrowsing} onSaved={finish} />)}
          {(adding || !folders.length) && <SharedFolderEditor key="new" resource={resource} controller={controller}
            browsing={browsing} setBrowsing={setBrowsing} onSaved={(message) => {
              setAdding(false);
              finish(message);
            }} onCancel={adding ? () => {
              setAdding(false);
              window.requestAnimationFrame(() => addButton.current?.focus());
            } : undefined} />}
        </div>
        {resource.folders && folders.length > 0 && !adding && <button type="button" className="secondary-button" ref={addButton}
          disabled={working || browsing} onClick={() => { setNotice(null); setAdding(true); }}>Weiteren gemeinsamen Ordner hinzufügen</button>}
      </>}
      {working && <p role="status">Änderung wird gespeichert …</p>}
      {notice && <p className="settings-feedback" role="status">{notice}</p>}
    </section>
  );
}
