import React, { useEffect, useState } from "react";
import { fetchProcessStatus } from "../../utils/api";

const ImportProgress = () => {
  const [state, setState] = useState<any>(null);

  useEffect(() => {
    const interval = setInterval(async () => {
      const s = await fetchProcessStatus();
      setState(s);
    }, 5000);

    return () => clearInterval(interval);
  }, []);

  if (!state || state.status === "idle" || state.status === "done") {
    return null;
  }

  const imgProgress =
    state.total_images > 0
      ? (state.processed_images / state.total_images) * 100
      : 0;

  const faceProgress =
    state.total_faces > 0
      ? (state.processed_faces / state.total_faces) * 100
      : 0;

  return (
    <div
      style={{
        position: "fixed",
        top: 20,
        right: 20,
        width: 300,
        padding: 16,
        background: "#1a1a1f",
        borderRadius: 8,
        boxShadow: "0 0 12px rgba(0,0,0,0.4)",
        zIndex: 9999,
        color: "white",
      }}
    >
      <h4 style={{ margin: 0, marginBottom: 8 }}>Import läuft…</h4>

      <div style={{ fontSize: 13, opacity: 0.8, marginBottom: 8 }}>
        Bilder: {state.processed_images} / {state.total_images}
      </div>

      <div
        style={{
          height: 6,
          background: "#333",
          borderRadius: 4,
          overflow: "hidden",
          marginBottom: 12,
        }}
      >
        <div
          style={{
            width: `${imgProgress}%`,
            height: "100%",
            background: "#4a90e2",
          }}
        />
      </div>

      <div style={{ fontSize: 13, opacity: 0.8, marginBottom: 8 }}>
        Gesichter: {state.processed_faces} / {state.total_faces}
      </div>

      <div
        style={{
          height: 6,
          background: "#333",
          borderRadius: 4,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            width: `${faceProgress}%`,
            height: "100%",
            background: "#e24a6a",
          }}
        />
      </div>
    </div>
  );
};

export default ImportProgress;
