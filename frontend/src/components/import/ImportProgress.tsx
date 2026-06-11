import React, { useEffect, useState } from "react";
import { fetchProcessStatus } from "../../utils/api";

const IDLE_POLL_INTERVAL_MS = 5000;
const ACTIVE_POLL_INTERVAL_MS = 1000;

const ImportProgress = () => {
  const [state, setState] = useState<any>(null);

  useEffect(() => {
    let timeoutId: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;

    const scheduleNext = (status?: string) => {
      const pollDelay =
        status === "running" ? ACTIVE_POLL_INTERVAL_MS : IDLE_POLL_INTERVAL_MS;
      timeoutId = setTimeout(poll, pollDelay);
    };

    const poll = async () => {
      try {
        const s = await fetchProcessStatus();
        if (cancelled) {
          return;
        }
        setState(s);
        scheduleNext(s?.status);
      } catch {
        if (!cancelled) {
          scheduleNext();
        }
      }
    };

    poll();

    return () => {
      cancelled = true;
      if (timeoutId) {
        clearTimeout(timeoutId);
      }
    };
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
        width: "100%",
        padding: 16,
        background: "#1a1a1f",
        borderRadius: 8,
        boxShadow: "0 0 12px rgba(0,0,0,0.4)",
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
