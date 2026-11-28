import React, { useEffect, useRef, useState } from "react";
import { useModalFocus } from "../../hooks/useModalFocus";
import { fetchRuntimeInfo, processFolder, selectImportFolder } from "../../utils/api";
import { pathBasename } from "../../utils/pathDisplay";
import "../import/import-sources.css";

interface FolderPickerModalProps {
  onClose: (started?: boolean) => void;
}

interface ImportSource {
  path: string;
  error?: string;
}

const IS_DEVELOPMENT = import.meta.env.DEV;

const FolderPickerModal: React.FC<FolderPickerModalProps> = ({ onClose }) => {
  const [sources, setSources] = useState<ImportSource[]>([]);
  const [draftPath, setDraftPath] = useState("");
  const [isBrowsing, setIsBrowsing] = useState(false);
  const [isStarting, setIsStarting] = useState(false);
  const [startingPath, setStartingPath] = useState("");
  const [queuedCount, setQueuedCount] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [browseCancelled, setBrowseCancelled] = useState(false);
  const [showLinuxPath, setShowLinuxPath] = useState(false);
  const queuedPaths = useRef(new Set<string>());
  const busyRef = useRef(false);
  const busy = isBrowsing || isStarting;
  const dialogRef = useRef<HTMLElement>(null);
  const close = () => onClose(queuedPaths.current.size > 0);
  useModalFocus(dialogRef, close, !busy);
  useEffect(() => {
    if (IS_DEVELOPMENT) return;
    let current = true;
    void fetchRuntimeInfo().then((runtime) => {
      if (current) setShowLinuxPath(runtime.host_platform === "linux" && runtime.display_platform !== "windows");
    }).catch(() => undefined);
    return () => { current = false; };
  }, []);

  const trimmedDraft = draftPath.trim();
  const hasSource = (path: string) => queuedPaths.current.has(path) || sources.some((source) => source.path === path);
  const draftIsNew = Boolean(trimmedDraft && !hasSource(trimmedDraft));
  const pendingCount = sources.length + Number(draftIsNew);
  const hasSourceError = sources.some((source) => Boolean(source.error));

  const addSource = (input: string) => {
    const path = input.trim();
    if (!path) return;
    if (hasSource(path)) {
      setError("Dieser Ordner ist bereits in deiner Auswahl oder wurde schon eingereiht.");
      return;
    }
    setSources((current) => [...current, { path }]);
    setDraftPath("");
    setError(null);
    setBrowseCancelled(false);
  };

  const browse = async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    setIsBrowsing(true);
    setError(null);
    setBrowseCancelled(false);
    try {
      const path = await selectImportFolder();
      if (path) addSource(path);
      else setBrowseCancelled(true);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Der Ordnerdialog konnte nicht geöffnet werden.");
    } finally {
      busyRef.current = false;
      setIsBrowsing(false);
    }
  };

  const submit = async () => {
    if (busyRef.current || pendingCount === 0) return;
    busyRef.current = true;
    setIsStarting(true);
    setError(null);
    setBrowseCancelled(false);
    const pending = draftIsNew ? [...sources, { path: trimmedDraft }] : sources;
    const failed: ImportSource[] = [];
    setSources(pending);
    setDraftPath("");
    for (const { path } of pending) {
      setStartingPath(path);
      try {
        await processFolder(path);
        queuedPaths.current.add(path);
        setQueuedCount(queuedPaths.current.size);
        setSources((current) => current.filter((source) => source.path !== path));
      } catch (requestError) {
        failed.push({
          path,
          error: requestError instanceof Error ? requestError.message : "Der Ordner konnte nicht eingereiht werden.",
        });
      }
    }
    busyRef.current = false;
    setIsStarting(false);
    setStartingPath("");
    setSources(failed);
    if (failed.length === 0) close();
  };

  return (
    <div
      className="modal-backdrop folder-picker-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busyRef.current) close();
      }}
    >
      <section
        className="folder-picker-modal import-sources-modal"
        role="dialog"
        ref={dialogRef}
        aria-modal="true"
        aria-labelledby="folder-picker-title"
      >
        <header className="folder-picker-modal__header">
          <div>
            <h2 id="folder-picker-title">Bilderordner hinzufügen</h2>
            <p>Sammle Ordner von deinem Gerät oder einem verbundenen Laufwerk.</p>
          </div>
          <button type="button" className="modal-close-button" onClick={close} disabled={busy} aria-label="Ordnerauswahl schließen">×</button>
        </header>

        <form
          className="folder-picker-modal__form"
          onSubmit={(event) => {
            event.preventDefault();
            if (!busyRef.current) addSource(draftPath);
          }}
        >
          <div className="folder-picker-modal__content">
            {!IS_DEVELOPMENT && (
              <button type="button" className="folder-picker-modal__browse" data-initial-focus onClick={() => void browse()} disabled={busy}>
                <span className="folder-icon" aria-hidden="true" />
                <span><strong>{isBrowsing ? "Ordnerauswahl wird geöffnet…" : sources.length > 0 ? "Weiteren Ordner auswählen" : "Ordner auswählen"}</strong></span>
              </button>
            )}
            {(IS_DEVELOPMENT || showLinuxPath) && (
              <div className="folder-picker-modal__dev-input">
                <label htmlFor="development-import-path">Pfad zum Bilderordner</label>
                <div className="import-sources__path-entry">
                  <input
                    id="development-import-path"
                    type="text"
                    value={draftPath}
                    onChange={(event) => { setDraftPath(event.target.value); setError(null); }}
                    placeholder={IS_DEVELOPMENT ? "/mnt/c/Users/Name/Pictures" : "/mnt/fotos"}
                    disabled={busy}
                    data-initial-focus={IS_DEVELOPMENT || undefined}
                    autoComplete="off"
                    spellCheck={false}
                  />
                  <button type="submit" className="secondary-button" disabled={!trimmedDraft || busy}>Zur Liste hinzufügen</button>
                </div>
                <p>{IS_DEVELOPMENT
                  ? "Verwende einen Pfad, auf den der Entwicklungsserver zugreifen kann."
                  : "Du kannst auch den Pfad zu einem Ordner oder einem eingebundenen Netzlaufwerk eingeben."}</p>
              </div>
            )}

            {sources.length > 0 && (
              <section className="import-sources__selection" aria-label="Ausgewählte Ordner">
                <h3>{sources.length === 1 ? "1 ausgewählter Ordner" : `${sources.length} ausgewählte Ordner`}</h3>
                <ul>
                  {sources.map((source) => (
                    <li key={source.path}>
                      <div className="import-sources__source-name">
                        <strong>{pathBasename(source.path)}</strong>
                        <span>{source.path}</span>
                        {source.error && <p className="import-sources__source-error" role="alert">{source.error}</p>}
                      </div>
                      <button
                        type="button"
                        className="secondary-button import-sources__remove"
                        aria-label={`Ordner ${source.path} aus der Auswahl entfernen`}
                        onClick={() => setSources((current) => current.filter((entry) => entry.path !== source.path))}
                        disabled={busy}
                      >
                        Entfernen
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {error && <p className="folder-picker-modal__error" role="alert">{error}</p>}
            {browseCancelled && <p className="folder-picker-modal__status" role="status">Kein neuer Ordner ausgewählt.</p>}
            {(isStarting || queuedCount > 0) && (
              <p className="folder-picker-modal__status" role="status">
                {queuedCount > 0 && (queuedCount === 1 ? "1 Ordner eingereiht. " : `${queuedCount} Ordner eingereiht. `)}
                {isStarting ? `„${pathBasename(startingPath)}“ wird eingereiht…` : "Den Fortschritt findest du unter Aktivitäten."}
              </p>
            )}

            <section className="import-sources__explanation" aria-label="So funktioniert der Import">
              <h3>Was passiert mit deinen Bildern?</h3>
              <ul>
                <li>Face Manager sucht in den Ordnern und Unterordnern nach JPG-, JPEG- und PNG-Dateien.</li>
                <li>Die Dateien bleiben an ihren Speicherorten. Identische Dateien erscheinen als ein Bild mit mehreren Speicherorten.</li>
                <li>Die Bilderordner bleiben unter Bilder → Ordner verwalten gespeichert. Den automatischen Import neuer Fotos kannst du dort einschalten.</li>
                <li>Gesichter werden erkannt und ähnliche Gesichter gruppiert. Du kannst die Gruppen prüfen und Personen benennen.</li>
              </ul>
            </section>
          </div>

          <footer className="folder-picker-modal__footer">
            <button type="button" className="secondary-button" onClick={close} disabled={busy}>
              {queuedCount > 0 ? "Schließen" : "Abbrechen"}
            </button>
            <button type="button" className="primary-button" disabled={pendingCount === 0 || busy} onClick={() => void submit()}>
              {isStarting ? "Wird eingereiht…" : hasSourceError ? "Ausstehende Ordner erneut versuchen" : pendingCount > 1 ? `${pendingCount} Ordner importieren` : "Bilder hinzufügen"}
            </button>
          </footer>
        </form>
      </section>
    </div>
  );
};

export default FolderPickerModal;
