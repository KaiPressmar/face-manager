import React, { useState } from "react";
import { assignClusterToPerson } from "../../utils/api";

const AssignPersonModal = ({ clusterId, persons, onClose }) => {
  const [newName, setNewName] = useState("");
  const [selected, setSelected] = useState("");

  const submit = async () => {
    const name = newName.trim() || selected;
    if (!name) return;

    await assignClusterToPerson(clusterId, name);
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
          background: "var(--surface-1)",
          color: "var(--text)",
          padding: 24,
          borderRadius: 6,
          width: 360,
          border: "1px solid var(--border-strong)",
        }}
      >
        <h3 style={{ marginTop: 0 }}>Person zuweisen</h3>

        <div style={{ marginBottom: 16 }}>
          <div style={{ marginBottom: 6 }}>Bestehende Person:</div>
          <select
            className="app-select"
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
            style={{
              width: "100%",
              padding: 8,
              border: "1px solid var(--border-solid)",
            }}
          >
            <option value="">– auswählen –</option>
            {persons.map((p) => (
              <option key={p.id} value={p.name}>
                {p.name}
              </option>
            ))}
          </select>
        </div>

        <div style={{ marginBottom: 16 }}>
          <div style={{ marginBottom: 6 }}>Neue Person:</div>
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="Name eingeben"
            style={{
              width: "100%",
              padding: 8,
              background: "var(--panel-solid)",
              color: "var(--text)",
              border: "1px solid var(--border-solid)",
            }}
          />
        </div>

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 12 }}>
          <button
            onClick={() => onClose(false)}
            style={{
              padding: "6px 12px",
              background: "var(--surface-raise)",
              color: "var(--text)",
              border: "1px solid var(--border-solid)",
            }}
          >
            Abbrechen
          </button>

          <button
            onClick={submit}
            style={{
              padding: "6px 12px",
              background: "var(--neon-cyan)",
              color: "var(--on-accent)",
              border: "none",
              fontWeight: "bold",
            }}
          >
            Speichern
          </button>
        </div>
      </div>
    </div>
  );
};

export default AssignPersonModal;
