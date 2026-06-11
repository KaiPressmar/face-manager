import React from "react";

const Topbar = () => {
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
    </header>
  );
};

export default Topbar;
