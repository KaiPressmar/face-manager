import React, { useEffect, useMemo, useRef, useState } from "react";
import { ClusterSummary } from "../../utils/api";

const UNKNOWN_PERSON_LABEL = "Unbekannt";

interface ClusterListProps {
  clusters: ClusterSummary[];
  selected: number | null;
  highlightedClusterId?: number | null;
  onSelect: (id: number) => void;
  isLoading: boolean;
}

const ClusterList: React.FC<ClusterListProps> = ({
  clusters,
  selected,
  highlightedClusterId = null,
  onSelect,
  isLoading,
}) => {
  const safeClusters = Array.isArray(clusters) ? clusters : [];
  const clusterRefs = useRef<Record<number, HTMLDivElement | null>>({});
  const lastScrolledClusterIdRef = useRef<number | null>(null);
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});

  const selectedGroupName = useMemo(() => {
    if (selected === null) return null;
    const selectedCluster = safeClusters.find((cluster) => cluster.cluster_id === selected);
    return selectedCluster?.person_name?.trim() || UNKNOWN_PERSON_LABEL;
  }, [safeClusters, selected]);

  useEffect(() => {
    setExpandedGroups((current) => {
      const next = { ...current };
      let changed = false;

      Object.keys(next).forEach((groupName) => {
        if (!safeClusters.some((cluster) => {
          const currentGroupName = cluster.person_name?.trim() || UNKNOWN_PERSON_LABEL;
          return currentGroupName === groupName;
        })) {
          delete next[groupName];
          changed = true;
        }
      });

      return changed ? next : current;
    });
  }, [safeClusters]);

  useEffect(() => {
    if (selected === null) return;
    const clusterNode = clusterRefs.current[selected];
    if (!clusterNode) return;

    if (
      lastScrolledClusterIdRef.current === selected &&
      highlightedClusterId !== selected
    ) {
      return;
    }

    lastScrolledClusterIdRef.current = selected;
    clusterNode.scrollIntoView({
      behavior: "smooth",
      block: "center",
      inline: "nearest",
    });
  }, [expandedGroups, highlightedClusterId, safeClusters, selected]);

  useEffect(() => {
    if (!selectedGroupName) return;
    setExpandedGroups((current) => {
      if (current[selectedGroupName]) {
        return current;
      }
      return {
        ...current,
        [selectedGroupName]: true,
      };
    });
  }, [selectedGroupName]);

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
        const isExpanded = expandedGroups[groupName] ?? false;
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
            <button
              type="button"
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                width: "100%",
                padding: "8px 10px",
                borderBottom: "1px solid #23232b",
                background: "rgba(0, 229, 255, 0.04)",
                borderLeft: 0,
                borderRight: 0,
                borderTop: 0,
                color: "inherit",
                cursor: "pointer",
              }}
              onClick={() =>
                setExpandedGroups((current) => ({
                  ...current,
                  [groupName]: !isExpanded,
                }))
              }
              aria-expanded={isExpanded}
            >
              <span style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
                <span
                  aria-hidden="true"
                  style={{
                    color: "var(--neon-cyan)",
                    fontSize: 12,
                    transform: isExpanded ? "rotate(90deg)" : "rotate(0deg)",
                    transition: "transform 0.18s ease",
                  }}
                >
                  ›
                </span>
                <strong
                  style={{
                    fontSize: 12,
                    color: "#d5d5dc",
                    letterSpacing: "0.02em",
                    textAlign: "left",
                  }}
                >
                  {groupName}
                </strong>
              </span>
              <span
                style={{
                  fontSize: 10,
                  fontWeight: 700,
                  color: "#081013",
                  background: "var(--neon-cyan)",
                  borderRadius: 999,
                  padding: "2px 8px",
                  flexShrink: 0,
                }}
              >
                {entries.length}
              </span>
            </button>

            {isExpanded && (
              <div style={{ display: "flex", flexDirection: "column", gap: 8, padding: 8 }}>
                {entries.map((c) => {
                  const isSelected = selected === c.cluster_id;

                  return (
                    <div
                      key={c.cluster_id}
                      className={`neon-card ${isSelected ? "neon-card--active" : ""}`}
                      ref={(node) => {
                        clusterRefs.current[c.cluster_id] = node;
                      }}
                      style={{
                        width: "100%",
                        cursor: "pointer",
                        padding: "10px",
                        borderColor: isSelected ? "var(--neon-magenta)" : "#2a2a33",
                        boxShadow: isSelected ? "0 0 14px rgba(255, 0, 229, 0.22)" : "none",
                        position: "relative",
                        overflow: "hidden",
                      }}
                      onClick={() => onSelect(c.cluster_id)}
                    >
                      {highlightedClusterId === c.cluster_id && (
                        <span className="cluster-jump-badge" aria-hidden="true">
                          Jump target
                        </span>
                      )}
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
            )}
          </section>
        );
      })}
    </div>
  );
};

export default ClusterList;
