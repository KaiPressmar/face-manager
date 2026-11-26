import { useRef } from "react";
import { useModalFocus } from "../../hooks/useModalFocus";
import { countWithLabel } from "../../utils/germanFormatting";
import type { RestorePreview } from "../../utils/libraryMaintenance";
import "./LibraryMaintenance.css";

interface Props {
  file: File;
  preview: RestorePreview;
  onClose: () => void;
  onConfirm: () => void;
}

export default function RestoreConfirmation({ file, preview, onClose, onConfirm }: Props) {
  const dialog = useRef<HTMLElement>(null);
  useModalFocus(dialog, onClose);
  return <div className="modal-backdrop">
    <section ref={dialog} className="settings-card library-maintenance-dialog"
      role="dialog" aria-modal="true" aria-labelledby="restore-confirm-heading">
      <h2 id="restore-confirm-heading">Bibliothek aus Sicherung wiederherstellen?</h2>
      <p className="library-maintenance-filename"><strong>{file.name}</strong></p>
      {file.lastModified > 0 && <p>Datei vom {new Date(file.lastModified).toLocaleString("de-DE", {
        dateStyle: "medium", timeStyle: "short",
      })}</p>}
      <p>{countWithLabel(preview.photos, "Foto", "Fotos")} · {countWithLabel(preview.people, "Person", "Personen")} · {countWithLabel(preview.albums, "Album", "Alben")}</p>
      <p>Die Sicherung ersetzt deine aktuellen Zuordnungen, Alben und Einstellungen. Deine Fotodateien bleiben an ihren Speicherorten.</p>
      <p>Face Manager sichert den bisherigen Stand. Du findest ihn anschließend unter „Vorherige Bibliothek herunterladen“.</p>
      <div className="settings-actions">
        <button type="button" className="secondary-button" data-initial-focus onClick={onClose}>Abbrechen</button>
        <button type="button" className="primary-button" onClick={onConfirm}>Bibliothek wiederherstellen</button>
      </div>
    </section>
  </div>;
}
