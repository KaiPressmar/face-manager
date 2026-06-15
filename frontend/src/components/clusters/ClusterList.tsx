import React from "react";
import { ClusterSummary } from "../../utils/api";

const UNKNOWN_PERSON_LABEL = "Unbekannt";

interface ClusterListProps {
  clusters: ClusterSummary[];
  selected: number | null;
  onSelect: (id: number) => void;
  isLoading: boolean;
}

const ClusterList: React.FC<ClusterListProps> = ({
  clusters,
  selected,
  onSelect,
  isLoading,
}) => {
  const safeClusters = Array.isArray(clusters) ? clusters : [];

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

  if (safeClusters.length === 0) {
    return <div style={{ opacity: 0.5, fontSize: 13 }}>Keine aktiven Cluster gefunden.</div>;
  }

  const groupedClusters = safeClusters.reduce((groups, cluster) => {
    const groupName = cluster.person_name?.trim() || UNKNOWN_PERSON_LABEL;
    if (!groups[groupName]) {
      groups[groupName] = [];
    }
    groups[groupName].push(cluster);
    return groups;
  }, {} as Record<string, ClusterSummary[]>);

  const groupOrder = Object.keys(groupedClusters).sort((a, b) => {
    if (a === UNKNOWN_PERSON_LABEL) return 1;
    if (b === UNKNOWN_PERSON_LABEL) return -1;
    return a.localeCompare(b, "de", { sensitivity: "base" });
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {groupOrder.map((groupName) => {
        const entries = groupedClusters[groupName];
        return (
          <section
            key={groupName}
            style={{
              border: "1px solid #1f1f27",
              borderRadius: 10,
              background: "linear-gradient(180deg, rgba(21, 21, 28, 0.95), rgba(13, 13, 18, 0.95))",
              overflow: "hidden",
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                padding: "8px 10px",
                borderBottom: "1px solid #23232b",
                background: "rgba(0, 229, 255, 0.04)",
              }}
            >
              <strong style={{ fontSize: 12, color: "#d5d5dc", letterSpacing: "0.02em" }}>
                {groupName}
              </strong>
              <span
                style={{
                  fontSize: 10,
                  fontWeight: 700,
                  color: "#081013",
                  background: "var(--neon-cyan)",
                  borderRadius: 999,
                  padding: "2px 8px",
                }}
              >
                {entries.length}
              </span>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 8, padding: 8 }}>
              {entries.map((c) => {
                const isSelected = selected === c.cluster_id;

                return (
                  <div
                    key={c.cluster_id}
                    className={`neon-card ${isSelected ? "neon-card--active" : ""}`}
                    style={{
                      width: "100%",
                      cursor: "pointer",
                      padding: "10px",
                      borderColor: isSelected ? "var(--neon-magenta)" : "#2a2a33",
                      boxShadow: isSelected ? "0 0 14px rgba(255, 0, 229, 0.22)" : "none",
                    }}
                    onClick={() => onSelect(c.cluster_id)}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                      <div style={{ fontSize: 13, fontWeight: 700 }}>Cluster {c.cluster_id}</div>
                      <span
                        style={{
                          fontSize: 10,
                          color: "var(--neon-cyan)",
                          border: "1px solid rgba(0, 229, 255, 0.35)",
                          borderRadius: 999,
                          padding: "2px 6px",
                        }}
                      >
                        {c.face_count} Gesichter
                      </span>
                    </div>
                    <div
                      style={{
                        opacity: 0.7,
                        fontSize: 11,
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                    >
                      {groupName === UNKNOWN_PERSON_LABEL ? "Nicht zugewiesen" : "Zugewiesen"}
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        );
      })}
    </div>
  );
};

export default ClusterList;
