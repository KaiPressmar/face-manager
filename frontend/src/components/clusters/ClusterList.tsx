import React, { useEffect, useMemo, useRef, useState } from "react";

import { ClusterSummary, FaceReviewGroupSummary } from "../../utils/api";

const UNKNOWN_PERSON_LABEL = "Noch zu prüfen";
const REVIEW_GROUP_LABELS: Record<string, string> = {
  unknown_person: "Unbekannte Personen",
  not_face: "Keine Gesichter",
};
const LIST_BATCH_SIZE = 100;

export interface ArchiveSectionSummary {
  key: string;
  label: string;
  faceCount: number;
}

interface ClusterListProps {
  mode: "open" | "people" | "archive";
  clusters: ClusterSummary[];
  reviewGroups: FaceReviewGroupSummary[];
  selected: number | null;
  selectedReviewGroupKey?: string | null;
  highlightedClusterId?: number | null;
  onSelect: (id: number) => void;
  onSelectReviewGroup: (groupKey: string) => void;
  /** Sub-groups of the selected archive group, mirroring the cluster lists. */
  archiveSections?: ArchiveSectionSummary[];
  activeArchiveSectionKey?: string | null;
  onSelectArchiveSection?: (sectionKey: string) => void;
  isLoading: boolean;
}

const ClusterList: React.FC<ClusterListProps> = ({
  mode,
  clusters,
  reviewGroups,
  selected,
  selectedReviewGroupKey = null,
  highlightedClusterId = null,
  onSelect,
  onSelectReviewGroup,
  archiveSections = [],
  activeArchiveSectionKey = null,
  onSelectArchiveSection,
  isLoading,
}) => {
  const safeClusters = Array.isArray(clusters) ? clusters : [];
  const clusterRefs = useRef<Record<number, HTMLDivElement | null>>({});
  const [openSectionKey, setOpenSectionKey] = useState<string | null>(null);
  const [expandedPersonNames, setExpandedPersonNames] = useState<Record<string, boolean>>({});
  const [searchTerm, setSearchTerm] = useState("");
  const [visibleOpenCount, setVisibleOpenCount] = useState(LIST_BATCH_SIZE);
  const [visiblePersonCount, setVisiblePersonCount] = useState(LIST_BATCH_SIZE);

  const selectedGroupName = useMemo(() => {
    if (selected === null) return null;
    const selectedCluster = safeClusters.find((cluster) => cluster.cluster_id === selected);
    return selectedCluster?.person_name?.trim() || UNKNOWN_PERSON_LABEL;
  }, [safeClusters, selected]);

  useEffect(() => {
    if (highlightedClusterId === null) return;
    const clusterNode = clusterRefs.current[highlightedClusterId];
    if (!clusterNode) return;
    clusterNode.scrollIntoView({
      behavior: "smooth",
      block: "center",
      inline: "nearest",
    });
  }, [highlightedClusterId, safeClusters]);

  // Keep the group of the selected cluster expanded so the active card stays
  // visible in the sidebar (people mode collapses persons by default).
  useEffect(() => {
    if (mode !== "people" || selected === null) return;
    const activeCluster = safeClusters.find((cluster) => cluster.cluster_id === selected);
    const personName = activeCluster?.person_name?.trim();
    if (!personName) return;
    setExpandedPersonNames((current) =>
      current[personName] ? current : { ...current, [personName]: true },
    );
  }, [mode, selected, safeClusters]);

  const normalizedSearch = searchTerm.trim().toLocaleLowerCase("de");
  const matchesSearch = (value: string) => {
    if (!normalizedSearch) return true;
    const normalizedValue = value.toLocaleLowerCase("de");
    return normalizedValue.includes(normalizedSearch);
  };

  const personClusterMap = safeClusters.reduce((groups, cluster) => {
    const personName = (cluster.person_name || "").trim();
    if (!personName) {
      return groups;
    }
    if (!groups[personName]) {
      groups[personName] = [];
    }
    groups[personName].push(cluster);
    return groups;
  }, {} as Record<string, ClusterSummary[]>);

  const personEntries = Object.keys(personClusterMap)
    .sort((a, b) => a.localeCompare(b, "de", { sensitivity: "base" }))
    .map((personName) => ({
      personName,
      faceCount: personClusterMap[personName].reduce(
        (sum, cluster) => sum + cluster.face_count,
        0,
      ),
      clusters: personClusterMap[personName].filter((cluster) => {
        const clusterLabel = `Gruppe ${cluster.cluster_id}`;
        const customClusterLabel = cluster.cluster_label?.trim() || "";
        return (
          matchesSearch(personName) ||
          matchesSearch(clusterLabel) ||
          matchesSearch(String(cluster.cluster_id)) ||
          (customClusterLabel ? matchesSearch(customClusterLabel) : false)
        );
      }),
    }))
    .filter((entry) => entry.clusters.length > 0);

  const unknownClusters = safeClusters.filter((cluster) => {
    if ((cluster.person_name || "").trim()) {
      return false;
    }
    const clusterLabel = `Gruppe ${cluster.cluster_id}`;
    const customClusterLabel = cluster.cluster_label?.trim() || "";
    return (
      matchesSearch(UNKNOWN_PERSON_LABEL) ||
      matchesSearch(clusterLabel) ||
      matchesSearch(String(cluster.cluster_id)) ||
      (customClusterLabel ? matchesSearch(customClusterLabel) : false)
    );
  });

  const allReviewEntries = reviewGroups
    .filter((group) => group.group_key !== "unassigned")
    .map((group) => ({
      ...group,
      sectionLabel: REVIEW_GROUP_LABELS[group.group_key] || group.label,
    }));
  // The overview buttons filter on the group label; the detail list below stays
  // reachable even when only a sub-group matches the search.
  const groupedReviewEntries = allReviewEntries.filter((group) =>
    matchesSearch(group.sectionLabel),
  );
  const matchingArchiveSections = archiveSections.filter((section) =>
    matchesSearch(section.label),
  );

  useEffect(() => {
    setVisibleOpenCount(LIST_BATCH_SIZE);
    setVisiblePersonCount(LIST_BATCH_SIZE);
  }, [mode, normalizedSearch]);

  const visibleReviewGroupCount = groupedReviewEntries.length;
  const visibleModeItemCount =
    mode === "open"
      ? unknownClusters.length
      : mode === "people"
        ? personEntries.length
        : groupedReviewEntries.length + matchingArchiveSections.length;
  const personSectionClusterCount = personEntries.reduce(
    (sum, entry) => sum + entry.clusters.length,
    0,
  );
  const personSectionFaceCount = personEntries.reduce(
    (sum, entry) => sum + entry.faceCount,
    0,
  );
  const unknownFaceCount = unknownClusters.reduce(
    (sum, cluster) => sum + cluster.face_count,
    0,
  );
  useEffect(() => {
    if (mode === "open") {
      setOpenSectionKey("unknown");
      return;
    }
    if (mode === "people") {
      setOpenSectionKey("persons");
      return;
    }
    if (mode === "archive") {
      setOpenSectionKey(selectedReviewGroupKey || "unknown_person");
    }
  }, [mode, selectedReviewGroupKey]);

  useEffect(() => {
    if (selectedGroupName && selectedGroupName !== UNKNOWN_PERSON_LABEL) {
      setExpandedPersonNames((current) =>
        current[selectedGroupName] ? current : { ...current, [selectedGroupName]: true },
      );
    }
  }, [selectedGroupName]);

  useEffect(() => {
    if (openSectionKey !== "persons") {
      return;
    }
    if (personEntries.length === 0) {
      setExpandedPersonNames({});
      return;
    }

    setExpandedPersonNames((current) => {
      const visiblePersonNames = new Set(personEntries.map((entry) => entry.personName));
      const keptEntries = Object.entries(current).filter(([personName]) =>
        visiblePersonNames.has(personName),
      );
      const removedHiddenPerson = keptEntries.length !== Object.entries(current).length;
      if (keptEntries.some(([, isExpanded]) => isExpanded)) {
        return removedHiddenPerson ? Object.fromEntries(keptEntries) : current;
      }
      return { ...Object.fromEntries(keptEntries), [personEntries[0].personName]: true };
    });
  }, [openSectionKey, personEntries]);

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

  const renderClusterCard = (cluster: ClusterSummary) => {
    const isSelected = selected === cluster.cluster_id;
    const displayLabel = cluster.cluster_label?.trim() || `Gesichtsgruppe ${cluster.cluster_id}`;
    return (
      <div
        key={cluster.cluster_id}
        className={`neon-card cluster-person-browser__cluster-card${isSelected ? " neon-card--active" : ""}`}
        ref={(node) => {
          clusterRefs.current[cluster.cluster_id] = node;
        }}
        style={{
          width: "100%",
          cursor: "pointer",
          padding: "10px",
          minHeight: "52px",
          position: "relative",
          overflow: "visible",
          textAlign: "left",
        }}
        onClick={() => onSelect(cluster.cluster_id)}
      >
        <div style={{ fontSize: 13, fontWeight: 700, color: isSelected ? "var(--text-strong)" : "var(--text)" }}>
          {displayLabel}
        </div>
        <div style={{ opacity: 0.62, fontSize: 12, marginTop: 2 }}>
          {`${cluster.face_count} Gesichter · Gruppe ${cluster.cluster_id}`}
        </div>
      </div>
    );
  };

  const renderCountSummary = (clusterCount: number, faceCount: number) => (
    <div className="cluster-nav-detail-title__meta">
      <span>{clusterCount} Gesichtsgruppen</span>
      <span>{faceCount} Gesichter</span>
    </div>
  );

  return (
    <div className="cluster-nav-shell">
      <section className="cluster-nav-search">
        <div className="cluster-nav-search__topline">
          <div>
            <span className="cluster-nav-search__eyebrow">Arbeitsliste</span>
            <strong className="cluster-nav-search__title">
              {mode === "open"
                ? "Neue Gesichtsgruppen"
                : mode === "people"
                  ? "Bestätigte Personen"
                  : "Aussortierte Gesichter"}
            </strong>
          </div>
        </div>
        <div className="cluster-nav-search__row">
          <label className="cluster-nav-search__field">
            <span className="cluster-nav-search__label">Suche</span>
            <input
              value={searchTerm}
              onChange={(event) => setSearchTerm(event.target.value)}
              placeholder={mode === "people" ? "Person suchen" : "Gesichtsgruppe suchen"}
              className="cluster-nav-search__input"
              type="search"
            />
          </label>

        </div>

        <div className="cluster-nav-search__meta">
          {mode === "open" && <span>{unknownClusters.length} offene Gesichtsgruppen</span>}
          {mode === "people" && <span>{personEntries.length} Personen</span>}
          {mode === "archive" && <span>{visibleReviewGroupCount} Kategorien</span>}
        </div>
      </section>

      {mode === "archive" && <section className="cluster-nav-overview cluster-nav-overview--archive">
        {groupedReviewEntries.map((group) => (
          <button
            key={group.group_key}
            type="button"
            className={`cluster-nav-overview__item${openSectionKey === group.group_key ? " cluster-nav-overview__item--active" : ""}`}
            onClick={() => {
              setOpenSectionKey(group.group_key);
              onSelectReviewGroup(group.group_key);
            }}
        >
          <span>{group.sectionLabel}</span>
          <strong>{group.cluster_count}</strong>
        </button>
      ))}
      </section>}

      {mode === "people" && (
        <section className="cluster-nav-section cluster-nav-section--detail">
          <div className="cluster-nav-section__body cluster-nav-section__body--person-browser">
            {personEntries.length > 0 ? (
              <div className="cluster-person-tree">
                <div className="cluster-person-browser__summary">
                  <strong>Personen</strong>
                  {renderCountSummary(personSectionClusterCount, personSectionFaceCount)}
                </div>
                <div className="cluster-person-tree__list" role="tree" aria-label="Personen und Gesichtsgruppen">
                  {personEntries.slice(0, visiblePersonCount).map(({ personName, clusters, faceCount }) => {
                    const isExpanded = expandedPersonNames[personName] ?? false;
                    const isActivePerson =
                      selectedGroupName === personName && selectedReviewGroupKey === null;
                    return (
                      <div key={personName} className="cluster-person-tree__item" role="treeitem" aria-expanded={isExpanded}>
                        <button
                          type="button"
                          className={`cluster-person-browser__person${isActivePerson ? " cluster-person-browser__person--active" : ""}`}
                          onClick={() => {
                            const shouldExpand = !isExpanded;
                            setExpandedPersonNames((current) => ({
                              ...current,
                              [personName]: shouldExpand,
                            }));
                          }}
                        >
                          <span className="cluster-person-tree__person-content">
                            <span
                              className={`cluster-person-tree__chevron${isExpanded ? " cluster-person-tree__chevron--expanded" : ""}`}
                              aria-hidden="true"
                            >
                              ›
                            </span>
                            <span>
                              <span className="cluster-person-browser__person-name">{personName}</span>
                              <span className="cluster-person-browser__person-meta">
                                {clusters.length} Gesichtsgruppen · {faceCount} Gesichter
                              </span>
                            </span>
                          </span>
                        </button>
                        {isExpanded && (
                          <div className="cluster-person-tree__clusters" role="group">
                            {clusters.map((cluster) => renderClusterCard(cluster))}
                          </div>
                        )}
                      </div>
                    );
                  })}
                  {visiblePersonCount < personEntries.length && (
                    <button className="cluster-nav-load-more" onClick={() => setVisiblePersonCount((count) => count + LIST_BATCH_SIZE)} type="button">
                      Weitere {Math.min(LIST_BATCH_SIZE, personEntries.length - visiblePersonCount)} Personen
                    </button>
                  )}
                </div>
              </div>
            ) : (
              <div className="cluster-nav-empty-state">Keine Personen zur Auswahl.</div>
            )}
          </div>
        </section>
      )}

      {mode === "open" && (
        <section className="cluster-nav-section cluster-nav-section--detail">
          <div className="cluster-nav-section__body cluster-nav-section__body--scroll">
            <div className="cluster-nav-detail-title">
              <strong>{UNKNOWN_PERSON_LABEL}</strong>
              {renderCountSummary(unknownClusters.length, unknownFaceCount)}
            </div>
            {unknownClusters.slice(0, visibleOpenCount).map((cluster) => renderClusterCard(cluster))}
            {visibleOpenCount < unknownClusters.length && (
              <button className="cluster-nav-load-more" onClick={() => setVisibleOpenCount((count) => count + LIST_BATCH_SIZE)} type="button">
                Weitere {Math.min(LIST_BATCH_SIZE, unknownClusters.length - visibleOpenCount)} Gesichtsgruppen
              </button>
            )}
          </div>
        </section>
      )}

      {allReviewEntries
        .filter((group) => mode === "archive" && openSectionKey === group.group_key)
        .map((group) => (
          <section key={group.group_key} className="cluster-nav-section cluster-nav-section--detail">
            <div className="cluster-nav-section__body cluster-nav-section__body--scroll">
              <div className="cluster-nav-detail-title">
                <strong>{group.sectionLabel}</strong>
                {renderCountSummary(group.cluster_count, group.face_count)}
              </div>
              {matchingArchiveSections.length > 0 ? (
                matchingArchiveSections
                  .map((section) => {
                    const isActiveSection = activeArchiveSectionKey === section.key;
                    return (
                      <button
                        key={section.key}
                        type="button"
                        className={`neon-card${isActiveSection ? " neon-card--active" : ""}`}
                        onClick={() => onSelectArchiveSection?.(section.key)}
                        style={{
                          width: "100%",
                          cursor: "pointer",
                          padding: "10px",
                          minHeight: "52px",
                          textAlign: "left",
                          display: "flex",
                          justifyContent: "space-between",
                          alignItems: "center",
                          gap: 8,
                        }}
                      >
                        <span style={{ fontSize: 13, fontWeight: 700, minWidth: 0 }}>
                          {section.label}
                        </span>
                        <span
                          style={{
                            fontSize: 10,
                            fontWeight: 700,
                            color: "var(--on-accent)",
                            background: "var(--neon-cyan)",
                            borderRadius: 999,
                            padding: "2px 8px",
                            flexShrink: 0,
                          }}
                        >
                          {section.faceCount}
                        </span>
                      </button>
                    );
                  })
              ) : (
                <div className="cluster-nav-empty-state">
                  {isLoading
                    ? "Gesichtsgruppen werden geladen…"
                    : normalizedSearch
                      ? "Keine passende Gesichtsgruppe gefunden."
                      : "Keine Gesichtsgruppen in diesem Bereich."}
                </div>
              )}
            </div>
          </section>
        ))}

      {normalizedSearch && visibleModeItemCount === 0 && (
        <div className="cluster-nav-empty-state">
          Keine Treffer für <strong>{searchTerm}</strong>.
        </div>
      )}
    </div>
  );
};

export default ClusterList;
