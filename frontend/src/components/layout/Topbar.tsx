import React, { useEffect, useState } from "react";
import { fetchRuntimeInfo, RuntimeInfo } from "../../utils/api";

const Topbar = () => {
  const [runtime, setRuntime] = useState<RuntimeInfo | null>(null);
  const [runtimeUnavailable, setRuntimeUnavailable] = useState(false);

  useEffect(() => {
    let cancelled = false;

    fetchRuntimeInfo()
      .then((info) => {
        if (!cancelled) setRuntime(info);
      })
      .catch(() => {
        if (!cancelled) setRuntimeUnavailable(true);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const mode = runtime?.compute_mode;
  const modeLabel = mode
    ? `${mode.toUpperCase()} mode`
    : runtimeUnavailable
      ? "Mode unavailable"
      : "Detecting mode";
  const modeTitle = runtime
    ? `ONNX Runtime provider: ${runtime.execution_provider}`
    : runtimeUnavailable
      ? "Could not retrieve the backend compute mode"
      : "Detecting backend compute mode";

  return (
    <header
      style={{
        height: 64,
        background: "#141418",
        borderBottom: "1px solid #1f1f22",
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "0 24px",
      }}
    >
      <span style={{ fontSize: 20 }}>Face Manager</span>
      <span className="app-version">v{__APP_VERSION__}</span>
      <span
        className={`compute-mode-badge compute-mode-badge--${mode ?? "unknown"}`}
        title={modeTitle}
      >
        <span className="compute-mode-badge__indicator" aria-hidden="true" />
        {modeLabel}
      </span>
    </header>
  );
};

export default Topbar;
