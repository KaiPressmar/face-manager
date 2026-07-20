import React, { useEffect, useMemo, useState } from "react";
import {
  fetchUpdateDownloadState,
  installDownloadedUpdate,
  openUpdateRelease,
  skipUpdate,
  startUpdateDownload,
  type AvailableUpdate,
  type UpdateDownloadState,
} from "../../utils/api";

interface Props {
  update: AvailableUpdate;
  onClose: () => void;
  onSkip: () => void;
}

function formatBytes(value?: number | null): string {
  if (!value) return "0 MB";
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

const UpdateAvailableModal: React.FC<Props> = ({ update, onClose, onSkip }) => {
  const [download, setDownload] = useState<UpdateDownloadState>({ status: "idle" });
  const [confirmInstall, setConfirmInstall] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const version = update.latest_version ?? "";

  useEffect(() => {
    if (!update.can_install) return;
    void fetchUpdateDownloadState().then((state) => {
      if (!state.version || state.version === version) setDownload(state);
    }).catch(() => undefined);
  }, [update.can_install, version]);

  useEffect(() => {
    if (download.status !== "downloading") return;
    const timer = window.setInterval(() => {
      void fetchUpdateDownloadState()
        .then(setDownload)
        .catch(() => undefined);
    }, 750);
    return () => window.clearInterval(timer);
  }, [download.status]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [download.status, onClose]);

  const progress = useMemo(() => {
    if (!download.total_bytes || !download.downloaded_bytes) return null;
    return Math.min(100, Math.round((download.downloaded_bytes / download.total_bytes) * 100));
  }, [download.downloaded_bytes, download.total_bytes]);

  const handleDownload = async () => {
    setBusy(true);
    setError(null);
    try {
      setDownload(await startUpdateDownload(version));
    } catch (downloadError) {
      setError(downloadError instanceof Error ? downloadError.message : "Download fehlgeschlagen.");
    } finally {
      setBusy(false);
    }
  };

  const handleInstall = async () => {
    setBusy(true);
    setError(null);
    try {
      await installDownloadedUpdate(version);
    } catch (installError) {
      setBusy(false);
      setConfirmInstall(false);
      setError(installError instanceof Error ? installError.message : "Update fehlgeschlagen.");
    }
  };

  const handleSkip = async () => {
    setBusy(true);
    setError(null);
    try {
      await skipUpdate(version);
      onSkip();
    } catch (skipError) {
      setError(skipError instanceof Error ? skipError.message : "Version konnte nicht übersprungen werden.");
      setBusy(false);
    }
  };

  const handleOpenRelease = async () => {
    setError(null);
    try {
      await openUpdateRelease(version);
    } catch (openError) {
      setError(openError instanceof Error ? openError.message : "Die GitHub-Seite ist nicht verfügbar.");
    }
  };

  return (
    <div className="modal-backdrop update-modal-backdrop" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <section className="update-modal" role="dialog" aria-modal="true" aria-labelledby="update-title">
        <header className="update-modal__header">
          <div>
            <span className="update-modal__eyebrow">Neue Version verfügbar</span>
            <h2 id="update-title">Face Manager {version}</h2>
            <p>Installiert: Version {update.current_version}{update.build_variant && ` · Ausgabe für ${update.build_variant.toUpperCase()}`}</p>
          </div>
          <button type="button" className="modal-close-button" onClick={onClose} aria-label="Update-Hinweis schließen">×</button>
        </header>

        <div className="update-modal__content">
          {(update.sections ?? []).map((section) => (
            <section className="whats-new-section" key={section.title}>
              <h3>{section.title}</h3>
              <ul>{section.items.map((item) => <li key={item}>{item}</li>)}</ul>
            </section>
          ))}

          {download.status === "downloading" && (
            <div className="update-download" role="status">
              <div><strong>Update wird heruntergeladen…</strong><span>{progress === null ? formatBytes(download.downloaded_bytes) : `${progress} %`}</span></div>
              <progress value={download.downloaded_bytes ?? 0} max={download.total_bytes ?? undefined} />
            </div>
          )}
          {download.status === "ready" && (
            <div className="update-download update-download--ready" role="status">
              <strong>Update vollständig heruntergeladen und geprüft</strong>
              <span>{download.installer_name} · {formatBytes(download.total_bytes)}</span>
            </div>
          )}
          {download.status === "error" && <div className="update-modal__error" role="alert">{download.error}</div>}
          {error && <div className="update-modal__error" role="alert">{error}</div>}

          {confirmInstall && (
            <div className="update-confirm">
              <strong>Update jetzt starten?</strong>
              <p>Face Manager wird geschlossen und die Installation geöffnet. Da die Installationsdatei noch nicht digital signiert ist, kann Windows weiterhin eine Sicherheitswarnung anzeigen.</p>
              <div className="update-confirm__actions">
                <button type="button" className="settings-text-button" onClick={() => setConfirmInstall(false)} disabled={busy}>Abbrechen</button>
                <button type="button" className="primary-button" onClick={handleInstall} disabled={busy}>{busy ? "Wird gestartet…" : "Face Manager schließen und installieren"}</button>
              </div>
            </div>
          )}
        </div>

        {!confirmInstall && (
          <footer className="update-modal__footer">
            <div className="update-modal__secondary-actions">
              <button type="button" className="settings-text-button" onClick={handleSkip} disabled={busy || download.status === "downloading"}>Diese Version überspringen</button>
              <button type="button" className="settings-text-button" onClick={onClose}>Später</button>
              <button type="button" className="settings-text-button" onClick={handleOpenRelease}>GitHub öffnen</button>
            </div>
            {update.can_install && update.download_available ? (
              download.status === "ready" ? (
                <button type="button" className="primary-button" onClick={() => setConfirmInstall(true)}>Update installieren</button>
              ) : (
                <button type="button" className="primary-button" onClick={handleDownload} disabled={busy || download.status === "downloading"}>{download.status === "downloading" ? "Download läuft…" : "Update herunterladen"}</button>
              )
            ) : (
              <span className="update-modal__availability">
                {update.can_install ? "Die passende Installationsdatei wird noch bereitgestellt." : "Die automatische Installation ist nur in der Windows-App verfügbar."}
              </span>
            )}
          </footer>
        )}
      </section>
    </div>
  );
};

export default UpdateAvailableModal;
