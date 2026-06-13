import React, { useState } from "react";
import { processFolder, selectImportFolder } from "../../utils/api";

interface FolderPickerModalProps {
  onClose: (started?: boolean) => void;
}

const FolderPickerModal: React.FC<FolderPickerModalProps> = ({ onClose }) => {
  const [folderPath, setFolderPath] = useState("");
  const [isBrowsing, setIsBrowsing] = useState(false);
  const [isStarting, setIsStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const browse = async () => {
    setIsBrowsing(true);
    setError(null);
    try {
      const selectedPath = await selectImportFolder();
      if (selectedPath) {
        setFolderPath(selectedPath);
      }
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Der Ordnerdialog konnte nicht geöffnet werden."
      );
    } finally {
      setIsBrowsing(false);
    }
  };

  const submit = async () => {
    if (!folderPath.trim()) return;

    setIsStarting(true);
    setError(null);
    try {
      await processFolder(folderPath);
      onClose(true);
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Der Import konnte nicht eingereiht werden."
      );
      setIsStarting(false);
    }
  };

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.6)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 9999,
      }}
    >
      <div
        style={{
          background: "#141418",
          padding: 24,
          borderRadius: 6,
          width: 520,
          border: "1px solid #2a2a30",
        }}
      >
        <h3 style={{ marginTop: 0 }}>Ordner importieren</h3>

        <p style={{ opacity: 0.75, lineHeight: 1.5 }}>
          Wähle am besten direkt einen Ordner im Dateisystem aus. Wenn der
          Dialog auf deinem System nicht verfügbar ist, kannst du den Pfad auch
          manuell einfügen. Windows-Pfade werden im Backend automatisch korrekt
          erkannt.
        </p>

        <div style={{ display: "flex", gap: 12, marginBottom: 16 }}>
          <button
            onClick={browse}
            disabled={isBrowsing || isStarting}
            style={{
              padding: "10px 14px",
              background: "#00e5ff",
              color: "#04131a",
              border: "none",
              fontWeight: "bold",
              cursor: isBrowsing || isStarting ? "default" : "pointer",
              opacity: isBrowsing || isStarting ? 0.65 : 1,
            }}
          >
            {isBrowsing ? "Öffne Dialog…" : "Ordner auswählen"}
          </button>
          <div
            style={{
              flex: 1,
              padding: 12,
              background: "#1f1f22",
              border: "1px solid #333",
              color: folderPath ? "#d9f7ff" : "#777",
              borderRadius: 4,
              fontFamily: "monospace",
              fontSize: 13,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
            title={folderPath}
          >
            {folderPath || "Noch kein Ordner ausgewählt"}
          </div>
        </div>

        <label
          style={{
            display: "block",
            marginBottom: 8,
            opacity: 0.75,
          }}
        >
          Alternativ Pfad manuell einfügen
        </label>
        <input
          type="text"
          value={folderPath}
          onChange={(event) => setFolderPath(event.target.value)}
          placeholder="C:\\Users\\Kai\\Pictures oder /home/kai/photos"
          disabled={isStarting}
          style={{
            width: "100%",
            padding: 12,
            background: "#1f1f22",
            border: "1px solid #333",
            color: "white",
            marginBottom: 16,
          }}
        />

        {isStarting && (
          <div style={{ color: "#00e5ff", marginBottom: 12 }}>
            Import wird gestartet…
          </div>
        )}

        {error && (
          <div style={{ color: "#ff6d89", marginBottom: 12 }}>{error}</div>
        )}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 12 }}>
          <button
            onClick={() => onClose(false)}
            disabled={isStarting || isBrowsing}
            style={{
              padding: "6px 12px",
              background: "#333",
              color: "white",
              border: "none",
              opacity: isStarting || isBrowsing ? 0.5 : 1,
            }}
          >
            Abbrechen
          </button>

          <button
            onClick={submit}
            disabled={!folderPath.trim() || isStarting || isBrowsing}
            style={{
              padding: "6px 12px",
              background: folderPath.trim() ? "#00e5ff" : "#555",
              color: "#000",
              border: "none",
              fontWeight: "bold",
              cursor:
                folderPath.trim() && !isStarting && !isBrowsing
                  ? "pointer"
                  : "not-allowed",
              opacity: isStarting || isBrowsing ? 0.5 : 1,
            }}
          >
            {isStarting ? "Starte…" : "Import starten"}
          </button>
        </div>
      </div>
    </div>
  );
};

export default FolderPickerModal;
