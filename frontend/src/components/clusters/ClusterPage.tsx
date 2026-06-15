import React, { useEffect, useMemo, useState } from "react";
import {
  fetchClusters,
  removeFaceFromCluster,
  dissolveCluster,
  assignClusterToPerson,
  listPersons,
} from "../../utils/api";
import ClusterList from "./ClusterList";
import ClusterFacesGrid from "./ClusterFacesGrid";

const UNKNOWN_PERSON_LABEL = "Unbekannt";

const sortClustersByAssignedPerson = (items: any[]) => {
  return [...items].sort((a, b) => {
    const aFaceCount = Array.isArray(a.faces) ? a.faces.length : 0;
    const bFaceCount = Array.isArray(b.faces) ? b.faces.length : 0;
    if (aFaceCount !== bFaceCount) {
      return bFaceCount - aFaceCount;
    }

    const aName = (a.person_name || "").trim();
    const bName = (b.person_name || "").trim();
    const aAssigned = aName.length > 0;
    const bAssigned = bName.length > 0;

    if (aAssigned !== bAssigned) {
      return aAssigned ? -1 : 1;
    }

    const byName = (aAssigned ? aName : UNKNOWN_PERSON_LABEL).localeCompare(
      bAssigned ? bName : UNKNOWN_PERSON_LABEL,
      "de",
      { sensitivity: "base" }
    );
    if (byName !== 0) {
      return byName;
    }

    return a.cluster_id - b.cluster_id;
  });
};

const ClusterPage: React.FC = () => {
  const [clusters, setClusters] = useState<any[]>([]);
  const [selectedClusterId, setSelectedClusterId] = useState<number | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const [persons, setPersons] = useState<any[]>([]);
  const [selectedPerson, setSelectedPerson] = useState<string>("");
  const [newPersonName, setNewPersonName] = useState("");
  
  // 🔥 Neuer Zustand für die aufgeräumte Zuweisung: "existing" oder "new"
  const [assignmentMode, setAssignmentMode] = useState<"existing" | "new">("existing");

  const sortedClusters = useMemo(
    () => sortClustersByAssignedPerson(clusters),
    [clusters]
  );

  useEffect(() => {
    let isMounted = true;

    const loadInitialData = async () => {
      try {
        const data = await fetchClusters();
        if (!isMounted) return;
        setClusters(data);

        if (data.length > 0 && selectedClusterId === null) {
          setSelectedClusterId(sortClustersByAssignedPerson(data)[0].cluster_id);
        }
      } catch (err) {
        console.error("Fehler beim Laden der Cluster:", err);
      } finally {
        if (isMounted) setIsLoading(false);
      }
    };

    loadInitialData();

    const interval = setInterval(async () => {
      const data = await fetchClusters();
      if (!isMounted) return;
      setClusters(data);
      const sortedData = sortClustersByAssignedPerson(data);

      if (data.length > 0) {
        if (selectedClusterId !== null) {
          const stillExists = data.some((c) => c.cluster_id === selectedClusterId);
          if (!stillExists) {
            setSelectedClusterId(sortedData[0].cluster_id);
          }
        } else {
          setSelectedClusterId(sortedData[0].cluster_id);
        }
      } else {
        setSelectedClusterId(null);
      }
    }, 10000);

    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, [selectedClusterId]);

  useEffect(() => {
    listPersons().then(setPersons);
  }, []);

  const currentCluster = clusters.find((c) => c.cluster_id === selectedClusterId);

  const determineNextClusterIdOnSameSpot = (currentId: number): number | null => {
    const currentIndex = sortedClusters.findIndex((c) => c.cluster_id === currentId);
    if (currentIndex === -1 || sortedClusters.length <= 1) return null;
    if (currentIndex === sortedClusters.length - 1) {
      return sortedClusters[currentIndex - 1].cluster_id;
    }
    return sortedClusters[currentIndex + 1].cluster_id;
  };

  const handleRemoveFace = async (faceId: number) => {
    await removeFaceFromCluster(selectedClusterId!, faceId);
    setClusters(prev => prev.map(c => {
      if (c.cluster_id === selectedClusterId) {
        return { ...c, faces: c.faces.filter((f: any) => f.id !== faceId) };
      }
      return c;
    }));
  };

  const handleDissolve = async () => {
    if (!selectedClusterId) return;
    const nextClusterId = determineNextClusterIdOnSameSpot(selectedClusterId);
    setClusters((prev) => prev.filter((c) => c.cluster_id !== selectedClusterId));
    setSelectedClusterId(nextClusterId);

    try {
      await dissolveCluster(selectedClusterId);
    } catch (error) {
      console.error("Fehler beim Auflösen:", error);
    }
  };

  const handleAssign = async () => {
    if (!selectedClusterId) return;
    
    // Je nach Modus den Namen wählen
    const name = assignmentMode === "new" ? newPersonName.trim() : selectedPerson;
    if (!name) return;

    setClusters((prev) =>
      prev.map((c) => {
        if (c.cluster_id === selectedClusterId) {
          return { ...c, person_name: name };
        }
        return c;
      })
    );

    setNewPersonName("");
    setSelectedPerson("");

    try {
      await assignClusterToPerson(selectedClusterId, name);
      listPersons().then(setPersons);
    } catch (error) {
      console.error("Fehler bei der Zuweisung:", error);
    }
  };

  return (
    <div style={{ display: "flex", gap: 24, height: "100%" }}>
      {/* Linke Seite: Cluster-Auswahlliste */}
      <div style={{ width: 250, borderRight: "1px solid #222", paddingRight: 16, overflowY: "auto" }}>
        <h3 style={{ marginTop: 0, color: "var(--neon-magenta)" }}>Menschliche Cluster</h3>
        <ClusterList
          clusters={sortedClusters}
          selected={selectedClusterId}
          onSelect={setSelectedClusterId}
          isLoading={isLoading}
        />
      </div>

      {/* Rechte Seite: Details des ausgewählten Clusters */}
      <div style={{ flex: 1, overflowY: "auto" }}>
        {isLoading ? (
          <div style={{ opacity: 0.5, paddingTop: 40, textAlign: "center" }}>Geladen wird...</div>
        ) : currentCluster ? (
          <>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 24 }}>
              <div>
                <h2 style={{ margin: 0 }}>Cluster {currentCluster.cluster_id}</h2>
                <p style={{ opacity: 0.5, margin: "4px 0 0 0" }}>
                  Zugeordnet zu: <strong style={{ color: "var(--neon-cyan)" }}>{currentCluster.person_name || "Niemand (Unbekannt)"}</strong>
                </p>
              </div>

              <div style={{ display: "flex", gap: 12 }}>
                <button
                  className="neon-card"
                  style={{ borderColor: "#ff0055", color: "#ff0055", cursor: "pointer", fontWeight: "bold" }}
                  onClick={handleDissolve}
                >
                  💥 Cluster auflösen
                </button>
              </div>
            </div>

            {/* 🔥 NEUE, AUFGERÄUMTE ZUWEISUNGS-KOMPONENTE */}
            <div className="neon-card" style={{ marginBottom: 24, padding: 20, background: "#0b0b0e", borderColor: "#222" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16, borderBottom: "1px solid #1a1a20", paddingBottom: 10 }}>
                <h4 style={{ margin: 0, fontSize: 14, letterSpacing: "0.5px", textTransform: "uppercase", color: "#aaa" }}>
                  Identität zuweisen
                </h4>
                
                {/* Cyberpunk Modus-Umschalter */}
                <div style={{ display: "flex", background: "#050507", padding: 3, borderRadius: 6, border: "1px solid #222" }}>
                  <button
                    onClick={() => setAssignmentMode("existing")}
                    style={{
                      background: assignmentMode === "existing" ? "var(--neon-cyan)" : "transparent",
                      color: assignmentMode === "existing" ? "#000" : "#888",
                      border: "none", padding: "6px 12px", borderRadius: 4, cursor: "pointer", fontSize: 12, fontWeight: "bold", transition: "all 0.2s"
                    }}
                  >
                    Bestehende Person
                  </button>
                  <button
                    onClick={() => setAssignmentMode("new")}
                    style={{
                      background: assignmentMode === "new" ? "var(--neon-cyan)" : "transparent",
                      color: assignmentMode === "new" ? "#000" : "#888",
                      border: "none", padding: "6px 12px", borderRadius: 4, cursor: "pointer", fontSize: 12, fontWeight: "bold", transition: "all 0.2s"
                    }}
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
                      onChange={(e) => setSelectedPerson(e.target.value)}
                      style={{ width: "100%", border: "1px solid #333", fontSize: 14 }}
                    >
                      <option value="">-- Person aus Liste wählen --</option>
                      {persons.map((p) => (
                        <option key={p.id} value={p.name}>{p.name}</option>
                      ))}
                    </select>
                  ) : (
                    <input
                      type="text"
                      placeholder="Namen der neuen Person eingeben..."
                      value={newPersonName}
                      onChange={(e) => setNewPersonName(e.target.value)}
                      style={{ width: "100%", background: "#141418", color: "#fff", border: "1px solid #333", padding: "10px 12px", borderRadius: 4, outline: "none", fontSize: 14 }}
                    />
                  )}
                </div>

                <button
                  className="neon-card"
                  disabled={assignmentMode === "new" ? !newPersonName.trim() : !selectedPerson}
                  style={{
                    borderColor: (assignmentMode === "new" ? newPersonName.trim() : selectedPerson) ? "var(--neon-cyan)" : "#333",
                    color: (assignmentMode === "new" ? newPersonName.trim() : selectedPerson) ? "#fff" : "#555",
                    opacity: (assignmentMode === "new" ? newPersonName.trim() : selectedPerson) ? 1 : 0.5,
                    cursor: (assignmentMode === "new" ? newPersonName.trim() : selectedPerson) ? "pointer" : "not-allowed",
                    padding: "10px 24px",
                    fontWeight: "bold",
                    height: 42,
                    boxShadow: (assignmentMode === "new" ? newPersonName.trim() : selectedPerson) ? "0 0 10px rgba(0, 229, 255, 0.2)" : "none"
                  }}
                  onClick={handleAssign}
                >
                  ✓ Bestätigen
                </button>
              </div>
            </div>

            <h3 style={{ borderBottom: "1px solid #222", paddingBottom: 8, color: "#fff" }}>Erkannte Gesichter ({currentCluster.faces.length})</h3>
            
            <ClusterFacesGrid 
              faces={currentCluster.faces} 
              onRemoveFace={handleRemoveFace} 
            />
          </>
        ) : (
          <div style={{ opacity: 0.5, paddingTop: 40, textAlign: "center" }}>Kein Cluster ausgewählt.</div>
        )}
      </div>
    </div>
  );
};

export default ClusterPage;
