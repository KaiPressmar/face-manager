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
          : "Der Ordner konnte nicht hinzugefügt werden."
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
          background: "var(--surface-1)",
          color: "var(--text)",
          padding: 24,
          borderRadius: 6,
          width: 520,
          border: "1px solid var(--border-strong)",
        }}
      >
        <h3 style={{ marginTop: 0 }}>Bilderordner hinzufügen</h3>

        <p style={{ opacity: 0.75, lineHeight: 1.5 }}>
          Wähle den Ordner aus, in dem deine Bilder liegen. Falls sich der
          Ordnerdialog nicht öffnet, kannst du den Speicherort darunter
          manuell einfügen.
        </p>

        <div style={{ display: "flex", gap: 12, marginBottom: 16 }}>
          <button
            onClick={browse}
            disabled={isBrowsing || isStarting}
            style={{
              padding: "10px 14px",
              background: "var(--neon-cyan)",
              color: "var(--on-accent)",
              border: "none",
              fontWeight: "bold",
              cursor: isBrowsing || isStarting ? "default" : "pointer",
              opacity: isBrowsing || isStarting ? 0.65 : 1,
            }}
          >
            {isBrowsing ? "Ordnerauswahl wird geöffnet…" : "Ordner auswählen"}
          </button>
          <div
            style={{
              flex: 1,
              padding: 12,
              background: "var(--surface-raise)",
              border: "1px solid var(--border-solid)",
              color: folderPath ? "var(--accent-text)" : "var(--text-faint)",
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
          Speicherort manuell einfügen
        </label>
        <input
          type="text"
          value={folderPath}
          onChange={(event) => setFolderPath(event.target.value)}
          placeholder="C:\\Users\\Name\\Pictures oder /home/name/photos"
          disabled={isStarting}
          style={{
            width: "100%",
            padding: 12,
            background: "var(--panel-solid)",
            border: "1px solid var(--border-solid)",
            color: "var(--text)",
            marginBottom: 16,
          }}
        />

        {isStarting && (
          <div style={{ color: "var(--accent-text)", marginBottom: 12 }}>
            Ordner wird hinzugefügt…
          </div>
        )}

        {error && (
          <div style={{ color: "var(--danger)", marginBottom: 12 }}>{error}</div>
        )}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 12 }}>
          <button
            onClick={() => onClose(false)}
            disabled={isStarting || isBrowsing}
            style={{
              padding: "6px 12px",
              background: "var(--surface-raise)",
              color: "var(--text)",
              border: "1px solid var(--border-solid)",
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
              background: folderPath.trim() ? "var(--neon-cyan)" : "var(--surface-raise)",
              color: folderPath.trim() ? "var(--on-accent)" : "var(--text-faint)",
              border: "none",
              fontWeight: "bold",
              cursor:
                folderPath.trim() && !isStarting && !isBrowsing
                  ? "pointer"
                  : "not-allowed",
              opacity: isStarting || isBrowsing ? 0.5 : 1,
            }}
          >
            {isStarting ? "Wird hinzugefügt…" : "Bilder hinzufügen"}
          </button>
        </div>
      </div>
    </div>
  );
};

export default FolderPickerModal;
