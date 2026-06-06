import React, { useEffect, useState } from "react";
import { fetchImages } from "../../utils/api";
import PersonFilter from "./PersonFilter";
import ImageGrid from "./ImageGrid";
import FolderPickerModal from "../shared/FolderPickerModal";

const PeoplePage = () => {
  const [images, setImages] = useState([]);
  const [selectedPersons, setSelectedPersons] = useState<string[]>([]);
  const [showPicker, setShowPicker] = useState(false);
  // Neuer Zustand, um den initialen Ladevorgang abzufangen
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    // Initialer Ladevorgang
    fetchImages()
      .then((data) => {
        setImages(data);
      })
      .finally(() => {
        setIsLoading(false); // Ladezustand beenden, sobald Daten (oder Fehler) da sind
      });

    // Aktualisierung alle 15 Sekunden statt jede Sekunde (Performance-Rettung!)
    const interval = setInterval(() => {
      fetchImages().then(setImages);
    }, 15000);

    return () => clearInterval(interval);
  }, []);

  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <PersonFilter
          images={images}
          selected={selectedPersons}
          onChange={setSelectedPersons}
        />

        <button
          className="neon-card"
          style={{
            padding: "10px 16px",
            cursor: "pointer",
            borderColor: "var(--neon-cyan)",
            fontWeight: "bold",
          }}
          onClick={() => setShowPicker(true)}
        >
          📁 Ordner hinzufügen
        </button>
      </div>

      <ImageGrid
        images={images}
        selectedPersons={selectedPersons}
        isLoading={isLoading} // Prop an das Grid weiterreichen
      />

      {showPicker && (
        <FolderPickerModal onClose={() => setShowPicker(false)} />
      )}
    </>
  );
};

export default PeoplePage;