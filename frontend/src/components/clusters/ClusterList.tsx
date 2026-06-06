import React from "react";

interface ClusterListProps {
  clusters: any[];
  selected: number | null;
  onSelect: (id: number) => void;
  isLoading: boolean; // 🔥 Neues Property
}

const ClusterList: React.FC<ClusterListProps> = ({ clusters, selected, onSelect, isLoading }) => {
  
  // 🔥 Während des Ladens pulsierende Sidebar-Einträge anzeigen
  if (isLoading) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {Array.from({ length: 6 }).map((_, idx) => (
          <div
            key={idx}
            className="skeleton-card"
            style={{
              height: "64px",
              width: "100%",
            }}
          />
        ))}
      </div>
    );
  }

  if (clusters.length === 0) {
    return <div style={{ opacity: 0.5, fontSize: 13 }}>Keine aktiven Cluster gefunden.</div>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {clusters.map((c) => {
        const isSelected = selected === c.cluster_id;

        return (
          <div
            key={c.cluster_id}
            className={`neon-card ${isSelected ? "neon-card--active" : ""}`}
            style={{
              width: "100%",
              cursor: "pointer",
              padding: "12px",
              borderColor: isSelected ? "var(--neon-magenta)" : "#1f1f22",
              boxShadow: isSelected ? "0 0 12px rgba(255, 0, 229, 0.25)" : "none",
            }}
            onClick={() => onSelect(c.cluster_id)}
          >
            <div style={{ fontSize: 14, fontWeight: "bold", marginBottom: 4 }}>
              Cluster {c.cluster_id}
            </div>
            <div style={{ opacity: 0.6, fontSize: 12, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              {c.person_name || "Unbekannt"}
            </div>
            <div style={{ fontSize: 11, color: "var(--neon-cyan)", marginTop: 4, opacity: 0.8 }}>
              {c.faces?.length || 0} Gesichter
            </div>
          </div>
        );
      })}
    </div>
  );
};

export default ClusterList;