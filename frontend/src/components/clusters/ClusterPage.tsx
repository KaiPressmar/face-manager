import React, { useEffect, useMemo, useRef, useState } from "react";
import type { ClusterNavigationTarget } from "../../App";
import {
  assignClusterToPerson,
  ClusterDetails,
  ClusterSummary,
  dissolveCluster,
  fetchClusterFaces,
  fetchClusters,
  listPersons,
  removeFaceFromCluster,
} from "../../utils/api";
import ClusterList from "./ClusterList";
import ClusterFacesGrid from "./ClusterFacesGrid";

const UNKNOWN_PERSON_LABEL = "Unbekannt";
const CLUSTER_POLL_INTERVAL_MS = 15_000;

interface PersonOption {
  id: number;
  name: string;
}

interface ClusterPageProps {
  navigationTarget: ClusterNavigationTarget | null;
}

const ClusterPage: React.FC<ClusterPageProps> = ({ navigationTarget }) => {
  const [clusters, setClusters] = useState<ClusterSummary[]>([]);
  const [selectedClusterId, setSelectedClusterId] = useState<number | null>(null);
  const [clusterDetails, setClusterDetails] = useState<ClusterDetails | null>(null);
  const [persons, setPersons] = useState<PersonOption[]>([]);
  const [selectedPerson, setSelectedPerson] = useState("");
  const [newPersonName, setNewPersonName] = useState("");
  const [assignmentMode, setAssignmentMode] = useState<"existing" | "new">("existing");
  const [isListLoading, setIsListLoading] = useState(true);
  const [isDetailsLoading, setIsDetailsLoading] = useState(false);
  const [isMutating, setIsMutating] = useState(false);
  const [highlightedClusterId, setHighlightedClusterId] = useState<number | null>(null);
  const listRequestIdRef = useRef(0);
  const detailsRequestIdRef = useRef(0);
  const pendingNavigationClusterIdRef = useRef<number | null>(null);
  const headerRef = useRef<HTMLDivElement | null>(null);

  const loadClusterDetails = React.useCallback((clusterId: number) => {
    const requestId = detailsRequestIdRef.current + 1;
    detailsRequestIdRef.current = requestId;
    setClusterDetails((current) =>
      current && current.cluster_id === clusterId ? current : null,
    );
    setIsDetailsLoading(true);

    void fetchClusterFaces(clusterId)
      .then((data) => {
        if (detailsRequestIdRef.current !== requestId) {
          return;
        }
        setClusterDetails(
          data
            ? {
                ...data,
                faces: Array.isArray(data.faces) ? data.faces : [],
              }
            : null,
        );
      })
      .catch((error) => {
        if (detailsRequestIdRef.current === requestId) {
          setClusterDetails(null);
        }
        console.error("Fehler beim Laden der Clusterdetails:", error);
      })
      .finally(() => {
        if (detailsRequestIdRef.current === requestId) {
          setIsDetailsLoading(false);
        }
      });
  }, []);

  const selectedSummary = useMemo(
    () => clusters.find((cluster) => cluster.cluster_id === selectedClusterId) || null,
    [clusters, selectedClusterId],
  );

  useEffect(() => {
    let isMounted = true;
    listPersons()
      .then((data) => {
        if (isMounted) {
          setPersons(data);
        }
      })
      .catch((error) => {
        console.error("Fehler beim Laden der Personenliste:", error);
      });
    return () => {
      isMounted = false;
    };
  }, []);

  useEffect(() => {
    let isMounted = true;

    const applyClusterSelection = (nextClusters: ClusterSummary[]) => {
      if (nextClusters.length === 0) {
        setSelectedClusterId(null);
        setClusterDetails(null);
        return;
      }

      setSelectedClusterId((current) => {
        const pendingClusterId = pendingNavigationClusterIdRef.current;
        if (
          pendingClusterId !== null &&
          nextClusters.some((cluster) => cluster.cluster_id === pendingClusterId)
        ) {
          return pendingClusterId;
        }
        if (current !== null && nextClusters.some((cluster) => cluster.cluster_id === current)) {
          return current;
        }
        return nextClusters[0].cluster_id;
      });
    };

      const loadClusterSummaries = async (background = false) => {
      const requestId = listRequestIdRef.current + 1;
      listRequestIdRef.current = requestId;
      if (!background) {
        setIsListLoading(true);
      }

      try {
        const data = await fetchClusters();
        const safeData = Array.isArray(data) ? data : [];
        if (!isMounted || listRequestIdRef.current !== requestId) {
          return;
        }
        setClusters(safeData);
        applyClusterSelection(safeData);
      } catch (error) {
        console.error("Fehler beim Laden der Clusterliste:", error);
      } finally {
        if (isMounted && listRequestIdRef.current === requestId) {
          setIsListLoading(false);
        }
      }
    };

    const maybeRefreshVisibleData = () => {
      if (document.visibilityState !== "visible") {
        return;
      }
      void loadClusterSummaries(true);
    };

    void loadClusterSummaries();

    const interval = window.setInterval(() => {
      if (document.visibilityState !== "visible" || isMutating) {
        return;
      }
      void loadClusterSummaries(true);
    }, CLUSTER_POLL_INTERVAL_MS);

    document.addEventListener("visibilitychange", maybeRefreshVisibleData);
    window.addEventListener("focus", maybeRefreshVisibleData);

    return () => {
      isMounted = false;
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", maybeRefreshVisibleData);
      window.removeEventListener("focus", maybeRefreshVisibleData);
    };
  }, [isMutating]);

  useEffect(() => {
    if (selectedClusterId === null) {
      setClusterDetails(null);
      setIsDetailsLoading(false);
      return;
    }

    loadClusterDetails(selectedClusterId);
  }, [loadClusterDetails, selectedClusterId]);

  useEffect(() => {
    if (selectedSummary === null) {
      if (clusterDetails !== null) {
        setClusterDetails(null);
      }
      return;
    }

    if (clusterDetails === null || clusterDetails.cluster_id !== selectedSummary.cluster_id) {
      return;
    }

    if (
      clusterDetails.face_count !== selectedSummary.face_count ||
      clusterDetails.person_name !== selectedSummary.person_name
    ) {
      loadClusterDetails(selectedSummary.cluster_id);
    }
  }, [clusterDetails, loadClusterDetails, selectedSummary]);

  useEffect(() => {
    if (selectedSummary === null || isDetailsLoading) {
      return;
    }

    const detailsMatchSelection =
      clusterDetails !== null && clusterDetails.cluster_id === selectedSummary.cluster_id;
    if (detailsMatchSelection) {
      return;
    }

    loadClusterDetails(selectedSummary.cluster_id);
  }, [clusterDetails, isDetailsLoading, loadClusterDetails, selectedSummary]);

  useEffect(() => {
    if (!navigationTarget) return;

    pendingNavigationClusterIdRef.current = navigationTarget.clusterId;
    setSelectedClusterId(navigationTarget.clusterId);
    setHighlightedClusterId(navigationTarget.clusterId);

    const timeoutId = window.setTimeout(() => {
      setHighlightedClusterId((current) =>
        current === navigationTarget.clusterId ? null : current,
      );
    }, 2600);

    return () => {
      window.clearTimeout(timeoutId);
    };
  }, [navigationTarget]);

  useEffect(() => {
    const pendingClusterId = pendingNavigationClusterIdRef.current;
    if (pendingClusterId === null) {
      return;
    }

    const targetExists = clusters.some((cluster) => cluster.cluster_id === pendingClusterId);
    if (!targetExists) {
      return;
    }

    setSelectedClusterId(pendingClusterId);
  }, [clusters, selectedClusterId]);

  useEffect(() => {
    const pendingClusterId = pendingNavigationClusterIdRef.current;
    if (pendingClusterId === null) {
      return;
    }

    const targetReady =
      selectedSummary !== null && selectedSummary.cluster_id === pendingClusterId;
    if (!targetReady) {
      return;
    }

    pendingNavigationClusterIdRef.current = null;
    window.requestAnimationFrame(() => {
      headerRef.current?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    });
  }, [selectedSummary]);

  const handleRemoveFace = async (faceId: number) => {
    if (selectedClusterId === null || clusterDetails === null || isMutating) {
      return;
    }

    const nextFaceCount = Math.max(0, clusterDetails.face_count - 1);
    const remainingFaces = clusterDetails.faces.filter((face) => face.id !== faceId);
    const nextClusterId =
      nextFaceCount === 0
        ? clusters.find((cluster) => cluster.cluster_id !== selectedClusterId)?.cluster_id || null
        : selectedClusterId;

    setIsMutating(true);
    try {
      await removeFaceFromCluster(selectedClusterId, faceId);
      if (nextFaceCount === 0) {
        setClusters((current) =>
          current.filter((cluster) => cluster.cluster_id !== selectedClusterId),
        );
        setSelectedClusterId(nextClusterId);
        setClusterDetails(null);
      } else {
        setClusters((current) =>
          current.map((cluster) =>
            cluster.cluster_id === selectedClusterId
              ? { ...cluster, face_count: nextFaceCount }
              : cluster,
          ),
        );
        setClusterDetails((current) =>
          current && current.cluster_id === selectedClusterId
            ? {
                ...current,
                face_count: nextFaceCount,
                faces: remainingFaces,
              }
            : current,
        );
      }
    } catch (error) {
      console.error("Fehler beim Entfernen des Gesichts:", error);
    } finally {
      setIsMutating(false);
    }
  };

  const handleDissolve = async () => {
    if (selectedClusterId === null || isMutating) {
      return;
    }

    const nextClusterId =
      clusters.find((cluster) => cluster.cluster_id !== selectedClusterId)?.cluster_id || null;

    setIsMutating(true);
    try {
      await dissolveCluster(selectedClusterId);
      setClusters((current) =>
        current.filter((cluster) => cluster.cluster_id !== selectedClusterId),
      );
      setSelectedClusterId(nextClusterId);
      setClusterDetails(null);
    } catch (error) {
      console.error("Fehler beim Auflösen des Clusters:", error);
    } finally {
      setIsMutating(false);
    }
  };

  const handleAssign = async () => {
    if (selectedClusterId === null || isMutating) {
      return;
    }

    const name = assignmentMode === "new" ? newPersonName.trim() : selectedPerson;
    if (!name) {
      return;
    }

    setIsMutating(true);
    try {
      await assignClusterToPerson(selectedClusterId, name);
      setClusters((current) =>
        current.map((cluster) =>
          cluster.cluster_id === selectedClusterId
            ? { ...cluster, person_name: name }
            : cluster,
        ),
      );
      setClusterDetails((current) =>
        current && current.cluster_id === selectedClusterId
          ? { ...current, person_name: name }
          : current,
      );

      if (assignmentMode === "new") {
        const nextPersons = await listPersons();
        setPersons(nextPersons);
      }

      setNewPersonName("");
      setSelectedPerson("");
    } catch (error) {
      console.error("Fehler bei der Zuweisung:", error);
    } finally {
      setIsMutating(false);
    }
  };

  const currentCluster =
    clusterDetails && clusterDetails.cluster_id === selectedClusterId
      ? clusterDetails
      : null;
  const currentClusterSummary = currentCluster || selectedSummary;
  const currentFaces = Array.isArray(currentCluster?.faces) ? currentCluster.faces : [];

  return (
    <div style={{ display: "flex", gap: 24, height: "100%" }}>
      <div
        style={{
          width: 280,
          minWidth: 280,
          borderRight: "1px solid #222",
          paddingRight: 16,
          overflowY: "auto",
        }}
      >
        <h3 style={{ marginTop: 0, color: "var(--neon-magenta)" }}>Menschliche Cluster</h3>
        <ClusterList
          clusters={clusters}
          selected={selectedClusterId}
          highlightedClusterId={highlightedClusterId}
          onSelect={setSelectedClusterId}
          isLoading={isListLoading}
        />
      </div>

      <div style={{ flex: 1, overflowY: "auto" }}>
        {isListLoading && !currentClusterSummary ? (
          <div style={{ opacity: 0.5, paddingTop: 40, textAlign: "center" }}>Geladen wird...</div>
        ) : currentClusterSummary ? (
          <>
            <div
              ref={headerRef}
              className={
                highlightedClusterId === currentClusterSummary.cluster_id
                  ? "cluster-hero cluster-hero--highlighted"
                  : "cluster-hero"
              }
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "flex-start",
                marginBottom: 24,
              }}
            >
              <div>
                <h2 style={{ margin: 0 }}>Cluster {currentClusterSummary.cluster_id}</h2>
                <p style={{ opacity: 0.5, margin: "4px 0 0 0" }}>
                  Zugeordnet zu:{" "}
                  <strong style={{ color: "var(--neon-cyan)" }}>
                    {currentClusterSummary.person_name || `Niemand (${UNKNOWN_PERSON_LABEL})`}
                  </strong>
                </p>
                <p style={{ opacity: 0.5, margin: "4px 0 0 0" }}>
                  {currentClusterSummary.face_count} Gesichter
                </p>
              </div>

              <div style={{ display: "flex", gap: 12 }}>
                <button
                  className="neon-card"
                  style={{
                    borderColor: "#ff0055",
                    color: "#ff0055",
                    cursor: isMutating ? "wait" : "pointer",
                    fontWeight: "bold",
                    opacity: isMutating ? 0.6 : 1,
                  }}
                  disabled={isMutating}
                  onClick={handleDissolve}
                >
                  Cluster auflösen
                </button>
              </div>
            </div>

            <div
              className="neon-card"
              style={{
                marginBottom: 24,
                padding: 20,
                background: "#0b0b0e",
                borderColor: "#222",
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  marginBottom: 16,
                  borderBottom: "1px solid #1a1a20",
                  paddingBottom: 10,
                }}
              >
                <h4
                  style={{
                    margin: 0,
                    fontSize: 14,
                    letterSpacing: "0.5px",
                    textTransform: "uppercase",
                    color: "#aaa",
                  }}
                >
                  Identität zuweisen
                </h4>

                <div
                  style={{
                    display: "flex",
                    background: "#050507",
                    padding: 3,
                    borderRadius: 6,
                    border: "1px solid #222",
                  }}
                >
                  <button
                    onClick={() => setAssignmentMode("existing")}
                    style={{
                      background:
                        assignmentMode === "existing" ? "var(--neon-cyan)" : "transparent",
                      color: assignmentMode === "existing" ? "#000" : "#888",
                      border: "none",
                      padding: "6px 12px",
                      borderRadius: 4,
                      cursor: "pointer",
                      fontSize: 12,
                      fontWeight: "bold",
                      transition: "all 0.2s",
                    }}
                    type="button"
                  >
                    Bestehende Person
                  </button>
                  <button
                    onClick={() => setAssignmentMode("new")}
                    style={{
                      background: assignmentMode === "new" ? "var(--neon-cyan)" : "transparent",
                      color: assignmentMode === "new" ? "#000" : "#888",
                      border: "none",
                      padding: "6px 12px",
                      borderRadius: 4,
                      cursor: "pointer",
                      fontSize: 12,
                      fontWeight: "bold",
                      transition: "all 0.2s",
                    }}
                    type="button"
                  >
                    Neue Person anlegen
                  </button>
                </div>
              </div>

              <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
                <div style={{ flex: 1 }}>
                  {assignmentMode === "existing" ? (
                    <select
                      className="app-select"
                      value={selectedPerson}
                      onChange={(event) => setSelectedPerson(event.target.value)}
                      style={{ width: "100%", border: "1px solid #333", fontSize: 14 }}
                      disabled={isMutating}
                    >
                      <option value="">-- Person aus Liste wählen --</option>
                      {persons.map((person) => (
                        <option key={person.id} value={person.name}>
                          {person.name}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      type="text"
                      placeholder="Namen der neuen Person eingeben..."
                      value={newPersonName}
                      onChange={(event) => setNewPersonName(event.target.value)}
                      style={{
                        width: "100%",
                        background: "#141418",
                        color: "#fff",
                        border: "1px solid #333",
                        padding: "10px 12px",
                        borderRadius: 4,
                        outline: "none",
                        fontSize: 14,
                      }}
                      disabled={isMutating}
                    />
                  )}
                </div>

                <button
                  className="neon-card"
                  disabled={
                    isMutating ||
                    (assignmentMode === "new" ? !newPersonName.trim() : !selectedPerson)
                  }
                  style={{
                    borderColor:
                      assignmentMode === "new"
                        ? newPersonName.trim()
                          ? "var(--neon-cyan)"
                          : "#333"
                        : selectedPerson
                          ? "var(--neon-cyan)"
                          : "#333",
                    color:
                      assignmentMode === "new"
                        ? newPersonName.trim()
                          ? "#fff"
                          : "#555"
                        : selectedPerson
                          ? "#fff"
                          : "#555",
                    opacity:
                      isMutating ||
                      (assignmentMode === "new" ? !newPersonName.trim() : !selectedPerson)
                        ? 0.5
                        : 1,
                    cursor: isMutating ? "wait" : "pointer",
                    padding: "10px 24px",
                    fontWeight: "bold",
                    height: 42,
                    boxShadow:
                      assignmentMode === "new"
                        ? newPersonName.trim()
                          ? "0 0 10px rgba(0, 229, 255, 0.2)"
                          : "none"
                        : selectedPerson
                          ? "0 0 10px rgba(0, 229, 255, 0.2)"
                          : "none",
                  }}
                  onClick={handleAssign}
                  type="button"
                >
                  Bestätigen
                </button>
              </div>
            </div>

            <h3 style={{ borderBottom: "1px solid #222", paddingBottom: 8, color: "#fff" }}>
              Erkannte Gesichter ({currentClusterSummary.face_count})
            </h3>

            {isDetailsLoading && !currentCluster ? (
              <div style={{ opacity: 0.5, paddingTop: 20, textAlign: "center" }}>
                Clusterdetails werden geladen...
              </div>
            ) : (
              <ClusterFacesGrid faces={currentFaces} onRemoveFace={handleRemoveFace} />
            )}
          </>
        ) : (
          <div style={{ opacity: 0.5, paddingTop: 40, textAlign: "center" }}>
            Kein Cluster ausgewählt.
          </div>
        )}
      </div>
    </div>
  );
};

export default ClusterPage;
