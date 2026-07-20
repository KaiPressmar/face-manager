import React from "react";
import type { AppPage } from "../../App";

interface NavItem {
  page: AppPage;
  icon: string;
  label: string;
  hint: string;
}

/**
 * The library is what the app is for and what stays relevant once everything
 * is imported. Setting up and correcting is a phase, so it stays easy to reach
 * without competing for the top spot.
 */
const NAV_GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: "Bibliothek",
    items: [
      { page: "people", icon: "▦", label: "Bilder", hint: "Fotos durchsuchen und filtern" },
      {
        page: "renaming",
        icon: "Aa",
        label: "Dateinamen",
        hint: "Erkannte Personen ergänzen",
      },
    ],
  },
  {
    label: "Verwalten",
    items: [
      {
        page: "review",
        icon: "◎",
        label: "Gesichter prüfen",
        hint: "Vorschläge prüfen und korrigieren",
      },
      {
        page: "settings",
        icon: "⚙",
        label: "Einstellungen",
        hint: "Face Manager verwalten",
      },
    ],
  },
];

const Sidebar: React.FC<{
  page: AppPage;
  onChangePage: (p: AppPage) => void;
  onShowReleaseNotes?: () => void;
  onShowUpdate?: () => void;
}> = ({ page, onChangePage, onShowReleaseNotes, onShowUpdate }) => {
  return (
    <aside className="sidebar">
      <div className="sidebar__header">
        <div className="sidebar__brand-mark" aria-hidden="true">FM</div>
        <div className="sidebar__brand-copy">
          <div className="sidebar__brand-line">
            <div className="sidebar__title">Face Manager</div>
            {onShowReleaseNotes ? (
              <button
                type="button"
                className="sidebar__version"
                title="Versionshinweise anzeigen"
                onClick={onShowReleaseNotes}
              >
                v{__APP_VERSION__}
              </button>
            ) : (
              <span className="sidebar__version" title="Installierte Version">
                v{__APP_VERSION__}
              </span>
            )}
          </div>
          <div className="sidebar__brand-meta">
            <span className="sidebar__subtitle">Fotos nach Personen finden</span>
            {onShowUpdate && (
              <button
                type="button"
                className="sidebar__update-button"
                onClick={onShowUpdate}
                title="Verfügbares Update anzeigen"
              >
                <span aria-hidden="true" />
                Update
              </button>
            )}
          </div>
        </div>
      </div>

      <nav className="sidebar__nav" aria-label="Hauptnavigation">
        {NAV_GROUPS.map((group) => (
          <React.Fragment key={group.label}>
            <div className="sidebar__section-label">{group.label}</div>
            {group.items.map((item) => (
              <button
                key={item.page}
                type="button"
                className={
                  page === item.page ? "neon-card neon-card--active" : "neon-card"
                }
                aria-current={page === item.page ? "page" : undefined}
                onClick={() => onChangePage(item.page)}
              >
                <span aria-hidden="true">{item.icon}</span>
                <span>
                  <strong>{item.label}</strong>
                  <small>{item.hint}</small>
                </span>
              </button>
            ))}
          </React.Fragment>
        ))}
      </nav>
    </aside>
  );
};

export default Sidebar;
