import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { useModalFocus } from "../../hooks/useModalFocus";
import { fetchRuntimeInfo, selectImportFolder } from "../../utils/api";
import { countWithLabel } from "../../utils/germanFormatting";
import {
  cancelImportSourceRelink, configureImportSource, fetchImportSources, registerImportSource, relinkImportSource, removeImportSource, scanImportSource,
  resolveImportSourceConflict, type ImportSource, type ImportSourceConflict, type ImportSourcesSnapshot,
} from "../../utils/importSources";
import { pathBasename } from "../../utils/pathDisplay";
import "./import-sources.css";

const statusLabels: Record<ImportSource["status"], string> = {
  idle: "Noch nicht geprüft",
  queued: "Prüfung eingereiht",
  running: "Wird geprüft",
  completed: "Prüfung abgeschlossen",
  offline: "Nicht erreichbar",
  failed: "Prüfung fehlgeschlagen",
  preview_ready: "Neuer Speicherort geprüft",
};

const dateFormat = new Intl.DateTimeFormat("de-DE", { dateStyle: "medium", timeStyle: "short" });
const scanPending = (source: ImportSource) => source.status === "running" || source.status === "queued";

type EditorAction = { type: "add" } | { type: "relink" | "remove"; id: string };
type Editor = EditorAction & { libraryId: string };

const timestamp = (value: string | null) => {
  const date = value ? Date.parse(value) : NaN;
  return Number.isFinite(date) ? dateFormat.format(date) : "Noch nie";
};

function SourceConflictChoice({ conflict, working, onConfirm }: {
  conflict: ImportSourceConflict;
  working: boolean;
  onConfirm: (path: string) => void;
}) {
  const group = useId();
  const [selected, setSelected] = useState("");
  const choice = conflict.candidate_paths.includes(selected) ? selected : "";
  return <fieldset className="managed-sources__conflict">
    <legend className="managed-sources__path">Bisheriger Speicherort: {conflict.original_path}</legend>
    {conflict.candidate_paths.map((path) => <label key={path} className="managed-sources__path">
      <input type="radio" name={group} checked={choice === path} disabled={working} onChange={() => setSelected(path)} />
      {path}
    </label>)}
    <button type="button" className="secondary-button" disabled={!choice || working}
      aria-label={`Verknüpfung prüfen: ${conflict.original_path}`} onClick={() => onConfirm(choice)}>Verknüpfung prüfen</button>
  </fieldset>;
}

export default function ImportSourcesDialog({ onClose }: { onClose: () => void }) {
  const dialog = useRef<HTMLElement>(null);
  const editorField = useRef<HTMLInputElement>(null);
  const editorAction = useRef<HTMLButtonElement>(null);
  const addButton = useRef<HTMLButtonElement>(null);
  const editorOpener = useRef<HTMLButtonElement>(null);
  const restoreEditorFocus = useRef(false);
  const scanButtons = useRef(new Map<string, HTMLButtonElement>());
  const focusAfterSave = useRef<string>();
  const performing = useRef(false);
  const [snapshot, setSnapshot] = useState<ImportSourcesSnapshot>();
  const [refresh, setRefresh] = useState(0);
  const [working, setWorking] = useState(false);
  const [readError, setReadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const [draft, setDraft] = useState<Editor>();
  const [path, setPath] = useState("");
  const [manualPath, setManualPath] = useState(import.meta.env.DEV);
  const editor = draft?.libraryId === snapshot?.library_id ? draft : undefined;

  const dismissEditor = () => {
    restoreEditorFocus.current = true;
    setDraft(undefined);
    setPath("");
    setActionError("");
  };
  useModalFocus(dialog, () => editor ? dismissEditor() : onClose(), !working);

  useLayoutEffect(() => {
    if (editor) {
      (editorField.current ?? editorAction.current)?.focus();
    } else if (restoreEditorFocus.current) {
      restoreEditorFocus.current = false;
      const opener = editorOpener.current;
      (opener?.isConnected ? opener : addButton.current)?.focus({ preventScroll: true });
    }
  }, [editor]);

  useLayoutEffect(() => {
    const id = focusAfterSave.current;
    if (id) scanButtons.current.get(id)?.focus({ preventScroll: true });
    focusAfterSave.current = undefined;
  }, [snapshot]);

  useEffect(() => {
    if (import.meta.env.DEV) return;
    let active = true;
    void fetchRuntimeInfo().then((runtime) => {
      if (active) setManualPath(runtime.host_platform === "linux" && runtime.display_platform !== "windows");
    }).catch(() => {});
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (working) return;
    const controller = new AbortController();
    let timer: number | undefined;
    const load = async () => {
      let delay = 5000;
      try {
        const result = await fetchImportSources(controller.signal);
        if (controller.signal.aborted) return;
        setSnapshot(result);
        setReadError("");
        if (result.sources.some(scanPending)) delay = 1000;
      } catch (error) {
        if (!controller.signal.aborted) setReadError(error instanceof Error ? error.message : "Die Bilderordner sind nicht erreichbar.");
      } finally {
        if (!controller.signal.aborted) timer = window.setTimeout(load, delay);
      }
    };
    void load();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [refresh, working]);

  const run = async (operation: () => Promise<void>) => {
    if (performing.current) return;
    performing.current = true;
    setWorking(true);
    setNotice("");
    setActionError("");
    try {
      await operation();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Die Aktion ist fehlgeschlagen.");
    } finally {
      performing.current = false;
      setWorking(false);
    }
  };

  const saveSource = (operation: (libraryId: string) => Promise<ImportSource | void>, message: string,
    options: { dismiss?: boolean; removeId?: string; focusSource?: string } = {}) => {
    if (!snapshot) return;
    const { library_id: libraryId } = snapshot;
    void run(async () => {
      const updated = await operation(libraryId);
      focusAfterSave.current = options.focusSource;
      setSnapshot((current) => {
        if (current?.library_id !== libraryId) return current;
        if (!updated) return { ...current, sources: current.sources.filter(({ id }) => id !== options.removeId) };
        const index = current.sources.findIndex(({ id }) => id === updated.id);
        const sources = [...current.sources];
        if (index < 0) sources.push(updated);
        else sources[index] = updated;
        return { ...current, sources };
      });
      if (options.dismiss) dismissEditor();
      setNotice(message);
    });
  };

  const startEditor = (opener: HTMLButtonElement, action: EditorAction) => {
    if (!snapshot || working) return;
    editorOpener.current = opener;
    setPath("");
    setActionError("");
    setDraft({ ...action, libraryId: snapshot.library_id });
  };

  const chooseFolder = () => void run(async () => {
    const selected = await selectImportFolder();
    if (selected) setPath(selected);
    else setNotice("Kein Ordner ausgewählt.");
  });

  const pathEditor = (onSubmit: (folder: string) => void, label: string) => (
    <form className="managed-sources__editor" onSubmit={(event) => {
      event.preventDefault();
      if (!working && path.trim()) onSubmit(path.trim());
    }}>
      {manualPath && <label className="folder-picker-modal__dev-input">Pfad zum Bilderordner
        <input ref={editorField} type="text" value={path} readOnly={working}
          onChange={(event) => setPath(event.target.value)} />
      </label>}
      {!manualPath && path && <p className="managed-sources__path">{path}</p>}
      <div className="managed-sources__actions">
        <button type="button" ref={editorAction} className="secondary-button" aria-disabled={working}
          onClick={chooseFolder}>Ordner auswählen</button>
        <button type="submit" className="primary-button" disabled={!path.trim() || working}>{label}</button>
        <button type="button" className="secondary-button" aria-disabled={working}
          onClick={() => { if (!working) dismissEditor(); }}>Abbrechen</button>
      </div>
    </form>
  );

  return <div className="modal-backdrop folder-picker-backdrop">
    <section ref={dialog} className="folder-picker-modal import-sources-modal managed-sources" role="dialog"
      aria-modal="true" aria-labelledby="managed-sources-title">
      <header className="folder-picker-modal__header">
        <div>
          <h2 id="managed-sources-title">Bilderordner verwalten</h2>
          <p>Prüfe gespeicherte Ordner auf neue Fotos oder verbinde einen verschobenen Ordner erneut.</p>
        </div>
        <button type="button" className="modal-close-button" aria-label="Bilderordnerverwaltung schließen"
          aria-disabled={working} onClick={() => { if (!working) onClose(); }}>×</button>
      </header>
      <div className="folder-picker-modal__content">
        {readError && <div role="alert"><p>{readError}</p>
          <button type="button" className="secondary-button" onClick={() => setRefresh((value) => value + 1)}>Erneut laden</button>
        </div>}
        {!snapshot && !readError && <p role="status">Bilderordner werden geladen …</p>}
        <div className="managed-sources__actions">
          <button ref={addButton} type="button" className="secondary-button" data-initial-focus
            aria-disabled={!snapshot || working} onClick={(event) => startEditor(event.currentTarget, { type: "add" })}>Ordner merken</button>
        </div>
        {editor?.type === "add" && pathEditor((folder) => saveSource((libraryId) => registerImportSource(folder, libraryId),
          "Bilderordner gespeichert.", { dismiss: true }), "Speichern")}
        {actionError && <p className="folder-picker-modal__error" role="alert">{actionError}</p>}
        {notice && <p className="folder-picker-modal__status" role="status">{notice}</p>}
        {snapshot?.sources.length === 0 && <p>Hier erscheinen deine hinzugefügten Bilderordner.</p>}
        {snapshot && snapshot.sources.length > 0 && <ul className="managed-sources__list">
          {snapshot.sources.map((source) => {
            const folder = source.folder_path;
            const name = pathBasename(folder);
            const pending = scanPending(source);
            const preview = source.status === "preview_ready" ? source.pending_path : null;
            const edit = editor && editor.type !== "add" && editor.id === source.id ? editor.type : undefined;
            return <li key={source.id} aria-label={folder}>
              <div className="managed-sources__identity">
                <h3>{name}</h3>
                <p className="managed-sources__path">{folder}</p>
                <p role="status">{statusLabels[source.status]}{!source.auto_scan && " · Automatische Prüfung pausiert"}</p>
              </div>
              <dl className="managed-sources__dates">
                <div><dt>Letzte Prüfung</dt><dd>{timestamp(source.last_scan_at)}</dd></div>
                <div><dt>Zuletzt erfolgreich geprüft</dt><dd>{timestamp(source.last_success_at)}</dd></div>
              </dl>
              {source.error && <p className="folder-picker-modal__error">{source.error}</p>}
              {source.status === "running" && source.scanned_files > 0 && <p>
                {countWithLabel(source.scanned_files, "Datei geprüft", "Dateien geprüft")}
              </p>}
              {source.status === "completed" && <p>{source.new_files > 0
                ? countWithLabel(source.new_files, "neues Foto", "neue Fotos")
                : source.error ? "Keine neuen Fotos in den geprüften Pfaden" : "Keine neuen Fotos"}
                {source.new_folders > 0 && ` · ${countWithLabel(source.new_folders, "neuer Unterordner", "neue Unterordner")}`}
                {source.relinked_files > 0 && ` · ${countWithLabel(source.relinked_files, "erneut verknüpftes Foto", "erneut verknüpfte Fotos")}`}
                {source.unavailable_files > 0 && ` · ${countWithLabel(source.unavailable_files, "fehlende Datei", "fehlende Dateien")}`}
                {source.conflicts > 0 && ` · ${countWithLabel(source.conflicts, "unklarer Treffer", "unklare Treffer")}`}
              </p>}
              {source.status === "completed" && !source.pending_path && !!source.conflict_candidates?.length &&
                <details className="managed-sources__editor">
                  <summary>Unklare Speicherorte prüfen</summary>
                  <p>Wähle den passenden neuen Pfad. Face Manager prüft die Datei vor der Verknüpfung erneut.</p>
                  {source.conflict_candidates.map((conflict) => <SourceConflictChoice key={conflict.original_path}
                    conflict={conflict} working={working} onConfirm={(candidate) => saveSource((libraryId) =>
                      resolveImportSourceConflict(source.id, libraryId, conflict.original_path, candidate),
                      "Verknüpfung zur Prüfung eingereiht.", { focusSource: source.id })} />)}
                </details>}
              {source.auto_import_blocked && <p>Nach dem Abbruch wartet der Import auf deinen nächsten Start.</p>}
              <div className="managed-sources__settings" role="group" aria-label={`Automatik für ${folder}`}>
                <label><input type="checkbox" checked={source.auto_scan} aria-disabled={working}
                  onChange={(event) => {
                    if (!working) saveSource((libraryId) => configureImportSource(source.id, libraryId, { auto_scan: event.target.checked }),
                      "Automatische Prüfung geändert.");
                  }} />Automatisch prüfen</label>
                <label><input type="checkbox" checked={source.auto_import} aria-disabled={working}
                  onChange={(event) => {
                    if (!working) saveSource((libraryId) => configureImportSource(source.id, libraryId, { auto_import: event.target.checked }),
                      "Automatischen Import geändert.");
                  }} />Neue Fotos automatisch importieren</label>
              </div>
              <div className="managed-sources__actions">
                {source.auto_import_blocked && <button type="button" className="secondary-button" aria-disabled={working}
                  aria-label={`Automatischen Import wieder aktivieren: ${folder}`} onClick={() =>
                    saveSource((libraryId) => configureImportSource(source.id, libraryId, { auto_import: true }),
                      "Automatischer Import wieder aktiviert.", { focusSource: source.id })}>Automatischen Import wieder aktivieren</button>}
                <button type="button" className="secondary-button" aria-disabled={working || pending}
                  ref={(button) => {
                    if (button) scanButtons.current.set(source.id, button);
                    else scanButtons.current.delete(source.id);
                  }}
                  aria-label={`Jetzt prüfen: ${folder}`} onClick={() => {
                    if (!pending) saveSource((libraryId) => scanImportSource(source.id, libraryId, false), "Ordnerprüfung eingereiht.");
                  }}>Jetzt prüfen</button>
                {source.status === "completed" && source.new_files > 0 && <button type="button" className="primary-button"
                  aria-disabled={working} aria-label={`Neue Fotos importieren: ${folder}`} onClick={() =>
                    saveSource((libraryId) => scanImportSource(source.id, libraryId, true), "Neue Fotos zum Import eingereiht.",
                      { focusSource: source.id })}>Neue Fotos importieren</button>}
                <button type="button" className="secondary-button" aria-disabled={working || pending}
                  aria-label={`Speicherort ändern: ${folder}`} onClick={(event) => {
                    if (!pending) startEditor(event.currentTarget, { type: "relink", id: source.id });
                  }}>Speicherort ändern</button>
                <button type="button" className="secondary-button" aria-disabled={working || pending}
                  aria-label={`Aus der Verwaltung entfernen: ${folder}`} onClick={(event) => {
                    if (!pending) startEditor(event.currentTarget, { type: "remove", id: source.id });
                  }}>Aus Verwaltung entfernen</button>
              </div>
              {source.pending_path && <div className="managed-sources__editor">
                <h4>Neuer Speicherort</h4>
                <p className="managed-sources__path">{source.pending_path}</p>
                {preview && <p>{countWithLabel(source.relinked_files, "verifiziertes Foto", "verifizierte Fotos")}
                  {` · ${countWithLabel(source.unavailable_files, "nicht verifizierte Datei", "nicht verifizierte Dateien")}`}
                  {source.conflicts > 0 && ` · ${countWithLabel(source.conflicts, "unklarer Treffer", "unklare Treffer")}`}
                  {` · ${countWithLabel(source.new_files, "neues Foto", "neue Fotos")}`}</p>}
                <div className="managed-sources__actions">
                  {preview && <button type="button" className="primary-button" aria-disabled={working}
                    aria-label={`Neuen Speicherort übernehmen: ${preview}`} onClick={() =>
                      saveSource((libraryId) => relinkImportSource(source.id, libraryId, preview, true),
                        "Die Verknüpfung wird erneut geprüft.", { focusSource: source.id })}>Neuen Speicherort übernehmen</button>}
                  {!pending && <button type="button" className="secondary-button" aria-disabled={working}
                    aria-label={`Neuen Speicherort verwerfen: ${source.pending_path}`} onClick={() =>
                      saveSource((libraryId) => cancelImportSourceRelink(source.id, libraryId), "Der bisherige Speicherort bleibt verbunden.",
                        { focusSource: source.id })}>Vorschau verwerfen</button>}
                </div>
              </div>}
              {edit === "relink" && pathEditor((folderPath) => saveSource((libraryId) =>
                relinkImportSource(source.id, libraryId, folderPath), "Der neue Speicherort wird geprüft.", { dismiss: true }), "Neuen Speicherort prüfen")}
              {edit === "remove" && <div className="managed-sources__editor">
                <p>Importierte Fotos und ihre Zuordnungen bleiben in der Bibliothek.</p>
                <div className="managed-sources__actions">
                  <button ref={editorAction} type="button" className="secondary-button" aria-disabled={working}
                    onClick={() => { if (!working) dismissEditor(); }}>Abbrechen</button>
                  <button type="button" className="secondary-button" aria-disabled={working} onClick={() =>
                    saveSource((libraryId) => removeImportSource(source.id, libraryId), "Ordner aus der Verwaltung entfernt.",
                      { dismiss: true, removeId: source.id })}>Ordner entfernen</button>
                </div>
              </div>}
            </li>;
          })}
        </ul>}
      </div>
    </section>
  </div>;
}
