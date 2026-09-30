import React, { useEffect } from "react";
import type { ReleaseNotes } from "../../utils/api";

type Variant = "whats-new" | "history";

interface Props {
  releases: ReleaseNotes[];
  variant?: Variant;
  onClose: () => void;
  onShowFullChangelog?: () => void;
}

const CATEGORY_ICONS: Record<string, string> = {
  Neu: "+",
  Verbessert: "↑",
  Behoben: "✓",
};

function formatReleaseDate(date: string | null): string | null {
  if (!date) return null;
  return new Intl.DateTimeFormat("de-DE", { dateStyle: "long" }).format(
    new Date(`${date}T00:00:00`),
  );
}

const WhatsNewModal: React.FC<Props> = ({
  releases,
  variant = "whats-new",
  onClose,
  onShowFullChangelog,
}) => {
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  const isHistory = variant === "history";
  const multiple = releases.length > 1;
  const latest = releases[0];

  const eyebrow = isHistory ? "Alle Versionen" : "Update installiert";
  const title = isHistory
    ? "Änderungsprotokoll"
    : multiple
      ? "Das ist neu seit deiner letzten Version"
      : `Neu in Face Manager ${latest?.version ?? ""}`.trim();
  const subtitle =
    isHistory
      ? "Alle veröffentlichten Neuerungen im Überblick."
      : multiple
        ? `Neuerungen aus ${releases.length} Versionen – keine wird übersprungen.`
        : latest
          ? (() => {
              const releaseDate = formatReleaseDate(latest.date);
              return releaseDate ? `Veröffentlicht am ${releaseDate}` : null;
            })()
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
            <span className="whats-new-modal__eyebrow">{eyebrow}</span>
            <h2 id="whats-new-title">{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
          <button
            type="button"
            className="modal-close-button"
            onClick={onClose}
            aria-label={isHistory ? "Änderungsprotokoll schließen" : "Versionshinweise schließen"}
          >
            ×
          </button>
        </header>

        <div className="whats-new-modal__content">
          {releases.length === 0 && (
            <p className="whats-new-modal__empty">
              Es sind noch keine veröffentlichten Änderungen vorhanden.
            </p>
          )}
          {releases.map((release) => {
            const releaseDate = formatReleaseDate(release.date);
            // A per-version heading is only useful when several versions share
            // the view; a single "what's new" release already names itself in
            // the modal title.
            const showVersionHeading = isHistory || multiple;
            return (
              <article className="whats-new-release" key={release.version}>
                {showVersionHeading && (
                  <div className="whats-new-release__heading">
                    <h3>Version {release.version}</h3>
                    {releaseDate && <span>{releaseDate}</span>}
                  </div>
                )}
                {release.sections.map((section) => (
                  <section className="whats-new-section" key={section.title}>
                    <h4>
                      <span aria-hidden="true">
                        {CATEGORY_ICONS[section.title] ?? "•"}
                      </span>
                      {section.title}
                    </h4>
                    <ul>
                      {section.items.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  </section>
                ))}
              </article>
            );
          })}
        </div>

        <footer className="whats-new-modal__footer">
          {isHistory ? (
            <button type="button" className="primary-button" onClick={onClose} autoFocus>
              Schließen
            </button>
          ) : (
            <>
              <p>
                {onShowFullChangelog ? (
                  <button
                    type="button"
                    className="whats-new-modal__link"
                    onClick={onShowFullChangelog}
                  >
                    Gesamtes Änderungsprotokoll anzeigen
                  </button>
                ) : (
                  "Diese Hinweise kannst du später über die Versionsnummer erneut öffnen."
                )}
              </p>
              <button type="button" className="primary-button" onClick={onClose} autoFocus>
                Weiter zu Face Manager
              </button>
            </>
          )}
        </footer>
      </section>
    </div>
  );
};

export default WhatsNewModal;
