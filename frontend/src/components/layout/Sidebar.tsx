import React, { useEffect, useRef, useState } from "react";
import type { AppPage } from "../../App";
import { PRIMARY_ITEMS, SECONDARY_ITEMS } from "./workspaceDestinations";

type NavItem = (typeof PRIMARY_ITEMS)[number] | (typeof SECONDARY_ITEMS)[number];

const Sidebar: React.FC<{
  page: AppPage;
  onChangePage: (p: AppPage) => void;
  onShowReleaseNotes?: () => void;
  onShowUpdate?: () => void;
}> = ({ page, onChangePage, onShowReleaseNotes, onShowUpdate }) => {
  const [moreOpen, setMoreOpen] = useState(false);
  const moreRef = useRef<HTMLButtonElement>(null);
  const secondaryRef = useRef<HTMLDivElement>(null);
  const secondaryPage = SECONDARY_ITEMS.find((item) => item.page === page);

  useEffect(() => {
    if (!moreOpen) return;
    const onPointerDown = (event: PointerEvent) => {
      if (event.target instanceof Node && !secondaryRef.current?.contains(event.target)) {
        setMoreOpen(false);
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [moreOpen]);

  const openPage = (item: NavItem) => {
    if (moreOpen && SECONDARY_ITEMS.includes(item)) moreRef.current?.focus();
    setMoreOpen(false);
    onChangePage(item.page);
  };

  const renderItem = (item: NavItem) => (
    <button
      key={item.page}
      type="button"
      className={`neon-card${page === item.page ? " neon-card--active" : ""}`}
      aria-current={page === item.page ? "page" : undefined}
      aria-label={item.label}
      onClick={() => openPage(item)}
    >
      <span aria-hidden="true">{item.icon}</span>
      <span>
        <strong>{item.label}</strong>
        <small>{item.hint}</small>
      </span>
    </button>
  );

  return (
    <aside className={`sidebar workflow-sidebar${moreOpen ? " workflow-sidebar--expanded" : ""}`}>
      <div className="sidebar__header">
        <img
          className="sidebar__brand-mark"
          src="/favicon.svg"
          alt=""
          width={36}
          height={36}
          aria-hidden="true"
        />
        <div className="sidebar__brand-copy">
          <div className="sidebar__brand-line">
            <div className="sidebar__title">Face Manager</div>
            {onShowReleaseNotes ? (
              <button
                type="button"
                className="sidebar__version"
                title="Änderungsprotokoll anzeigen"
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
            <span className="sidebar__subtitle">Fotos sammeln und wiederfinden</span>
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

      <nav className="sidebar__nav workflow-sidebar__nav" aria-label="Hauptnavigation">
        <div className="sidebar__section-label">Bibliothek</div>
        {PRIMARY_ITEMS.map(renderItem)}
        <div
          className="workflow-sidebar__secondary"
          ref={secondaryRef}
          onKeyDown={(event) => {
            if (!moreOpen || event.key !== "Escape") return;
            event.preventDefault();
            setMoreOpen(false);
            moreRef.current?.focus();
          }}
        >
          <button
            ref={moreRef}
            type="button"
            className={`neon-card workflow-sidebar__more${secondaryPage ? " neon-card--active" : ""}`}
            aria-expanded={moreOpen}
            aria-controls="additional-navigation"
            aria-label={secondaryPage ? `Mehr, aktuelle Ansicht: ${secondaryPage.label}` : "Mehr"}
            onClick={() => setMoreOpen((open) => !open)}
          >
            <span aria-hidden="true">···</span><strong>Mehr</strong>
          </button>
          <div id="additional-navigation" className="workflow-sidebar__additional">
            <div className="sidebar__section-label">Werkzeuge</div>
            {SECONDARY_ITEMS.map(renderItem)}
          </div>
        </div>
      </nav>
    </aside>
  );
};

export default Sidebar;
