import React, { useState, useEffect } from "react";
import { processFolder } from "../../utils/api";
import { windowsPathToWSL } from "../../utils/windowsPathToWSL";

const FolderPickerModal = ({ onClose }) => {
  const [winPath, setWinPath] = useState("");
  const [wslPath, setWslPath] = useState("");
  const [isStarting, setIsStarting] = useState(false);

  useEffect(() => {
    setWslPath(windowsPathToWSL(winPath));
  }, [winPath]);

  const submit = async () => {
    if (!wslPath) return;

    setIsStarting(true); // UI Feedback

    await processFolder(wslPath);

    // Modal schließen → ProgressOverlay übernimmt
    onClose(true);
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
          width: 480,
          border: "1px solid #2a2a30",
        }}
      >
        <h3 style={{ marginTop: 0 }}>📁 Ordner importieren</h3>

        <p style={{ opacity: 0.7 }}>
          Füge hier den Windows‑Pfad ein, z.B.:
          <br />
          <code>D:\Bilder\Sortiert\2025\...</code>
        </p>

        <input
          type="text"
          value={winPath}
          onChange={(e) => setWinPath(e.target.value)}
          placeholder="D:\\Bilder\\Sortiert\\2025\\..."
          style={{
            width: "100%",
            padding: 12,
            background: "#1f1f22",
            border: "1px solid #333",
            color: "white",
            marginBottom: 16,
          }}
        />

        <p style={{ opacity: 0.7, marginTop: 0 }}>Erkannter WSL‑Pfad:</p>

        <div
          style={{
            width: "100%",
            padding: 12,
            background: "#1f1f22",
            border: "1px solid #333",
            color: wslPath ? "#00e5ff" : "#777",
            marginBottom: 16,
            borderRadius: 4,
            fontFamily: "monospace",
          }}
        >
          {wslPath || "Ungültiger Windows‑Pfad"}
        </div>

        {isStarting && (
          <div style={{ color: "#00e5ff", marginBottom: 12 }}>
            Import wird gestartet…
          </div>
        )}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 12 }}>
          <button
            onClick={() => onClose(false)}
            disabled={isStarting}
            style={{
              padding: "6px 12px",
              background: "#333",
              color: "white",
              border: "none",
              opacity: isStarting ? 0.5 : 1,
            }}
          >
            Abbrechen
          </button>

          <button
            onClick={submit}
            disabled={!wslPath || isStarting}
            style={{
              padding: "6px 12px",
              background: wslPath ? "#00e5ff" : "#555",
              color: "#000",
              border: "none",
              fontWeight: "bold",
              cursor: wslPath && !isStarting ? "pointer" : "not-allowed",
              opacity: isStarting ? 0.5 : 1,
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
