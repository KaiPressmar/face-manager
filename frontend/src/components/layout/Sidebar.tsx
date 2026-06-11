import React from "react";
import ImportProgress from "../import/ImportProgress";

const Sidebar: React.FC<{
  page: "people" | "clusters";
  onChangePage: (p: "people" | "clusters") => void;
}> = ({ page, onChangePage }) => {
  return (
    <aside
      style={{
        width: 240,
        background: "#0b0b10",
        borderRight: "1px solid #1f1f22",
        padding: 16,
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div style={{ marginBottom: 16, color: "#9e9e9e", fontSize: 12 }}>
        Navigation
      </div>

      <button
        className={
          page === "people" ? "neon-card neon-card--active" : "neon-card"
        }
        style={{
          width: "100%",
          marginBottom: 12,
          cursor: "pointer",
          background: "none",
        }}
        onClick={() => onChangePage("people")}
      >
        Bilder & Personen
      </button>

      <button
        className={
          page === "clusters" ? "neon-card neon-card--active" : "neon-card"
        }
        style={{
          width: "100%",
          cursor: "pointer",
          background: "none",
        }}
        onClick={() => onChangePage("clusters")}
      >
        Cluster verwalten
      </button>

      <div style={{ marginTop: "auto", paddingTop: 16 }}>
        <ImportProgress />
      </div>
    </aside>
  );
};

export default Sidebar;
