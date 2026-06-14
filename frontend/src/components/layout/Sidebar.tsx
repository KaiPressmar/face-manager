import React from "react";
import ImportProgress from "../import/ImportProgress";

const Sidebar: React.FC<{
  page: "people" | "clusters" | "settings";
  onChangePage: (p: "people" | "clusters" | "settings") => void;
}> = ({ page, onChangePage }) => {
  return (
    <aside className="sidebar">
      <div className="sidebar__header">
        <div className="sidebar__eyebrow">Navigation</div>
        <div className="sidebar__title">Face Manager</div>
        <div className="sidebar__subtitle">Importe, Bilder und Cluster</div>
      </div>

      <nav className="sidebar__nav" aria-label="Hauptnavigation">
        <button
          className={
            page === "people" ? "neon-card neon-card--active" : "neon-card"
          }
          onClick={() => onChangePage("people")}
        >
          Bilder
        </button>

        <button
          className={
            page === "clusters" ? "neon-card neon-card--active" : "neon-card"
          }
          onClick={() => onChangePage("clusters")}
        >
          Cluster
        </button>

        <button
          className={
            page === "settings" ? "neon-card neon-card--active" : "neon-card"
          }
          onClick={() => onChangePage("settings")}
        >
          Einstellungen
        </button>
      </nav>

      <section className="sidebar__imports" aria-label="Importstatus">
        <ImportProgress />
      </section>
    </aside>
  );
};

export default Sidebar;
