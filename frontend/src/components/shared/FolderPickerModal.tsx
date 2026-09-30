import React, { useEffect, useState } from "react";

import { processFolder, selectImportFolder } from "../../utils/api";

interface FolderPickerModalProps {
  onClose: (started?: boolean) => void;
}

const IS_DEVELOPMENT = import.meta.env.DEV;

const FolderPickerModal: React.FC<FolderPickerModalProps> = ({ onClose }) => {
  const [folderPath, setFolderPath] = useState("");
  const [isBrowsing, setIsBrowsing] = useState(false);
  const [isStarting, setIsStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const busy = isBrowsing || isStarting;

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) onClose(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [busy, onClose]);

  const browse = async () => {
    setIsBrowsing(true);
    setError(null);
    try {
      const selectedPath = await selectImportFolder();
      if (selectedPath) setFolderPath(selectedPath);
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Der Ordnerdialog konnte nicht geöffnet werden.",
      );
    } finally {
      setIsBrowsing(false);
    }
  };

  const submit = async () => {
    const normalizedPath = folderPath.trim();
    if (!normalizedPath) return;

    setIsStarting(true);
    setError(null);
    try {
      await processFolder(normalizedPath);
      onClose(true);
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Der Ordner konnte nicht hinzugefügt werden.",
      );
      setIsStarting(false);
    }
  };

  return (
    <div
      className="modal-backdrop folder-picker-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onClose(false);
      }}
    >
      <section
        className={`folder-picker-modal ${IS_DEVELOPMENT ? "folder-picker-modal--dev" : "folder-picker-modal--prod"}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="folder-picker-title"
      >
        <header className="folder-picker-modal__header">
          <div>
            <span className="folder-picker-modal__eyebrow">
              {IS_DEVELOPMENT ? "Entwicklungsserver · WSL" : "Bildersammlung"}
            </span>
            <h2 id="folder-picker-title">Bilderordner hinzufügen</h2>
            <p>
              {IS_DEVELOPMENT
                ? "Der Entwicklungsserver greift direkt auf das WSL-Dateisystem zu."
                : "Wähle den Ordner aus, dessen Bilder Face Manager erkennen soll."}
            </p>
          </div>
          <button
            type="button"
            className="modal-close-button"
            onClick={() => onClose(false)}
            disabled={busy}
            aria-label="Ordnerauswahl schließen"
          >
            ×
          </button>
        </header>

        <form
          className="folder-picker-modal__form"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <div className="folder-picker-modal__content">
            {IS_DEVELOPMENT ? (
              <div className="folder-picker-modal__dev-input">
                <label htmlFor="development-import-path">Pfad zum Bilderordner</label>
                <input
                  id="development-import-path"
                  type="text"
                  value={folderPath}
                  onChange={(event) => setFolderPath(event.target.value)}
                  placeholder="C:\\Users\\Name\\Pictures oder /mnt/c/Users/Name/Pictures"
                  disabled={isStarting}
                  autoFocus
                  autoComplete="off"
                  spellCheck={false}
                />
                <p>
                  Windows-Pfade werden für WSL automatisch übersetzt. Alternativ kannst du
                  direkt einen Linux- oder <code>/mnt/…</code>-Pfad einfügen.
                </p>
              </div>
            ) : (
              <div className="folder-picker-modal__native-picker">
                <button
                  type="button"
                  className="folder-picker-modal__browse"
                  onClick={() => void browse()}
                  disabled={busy}
                >
                  <span className="folder-icon" aria-hidden="true" />
                  <span>
                    <strong>
                      {isBrowsing
                        ? "Windows-Ordnerauswahl wird geöffnet…"
                        : folderPath
                          ? "Anderen Ordner auswählen"
                          : "Ordner auswählen"}
                    </strong>
                    <small>Öffnet den sicheren Systemdialog</small>
                  </span>
                </button>

                {folderPath && (
                  <div className="folder-picker-modal__selected" title={folderPath}>
                    <span>Ausgewählt</span>
                    <strong>{folderPath}</strong>
                  </div>
                )}
              </div>
            )}

            {isStarting && (
              <div className="folder-picker-modal__status" role="status">
                Der Bilderordner wird zur Import-Warteschlange hinzugefügt…
              </div>
            )}
            {error && (
              <div className="folder-picker-modal__error" role="alert">
                {error}
              </div>
            )}
          </div>

          <footer className="folder-picker-modal__footer">
            <button
              type="button"
              className="secondary-button"
              onClick={() => onClose(false)}
              disabled={busy}
            >
              Abbrechen
            </button>
            <button
              type="submit"
              className="primary-button"
              disabled={!folderPath.trim() || busy}
            >
              {isStarting ? "Wird hinzugefügt…" : "Bilder hinzufügen"}
            </button>
          </footer>
        </form>
      </section>
    </div>
  );
};

export default FolderPickerModal;
