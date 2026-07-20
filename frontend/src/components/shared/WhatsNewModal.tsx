import React, { useEffect } from "react";
import type { ReleaseNotes } from "../../utils/api";

interface Props {
  release: ReleaseNotes;
  onClose: () => void;
}

const CATEGORY_ICONS: Record<string, string> = {
  Neu: "+",
  Verbessert: "↑",
  Behoben: "✓",
};

const WhatsNewModal: React.FC<Props> = ({ release, onClose }) => {
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  const releaseDate = release.date
    ? new Intl.DateTimeFormat("de-DE", { dateStyle: "long" }).format(
        new Date(`${release.date}T00:00:00`),
      )
    : null;

  return (
    <div
      className="modal-backdrop whats-new-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="whats-new-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="whats-new-title"
      >
        <header className="whats-new-modal__header">
          <div>
            <span className="whats-new-modal__eyebrow">Update installiert</span>
            <h2 id="whats-new-title">Neu in Face Manager {release.version}</h2>
            {releaseDate && <p>Veröffentlicht am {releaseDate}</p>}
          </div>
          <button
            type="button"
            className="modal-close-button"
            onClick={onClose}
            aria-label="Versionshinweise schließen"
          >
            ×
          </button>
        </header>

        <div className="whats-new-modal__content">
          {release.sections.map((section) => (
            <section className="whats-new-section" key={section.title}>
              <h3>
                <span aria-hidden="true">{CATEGORY_ICONS[section.title] ?? "•"}</span>
                {section.title}
              </h3>
              <ul>
                {section.items.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </section>
          ))}
        </div>

        <footer className="whats-new-modal__footer">
          <p>Diese Hinweise kannst du später über die Versionsnummer erneut öffnen.</p>
          <button type="button" className="primary-button" onClick={onClose} autoFocus>
            Weiter zu Face Manager
          </button>
        </footer>
      </section>
    </div>
  );
};

export default WhatsNewModal;
